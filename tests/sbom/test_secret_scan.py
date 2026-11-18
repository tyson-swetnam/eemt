# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
"""Tests for scripts/sbom/secret_scan.py: path normalisation and detection."""

from __future__ import annotations

import io
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "sbom"))

import secret_scan  # noqa: E402


@pytest.mark.parametrize(
    "member_name",
    [
        # Root-level dotfiles: the leading dot is part of the name and must survive
        # normalisation (a character-set strip of "./" used to remove it).
        ".netrc",
        "./.netrc",
        "/.netrc",
        ".env",
        "./.env",
        ".git-credentials",
        ".pypirc",
        ".eemt-makeflow-password",
        "./.eemt-makeflow-password",
        "..netrc/.netrc",
        # Nested credential files.
        "root/.netrc",
        "./home/eemt/.netrc",
        "opt/eemt/sol/.eemt-makeflow-password",
        "home/eemt/.ssh/id_ed25519",
        "app/jobs.db",
        "app/web.sqlite",
        "app/api.password",
    ],
)
def test_credential_names_are_flagged(member_name: str) -> None:
    assert secret_scan.classify(member_name) is not None


@pytest.mark.parametrize(
    "member_name",
    [".docker/config.json", "./.docker/config.json", "root/.docker/config.json"],
)
def test_docker_client_config_is_flagged(member_name: str) -> None:
    reason = secret_scan.classify(member_name)
    assert reason is not None and "docker" in reason.lower()


@pytest.mark.parametrize(
    "member_name",
    ["app/uploads/dem.tif", "./data/results/x/out.tif", "uploads/a.tif"],
)
def test_job_data_directories_are_flagged(member_name: str) -> None:
    assert secret_scan.classify(member_name) is not None


@pytest.mark.parametrize(
    "member_name",
    [
        "env",  # not ".env"
        "netrc",
        "docker/config.json",  # not ".docker"
        "opt/eemt/env/lib/python3.14/site-packages/dotenv/main.py",
        "usr/share/doc/netrc.txt",
        "opt/eemt/env/conda-meta/numpy-2.5.4.json",
        "app/templates/upload.html",
    ],
)
def test_ordinary_files_are_not_flagged(member_name: str) -> None:
    assert secret_scan.classify(member_name) is None


def test_normalise_keeps_leading_dots() -> None:
    assert secret_scan.normalise(".netrc") == "/.netrc"
    assert secret_scan.normalise("./.netrc") == "/.netrc"
    assert secret_scan.normalise("././.env") == "/.env"
    assert secret_scan.normalise("/a/./b/../.env") == "/a/.env"
    assert secret_scan.normalise("..") == "/"


def _tar(entries: dict[str, bytes]) -> io.BytesIO:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf


def test_scan_reports_root_dotfiles_with_their_real_path() -> None:
    findings, files = secret_scan.scan(
        _tar({".netrc": b"machine x", "./.env": b"A=1", "etc/hosts": b"127.0.0.1"})
    )
    assert files == 3
    assert any(f.startswith("/.netrc:") for f in findings), findings
    assert any(f.startswith("/.env:") for f in findings), findings
    assert not any(f.startswith("/etc/hosts") for f in findings)


def test_scan_finds_private_key_content() -> None:
    body = b"A" * 80 + b"\n"
    pem = (
        b"-----BEGIN OPENSSH PRIVATE KEY-----\n"
        + body
        + b"-----END OPENSSH PRIVATE KEY-----\n"
    )
    findings, _ = secret_scan.scan(_tar({"opt/app/key.txt": pem}))
    assert findings == ["/opt/app/key.txt: contains a PEM private key"]
