# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
"""Shared helpers for the SBOM scripts in scripts/sbom/ (standard library only)."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import sys
import tomllib
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Fixed UUID namespace for serial numbers and document namespaces (uuid5 of the
# canonical content). Never change it: every committed serial number depends on it.
SBOM_UUID_NAMESPACE = uuid.UUID("5b0f1a52-3c61-5b5e-9a43-2f6c0e7e3a11")

CDX_SPEC = "1.7"
SPDX_SPEC = "SPDX-2.3"

Json = Any


@dataclass(frozen=True)
class Supplier:
    """An organisation that distributes a component (NTIA "supplier")."""

    name: str
    url: str = ""

    def cdx(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name}
        if self.url:
            out["url"] = [self.url]
        return out

    def spdx(self) -> str:
        return f"Organization: {self.name}"


# Supplier policy: the supplier is the registry or distribution that ships the
# component, never the EEMT project (unless the component is the project's own).
CONDA_FORGE = Supplier("conda-forge", "https://conda-forge.org")
PYPI = Supplier("Python Package Index (PyPI)", "https://pypi.org")
CRATES_IO = Supplier("crates.io", "https://crates.io")
UBUNTU = Supplier("Ubuntu (Canonical Ltd.)", "https://ubuntu.com")
DEBIAN = Supplier("Debian", "https://www.debian.org")
OS_SUPPLIERS = {"ubuntu": UBUNTU, "debian": DEBIAN}


@dataclass(frozen=True)
class PathSupplier:
    """Supplier for components found only by file location (binaries, Go modules)."""

    prefix: str
    supplier: Supplier


@dataclass
class Target:
    """One SBOM target from scripts/sbom/targets.toml."""

    name: str
    kind: str
    root_type: str
    root_license: str
    description: str
    root_name: str = ""
    parent: str = ""
    image: str = ""
    build: str = ""
    dockerfile: str = ""
    inputs: list[str] = field(default_factory=list)
    path_suppliers: list[PathSupplier] = field(default_factory=list)


@dataclass
class Config:
    project: str
    project_supplier: Supplier
    namespace: str
    targets: dict[str, Target]
    locks_without_sbom: dict[str, str] = field(default_factory=dict)


def load_config(path: Path) -> Config:
    """Read scripts/sbom/targets.toml."""
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    targets: dict[str, Target] = {}
    for t in raw["target"]:
        rules = [
            PathSupplier(r["prefix"], Supplier(r["name"], r.get("url", "")))
            for r in t.get("path_supplier", [])
        ]
        targets[t["name"]] = Target(
            name=t["name"],
            kind=t["kind"],
            root_type=t["root_type"],
            root_license=t["root_license"],
            description=t["description"],
            root_name=t.get("root_name", t["name"]),
            parent=t.get("parent", ""),
            image=t.get("image", ""),
            build=t.get("build", ""),
            dockerfile=t.get("dockerfile", ""),
            inputs=list(t.get("inputs", [])),
            path_suppliers=rules,
        )
    sup = raw["supplier"]
    return Config(
        project=raw["project"],
        project_supplier=Supplier(sup["name"], sup.get("url", "")),
        namespace=raw["namespace"].rstrip("/"),
        targets=targets,
        locks_without_sbom={
            e["path"]: e["reason"] for e in raw.get("lock_without_sbom", [])
        },
    )


def read_epoch(path: Path) -> int:
    """sbom/SOURCE_DATE_EPOCH: one integer (seconds since 1970-01-01 UTC)."""
    text = path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+", text):
        raise ValueError(f"{path}: not an integer epoch: {text!r}")
    return int(text)


def iso_utc(epoch: int) -> str:
    return _dt.datetime.fromtimestamp(epoch, _dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_hex(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()  # noqa: S324 - SPDX 2.3 requires SHA1


def _sort_key(value: Json) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def canon(value: Json) -> Json:
    """Sort every list by its canonical JSON (dict keys are sorted when dumped).

    None of the arrays in CycloneDX or SPDX JSON that these scripts write carries
    meaning in its order, so sorting them all is safe and makes output stable.
    """
    if isinstance(value, dict):
        return {k: canon(v) for k, v in value.items()}
    if isinstance(value, list):
        return sorted((canon(v) for v in value), key=_sort_key)
    return value


def dumps(doc: Json) -> str:
    """Canonical JSON: sorted keys and arrays, two-space indent, final newline."""
    return json.dumps(canon(doc), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def content_uuid(doc: Json, drop: tuple[str, ...]) -> uuid.UUID:
    """uuid5 of the canonical document without the given top-level keys."""
    body = {k: v for k, v in doc.items() if k not in drop}
    return uuid.uuid5(SBOM_UUID_NAMESPACE, sha256_hex(dumps(body)))


# --- package URLs -------------------------------------------------------------


@dataclass(frozen=True)
class Purl:
    type: str
    namespace: str
    name: str
    version: str
    qualifiers: tuple[tuple[str, str], ...]

    def qualifier(self, key: str) -> str:
        return dict(self.qualifiers).get(key, "")


def parse_purl(purl: str) -> Purl | None:
    """Minimal purl parser (pkg:conda/deb/pypi/cargo/golang/generic/github)."""
    if not purl.startswith("pkg:"):
        return None
    rest = purl[4:].split("#", 1)[0]
    rest, _, query = rest.partition("?")
    head, _, version = rest.partition("@")
    parts = head.split("/")
    ptype = parts[0].lower()
    name = urllib.parse.unquote(parts[-1]) if len(parts) > 1 else ""
    namespace = "/".join(urllib.parse.unquote(p) for p in parts[1:-1])
    quals = tuple(
        sorted(
            (k, urllib.parse.unquote(v))
            for k, _, v in (q.partition("=") for q in query.split("&") if q)
        )
    )
    return Purl(ptype, namespace, name, urllib.parse.unquote(version), quals)


def strip_qualifier(purl: str, key: str) -> str:
    """Remove one qualifier (e.g. syft's run-specific package-id) from a purl string."""
    base, sep, query = purl.partition("?")
    if not sep:
        return purl
    query, hsep, frag = query.partition("#")
    kept = [q for q in query.split("&") if q and q.split("=", 1)[0] != key]
    out = base + ("?" + "&".join(kept) if kept else "")
    return out + (hsep + frag if hsep else "")


def conda_purl(
    name: str, version: str, build: str, channel: str, subdir: str, fn: str
) -> str:
    """pkg:conda purl (purl spec: build, channel, subdir and type qualifiers)."""
    ext = "conda" if fn.endswith(".conda") else "tar.bz2"
    q = urllib.parse.quote
    return (
        f"pkg:conda/{q(name)}@{q(version)}?build={q(build)}&channel={q(channel)}"
        f"&subdir={q(subdir)}&type={ext}"
    )


def default_supplier(purl: str | None, ctype: str, name: str) -> Supplier | None:
    """Supplier by package ecosystem (see sbom/README.md, "Suppliers")."""
    if ctype == "operating-system":
        return OS_SUPPLIERS.get(name.lower())
    p = parse_purl(purl) if purl else None
    if p is None:
        return None
    if p.type == "conda":
        channel = p.qualifier("channel")
        return CONDA_FORGE if channel in ("conda-forge", "") else Supplier(channel)
    if p.type == "deb":
        return OS_SUPPLIERS.get(p.namespace.lower())
    if p.type == "pypi":
        return PYPI
    if p.type == "cargo":
        return CRATES_IO
    if p.type == "github" and p.namespace:
        return Supplier(f"{p.namespace} (GitHub)", f"https://github.com/{p.namespace}")
    return None


def path_supplier(rules: list[PathSupplier], paths: list[str]) -> Supplier | None:
    """First rule whose prefix covers every location of the component."""
    if not paths:
        return None
    for rule in rules:
        if all(p.startswith(rule.prefix) for p in paths):
            return rule.supplier
    return None


# --- licences -----------------------------------------------------------------

LICENSE_REF = re.compile(r"LicenseRef-[A-Za-z0-9.\-]+")


def license_ref(name: str) -> str:
    """A LicenseRef- id for a free-text licence name."""
    return "LicenseRef-" + re.sub(r"[^A-Za-z0-9.\-]+", "-", name).strip("-")


def cargo_license_expression(text: str) -> str:
    """Cargo's legacy "MIT/Apache-2.0" means "MIT OR Apache-2.0"."""
    return re.sub(r"\s*/\s*", " OR ", text.strip())


def main(argv: list[str]) -> int:
    """`sbomlib.py list CONFIG [KIND]`: lines "name|kind|image|build|lock"."""
    if len(argv) < 2 or argv[0] != "list":
        print("usage: sbomlib.py list CONFIG [KIND]", file=sys.stderr)
        return 2
    config = load_config(Path(argv[1]))
    for t in config.targets.values():
        if len(argv) > 2 and argv[2] and t.kind != argv[2]:
            continue
        print(
            "|".join(
                [t.name, t.kind, t.image, t.build, t.inputs[0] if t.inputs else ""]
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
