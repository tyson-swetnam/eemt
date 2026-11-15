#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
"""Scan a container filesystem (a tar stream, e.g. `docker export`) for credentials
and local state that must never be in an image.

    docker export <container> | secret_scan.py [--label NAME] -

Standard library only. Fails (exit status 1) on:

- credential or state files by name: .eemt-makeflow-password, *.password, .netrc,
  .git-credentials, .pypirc, .docker/config.json, .env, private SSH keys (id_rsa,
  id_dsa, id_ecdsa, id_ed25519), jobs.db, *.sqlite;
- any regular file inside an `uploads/` or `results/` directory (job data);
- a PEM private key block (`-----BEGIN ... PRIVATE KEY-----`, base64 body,
  footer) in any file of at most 1 MiB,
  unless the path is in ALLOWED_KEY_FILES (published test fixtures, with a reason).

Prints every finding and a one-line summary.
"""

from __future__ import annotations

import argparse
import re
import sys
import tarfile
from pathlib import PurePosixPath
from typing import IO

BAD_NAMES = {
    ".eemt-makeflow-password",
    ".netrc",
    "_netrc",
    ".git-credentials",
    ".pypirc",
    ".env",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "jobs.db",
}
BAD_SUFFIXES = (".password", ".sqlite", ".sqlite3")
STATE_DIRS = {"uploads", "results"}
# A whole PEM block (header, base64 body, matching footer): parsers and docs that only
# mention the header line (libssh2, OpenSSH, shared-mime-info) do not match.
PRIVATE_KEY = re.compile(
    rb"-----BEGIN ((?:[A-Z0-9]+ )*)PRIVATE KEY-----\r?\n"
    rb"(?:[A-Za-z-]+: [^\n]*\n)*\r?\n?"
    rb"[A-Za-z0-9+/=\r\n]{64,}"
    rb"-----END \1PRIVATE KEY-----"
)
MAX_CONTENT_SCAN = 1 << 20

# Files that contain a private key on purpose, with the reason. Keep this empty unless
# a scan finds a published test fixture inside a third-party package.
ALLOWED_KEY_FILES: dict[str, str] = {}


def classify(path: str) -> str | None:
    """Reason why a regular file at `path` must not be in an image (None if fine)."""
    p = PurePosixPath("/" + path.lstrip("./"))
    name = p.name
    if name in BAD_NAMES:
        return f"credential/state file name {name!r}"
    if name.endswith(BAD_SUFFIXES):
        return f"credential/state file suffix ({name})"
    if p.parent.name == ".docker" and name == "config.json":
        return "Docker client credentials (.docker/config.json)"
    if STATE_DIRS & set(p.parent.parts):
        return "file inside a job-data directory (uploads/ or results/)"
    return None


def scan(stream: IO[bytes]) -> tuple[list[str], int]:
    findings: list[str] = []
    files = 0
    with tarfile.open(fileobj=stream, mode="r|*") as tar:
        for member in tar:
            if not member.isfile():
                continue
            files += 1
            path = "/" + member.name.lstrip("./")
            reason = classify(member.name)
            if reason:
                findings.append(f"{path}: {reason}")
            if member.size <= MAX_CONTENT_SCAN:
                fh = tar.extractfile(member)
                data = fh.read() if fh else b""
                if PRIVATE_KEY.search(data) and path not in ALLOWED_KEY_FILES:
                    findings.append(f"{path}: contains a PEM private key")
    return findings, files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("tar", help="'-' for standard input")
    ap.add_argument("--label", default="image")
    args = ap.parse_args(argv)
    if args.tar == "-":
        findings, files = scan(sys.stdin.buffer)
    else:
        with open(args.tar, "rb") as fh:
            findings, files = scan(fh)
    for f in findings:
        print(f"FAIL  {args.label}: {f}")
    print(f"secret-scan {args.label}: {files} files, {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
