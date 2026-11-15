#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
"""Write a raw CycloneDX 1.7 document from a lock file (normalize.py finishes it).

    lock2cdx.py conda LOCK OUT [--platform linux-64] [--licenses-from CDX]...
    lock2cdx.py cargo CARGO_LOCK METADATA_JSON OUT
    lock2cdx.py lean LAKE_MANIFEST LEAN_TOOLCHAIN OUT

conda  every package of the platform: pkg:conda purl (build, channel, subdir), SHA-256
       and MD5 from the lock, download URL, dependency edges; pip entries of the lock
       as pkg:pypi. conda-lock files carry no licences: with --licenses-from, licences
       are copied from an image SBOM whose conda component has the identical purl
       (same name, version and build), i.e. from that package's own conda-meta record.
cargo  every [[package]] of Cargo.lock: pkg:cargo purl and SHA-256 checksum for
       registry crates; licence from `cargo metadata` (legacy "A/B" read as "A OR B");
       workspace members (path sources) have no purl and the project as supplier.
lean   the Lean toolchain (lean-toolchain) and every Lake package (git URL and rev).

The conda reader needs PyYAML (requirements-dev.txt); the others use the standard
library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
import urllib.parse
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sbomlib  # noqa: E402
from sbomlib import Json  # noqa: E402

CRATES_IO_SOURCE = "registry+https://github.com/rust-lang/crates.io-index"


def bom(components: list[Json], dependencies: dict[str, set[str]], tool: str) -> Json:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": sbomlib.CDX_SPEC,
        "version": 1,
        "metadata": {
            "tools": {
                "components": [{"type": "application", "group": "eemt", "name": tool}]
            }
        },
        "components": components,
        "dependencies": [
            {"ref": ref, "dependsOn": sorted(dst)} if dst else {"ref": ref}
            for ref, dst in sorted(dependencies.items())
        ],
    }


# --- conda ----------------------------------------------------------------------


def _conda_split(url: str, name: str, version: str) -> tuple[str, str, str, str]:
    """(channel, subdir, build, filename) from a conda package URL."""
    path = urllib.parse.urlparse(url).path.strip("/").split("/")
    fn = path[-1]
    subdir = path[-2]
    channel = path[-3]
    stem = fn.removesuffix(".conda").removesuffix(".tar.bz2")
    prefix = f"{name}-{version}-"
    if not stem.startswith(prefix):
        raise ValueError(f"cannot read the build string from {fn} ({name} {version})")
    return channel, subdir, stem[len(prefix) :], fn


def load_licenses(paths: list[Path]) -> dict[str, list[Json]]:
    """purl -> CycloneDX licences, from conda components of (normalised) image SBOMs."""
    out: dict[str, list[Json]] = {}
    for path in paths:
        with path.open(encoding="utf-8") as fh:
            doc = json.load(fh)
        for c in doc.get("components", []):
            purl = c.get("purl", "")
            if purl.startswith("pkg:conda/") and c.get("licenses"):
                out[purl] = c["licenses"]
    return out


def conda_components(
    lock: Json, platform: str, licenses: dict[str, list[Json]]
) -> tuple[list[Json], dict[str, set[str]]]:
    pkgs = [p for p in lock["package"] if p.get("platform") == platform]
    comps: list[Json] = []
    ref_by_name: dict[str, str] = {}
    for p in pkgs:
        name, version, url = p["name"], str(p["version"]), p["url"]
        hashes = []
        h = p.get("hash", {})
        if p.get("manager") == "pip":
            # purl spec: PyPI names are lower-cased with "_" and "." read as "-".
            pypi_name = re.sub(r"[-_.]+", "-", name).lower()
            q = urllib.parse.quote
            purl = f"pkg:pypi/{q(pypi_name)}@{q(version)}"
        else:
            channel, subdir, build, fn = _conda_split(url, name, version)
            purl = sbomlib.conda_purl(name, version, build, channel, subdir, fn)
        if h.get("sha256"):
            hashes.append({"alg": "SHA-256", "content": h["sha256"]})
        if h.get("md5"):
            hashes.append({"alg": "MD5", "content": h["md5"]})
        comp: Json = {
            "bom-ref": purl,
            "type": "library",
            "name": name,
            "version": version,
            "purl": purl,
            "externalReferences": [{"type": "distribution", "url": url}],
        }
        if hashes:
            comp["hashes"] = hashes
        if purl in licenses:
            comp["licenses"] = licenses[purl]
        comps.append(comp)
        key = f"{p.get('manager', 'conda')}:{name}"
        if key in ref_by_name:
            raise ValueError(f"package listed twice for {platform}: {name}")
        ref_by_name[key] = purl
    deps: dict[str, set[str]] = {c["bom-ref"]: set() for c in comps}
    for p, c in zip(pkgs, comps, strict=True):
        manager = p.get("manager", "conda")
        for dep in p.get("dependencies", {}) or {}:
            # conda-lock lists dependencies by name; pip entries may depend on conda
            # packages that provide the same distribution, which keeps their own name.
            target = ref_by_name.get(f"{manager}:{dep}") or ref_by_name.get(
                f"conda:{dep}"
            )
            if target and target != c["bom-ref"]:
                deps[c["bom-ref"]].add(target)
    return comps, deps


def conda_bom(lock_path: Path, platform: str, license_sources: list[Path]) -> Json:
    import yaml  # PyYAML, from requirements-dev.txt

    with lock_path.open(encoding="utf-8") as fh:
        lock = yaml.safe_load(fh)
    comps, deps = conda_components(lock, platform, load_licenses(license_sources))
    if not comps:
        raise SystemExit(f"{lock_path}: no packages for platform {platform}")
    return bom(comps, deps, "lock2cdx-conda")


# --- cargo ----------------------------------------------------------------------


def cargo_bom(lock_path: Path, metadata_path: Path, project: sbomlib.Supplier) -> Json:
    with lock_path.open("rb") as fh:
        lock = tomllib.load(fh)
    with metadata_path.open(encoding="utf-8") as fh:
        meta = json.load(fh)
    meta_by_key: dict[tuple[str, str, str], Json] = {}
    for m in meta["packages"]:
        meta_by_key[(m["name"], m["version"], m.get("source") or "")] = m

    comps: list[Json] = []
    ref_of: dict[tuple[str, str], str] = {}
    by_name: dict[str, list[tuple[str, str]]] = {}
    for p in lock["package"]:
        name, version, source = p["name"], p["version"], p.get("source", "")
        m = meta_by_key.get((name, version, source))
        comp: Json = {"type": "library", "name": name, "version": version}
        if source == CRATES_IO_SOURCE:
            purl = f"pkg:cargo/{name}@{version}"
            comp["purl"] = purl
            comp["bom-ref"] = purl
            if p.get("checksum"):
                comp["hashes"] = [{"alg": "SHA-256", "content": p["checksum"]}]
            comp["externalReferences"] = [
                {
                    "type": "distribution",
                    "url": f"https://crates.io/api/v1/crates/{name}/{version}/download",
                }
            ]
        elif not source:
            # Workspace member: the project's own crate, not published anywhere.
            comp["bom-ref"] = f"crate:{name}@{version}"
            comp["supplier"] = project.cdx()
            comp["properties"] = [{"name": "eemt:cargo-source", "value": "workspace"}]
        else:
            raise SystemExit(
                f"{lock_path}: unsupported source for {name} {version}: {source}"
            )
        if m is not None:
            if m.get("license"):
                comp["licenses"] = [
                    {"expression": sbomlib.cargo_license_expression(m["license"])}
                ]
            if m.get("description"):
                comp["description"] = " ".join(str(m["description"]).split())
            if m.get("repository"):
                comp.setdefault("externalReferences", []).append(
                    {"type": "vcs", "url": m["repository"]}
                )
        comps.append(comp)
        ref_of[(name, version)] = comp["bom-ref"]
        by_name.setdefault(name, []).append((name, version))

    deps: dict[str, set[str]] = {c["bom-ref"]: set() for c in comps}
    for p in lock["package"]:
        src = ref_of[(p["name"], p["version"])]
        for d in p.get("dependencies", []):
            parts = d.split(" ")
            if len(parts) >= 2:
                key = (parts[0], parts[1])
            else:
                candidates = by_name.get(parts[0], [])
                if len(candidates) != 1:
                    raise SystemExit(
                        f"{lock_path}: ambiguous dependency {d!r} of {p['name']}"
                    )
                key = candidates[0]
            deps[src].add(ref_of[key])
    return bom(comps, deps, "lock2cdx-cargo")


# --- lean -----------------------------------------------------------------------


def _github(url: str) -> tuple[str, str] | None:
    u = urllib.parse.urlparse(url)
    parts = u.path.strip("/").removesuffix(".git").split("/")
    if u.netloc == "github.com" and len(parts) == 2:
        return parts[0], parts[1]
    return None


def lean_bom(manifest_path: Path, toolchain_path: Path) -> Json:
    with manifest_path.open(encoding="utf-8") as fh:
        manifest = json.load(fh)
    toolchain = toolchain_path.read_text(encoding="utf-8").strip()
    origin, _, tag = toolchain.partition(":")
    owner, _, repo = origin.partition("/")
    comps: list[Json] = [
        {
            "bom-ref": f"pkg:github/{owner}/{repo}@{tag}",
            "type": "platform",
            "name": repo,
            "version": tag,
            "purl": f"pkg:github/{owner}/{repo}@{tag}",
            "properties": [{"name": "eemt:lean-toolchain", "value": toolchain}],
        }
    ]
    for p in manifest.get("packages", []):
        if p.get("type") != "git":
            raise SystemExit(
                f"{manifest_path}: unsupported Lake package type "
                f"{p.get('type')!r} ({p.get('name')})"
            )
        gh = _github(p["url"])
        rev = p["rev"]
        comp: Json = {
            "type": "library",
            "name": p["name"],
            "version": rev,
            "externalReferences": [{"type": "vcs", "url": p["url"]}],
        }
        if gh:
            comp["purl"] = f"pkg:github/{gh[0]}/{gh[1]}@{rev}"
            comp["bom-ref"] = comp["purl"]
        else:
            comp["bom-ref"] = f"git:{p['url']}@{rev}"
        props = [{"name": "eemt:lake-input-rev", "value": str(p.get("inputRev", ""))}]
        if p.get("inherited") is not None:
            props.append(
                {
                    "name": "eemt:lake-inherited",
                    "value": str(bool(p["inherited"])).lower(),
                }
            )
        comp["properties"] = props
        comps.append(comp)
    return bom(comps, {c["bom-ref"]: set() for c in comps}, "lock2cdx-lean")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="kind", required=True)
    c = sub.add_parser("conda")
    c.add_argument("lock", type=Path)
    c.add_argument("output", type=Path)
    c.add_argument("--platform", default="linux-64")
    c.add_argument("--licenses-from", type=Path, action="append", default=[])
    g = sub.add_parser("cargo")
    g.add_argument("lock", type=Path)
    g.add_argument("metadata", type=Path)
    g.add_argument("output", type=Path)
    g.add_argument("--config", type=Path, required=True)
    lean = sub.add_parser("lean")
    lean.add_argument("manifest", type=Path)
    lean.add_argument("toolchain", type=Path)
    lean.add_argument("output", type=Path)
    args = ap.parse_args(argv)

    doc: Any
    if args.kind == "conda":
        doc = conda_bom(args.lock, args.platform, args.licenses_from)
    elif args.kind == "cargo":
        doc = cargo_bom(
            args.lock, args.metadata, sbomlib.load_config(args.config).project_supplier
        )
    else:
        doc = lean_bom(args.manifest, args.toolchain)
    args.output.write_text(sbomlib.dumps(doc), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
