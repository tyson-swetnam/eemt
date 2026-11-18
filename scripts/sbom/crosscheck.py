#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
"""Consistency checks over the committed SBOM tree (part of scripts/sbom/validate.sh).

    crosscheck.py --config scripts/sbom/targets.toml --sbom-dir sbom --root .
                  --locks LOCKS.txt [--forbid STRING]...

1. conda: the conda packages in image-eemt equal docker/workflow/conda-lock.yml
   (name, version, build and sha256), and so do those of env-conda-workflow.
2. pip: the PyPI packages in image-eemt-web equal web-interface/requirements.txt
   (name and version), apart from packages the base image ships (BASE_IMAGE_PYPI).
3. manifest: every tracked lock file (LOCKS.txt) is listed with its current sha256 and
   an SBOM or a reason; every target's files and inputs have the recorded sha256; the
   recorded tool versions are the ones the documents and the tool lock name.
4. twins: each CycloneDX document and its SPDX twin list the same purls and the same
   number of components.
5. aggregate: sbom/eemt.* reference exactly the member documents, with matching
   serial number/namespace and file hashes.
6. privacy: no image labels, OCI image digests, scan paths or --forbid strings
   (home directory, user and host name, image ids) in any committed document.

Needs PyYAML (requirements-dev.txt) for the conda lock. Exit status 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sbomlib  # noqa: E402
from sbomlib import Json  # noqa: E402

# PyPI packages that the python:3.14.10-slim-trixie base image installs itself.
BASE_IMAGE_PYPI = {
    "pip": "installed by the python:3.14.10-slim-trixie base image (ensurepip)"
}

FORBIDDEN_ALWAYS = ("syft:image:labels", "docker-archive:", "pkg:oci/", "/cache/work/")


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.passed: list[str] = []

    def check(self, ok: bool, what: str, detail: str = "") -> None:
        if ok:
            self.passed.append(what)
            print(f"ok    {what}")
        else:
            self.failures.append(what)
            print(f"FAIL  {what}" + (f": {detail}" if detail else ""))


def load(path: Path) -> Json:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def canonical_pypi(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def conda_set_from_lock(
    lock_path: Path, platform: str = "linux-64"
) -> set[tuple[str, str, str, str]]:
    import yaml  # PyYAML

    with lock_path.open(encoding="utf-8") as fh:
        lock = yaml.safe_load(fh)
    out = set()
    for p in lock["package"]:
        if p.get("platform") != platform or p.get("manager", "conda") != "conda":
            continue
        fn = urllib.parse.urlparse(p["url"]).path.rsplit("/", 1)[-1]
        stem = fn.removesuffix(".conda").removesuffix(".tar.bz2")
        build = stem[len(f"{p['name']}-{p['version']}-") :]
        out.add((p["name"], str(p["version"]), build, p["hash"]["sha256"]))
    return out


def conda_set_from_sbom(doc: Json) -> set[tuple[str, str, str, str]]:
    out = set()
    for c in doc.get("components", []):
        p = sbomlib.parse_purl(c.get("purl", ""))
        if p is None or p.type != "conda":
            continue
        sha = next(
            (h["content"] for h in c.get("hashes", []) if h["alg"] == "SHA-256"), ""
        )
        out.add((p.name, p.version, p.qualifier("build"), sha))
    return out


def pypi_set_from_requirements(path: Path) -> set[tuple[str, str]]:
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?==([^\s;\\]+)", line)
        if m:
            out.add((canonical_pypi(m.group(1)), m.group(3)))
    return out


def pypi_set_from_sbom(doc: Json) -> set[tuple[str, str]]:
    out = set()
    for c in doc.get("components", []):
        p = sbomlib.parse_purl(c.get("purl", ""))
        if p is not None and p.type == "pypi":
            out.add((canonical_pypi(p.name), p.version))
    return out


def fmt(items: set[tuple[str, ...]], limit: int = 8) -> str:
    s = sorted(items)
    return ", ".join("/".join(x) for x in s[:limit]) + (
        f" (+{len(s) - limit} more)" if len(s) > limit else ""
    )


def check_conda(r: Report, sbom_dir: Path, root: Path) -> None:
    lock = conda_set_from_lock(root / "docker/workflow/conda-lock.yml")
    for name in ("image-eemt", "env-conda-workflow"):
        got = conda_set_from_sbom(load(sbom_dir / f"{name}.cdx.json"))
        r.check(
            got == lock and len(lock) > 0,
            f"conda packages of {name} == docker/workflow/conda-lock.yml "
            f"({len(lock)} name/version/build/sha256)",
            f"only in SBOM: {fmt(got - lock)}; only in lock: {fmt(lock - got)}",
        )


def check_pip(r: Report, sbom_dir: Path, root: Path) -> None:
    req = pypi_set_from_requirements(root / "web-interface/requirements.txt")
    img = {
        x
        for x in pypi_set_from_sbom(load(sbom_dir / "image-eemt-web.cdx.json"))
        if x[0] not in BASE_IMAGE_PYPI
    }
    r.check(
        img <= req,
        "PyPI packages of image-eemt-web are a subset of "
        "web-interface/requirements.txt",
        f"not in the lock: {fmt(img - req)}",
    )
    r.check(
        req <= img,
        "every web-interface/requirements.txt pin is installed in image-eemt-web",
        f"missing from the image: {fmt(req - img)}",
    )
    env = pypi_set_from_sbom(load(sbom_dir / "env-pip-web.cdx.json"))
    r.check(
        env == req,
        "env-pip-web lists exactly the pins of web-interface/requirements.txt",
        f"only in SBOM: {fmt(env - req)}; only in lock: {fmt(req - env)}",
    )


def tool_versions(doc: Json) -> dict[str, str]:
    tools = doc.get("metadata", {}).get("tools", {})
    return {
        t["name"]: t["version"] for t in tools.get("components", []) if t.get("version")
    }


def check_manifest(
    r: Report, config: sbomlib.Config, sbom_dir: Path, root: Path, locks: list[str]
) -> list[str]:
    m = load(sbom_dir / "manifest.json")
    listed = {e["path"]: e for e in m["locks"]}
    r.check(
        set(listed) == set(locks),
        "manifest lists every tracked lock file and no other",
        f"untracked in manifest: {sorted(set(locks) - set(listed))}; "
        f"extra: {sorted(set(listed) - set(locks))}",
    )
    bad = [
        p
        for p, e in listed.items()
        if (root / p).exists() and sbomlib.file_sha256(root / p) != e["sha256"]
    ]
    r.check(
        not bad,
        "manifest sha256 of every lock file is current (else regenerate the SBOMs)",
        f"stale: {bad}",
    )
    orphan = [p for p, e in listed.items() if not e["sbom"] and not e.get("reason")]
    r.check(not orphan, "every lock file has an SBOM or a recorded reason", f"{orphan}")

    stale = []
    names = []
    for t in m["targets"]:
        names.append(t["name"])
        for kind in ("cyclonedx", "spdx"):
            f = root / t[kind]["path"]
            if not f.exists() or sbomlib.file_sha256(f) != t[kind]["sha256"]:
                stale.append(t[kind]["path"])
        for i in t["inputs"]:
            if sbomlib.file_sha256(root / i["path"]) != i["sha256"]:
                stale.append(i["path"])
        cdx = load(root / t["cyclonedx"]["path"])
        if t["components"] != len(cdx.get("components", [])):
            stale.append(f"{t['name']} component count")
        if (
            t["cyclonedx"]["spec"] != sbomlib.CDX_SPEC
            or t["spdx"]["spec"] != sbomlib.SPDX_SPEC
        ):
            stale.append(f"{t['name']} spec versions")
        vers = tool_versions(cdx)
        for step, info in t["producers"].items():
            if step in ("syft", "cyclonedx-py") and info.get("version") != vers.get(
                step
            ):
                stale.append(
                    f"{t['name']} {step} version {info.get('version')} "
                    f"!= document {vers.get(step)}"
                )
    r.check(
        not stale,
        "manifest hashes, inputs, counts and spec versions match the files",
        f"{stale}",
    )

    tools = m["tools"]
    syft_v = next(
        (
            t["producers"]["syft"]["version"]
            for t in m["targets"]
            if "syft" in t["producers"]
        ),
        "",
    )
    r.check(
        f":v{syft_v}@" in tools["syft"]["image"],
        f"syft version {syft_v} is the pinned image's tag",
        tools["syft"]["image"],
    )
    pins = pypi_set_from_requirements(root / tools["cyclonedx-py"]["lock"])
    cdxpy = next(
        (
            t["producers"]["cyclonedx-py"]["version"]
            for t in m["targets"]
            if "cyclonedx-py" in t["producers"]
        ),
        "",
    )
    r.check(
        ("cyclonedx-bom", cdxpy) in pins,
        f"cyclonedx-py version {cdxpy} is the one pinned in "
        f"{tools['cyclonedx-py']['lock']}",
    )
    r.check(
        sbomlib.file_sha256(root / tools["cyclonedx-py"]["lock"])
        == tools["cyclonedx-py"]["lock_sha256"],
        "manifest sha256 of the SBOM tool lock is current",
    )

    expected = sorted(
        t.name
        for t in config.targets.values()
        if t.kind != "aggregate" and (sbom_dir / f"{t.name}.cdx.json").exists()
    )
    present = sorted(
        p.name.removesuffix(".cdx.json")
        for p in sbom_dir.glob("*.cdx.json")
        if p.name != "eemt.cdx.json"
    )
    r.check(
        sorted(names) == expected == present,
        "manifest targets == documents in sbom/ == targets.toml",
        f"manifest {sorted(names)}; files {present}; config {expected}",
    )
    return names


def purls_cdx(doc: Json) -> list[str]:
    return sorted(c["purl"] for c in doc.get("components", []) if c.get("purl"))


def purls_spdx(doc: Json) -> list[str]:
    return sorted(
        r["referenceLocator"]
        for p in doc["packages"]
        if p["SPDXID"] != "SPDXRef-DocumentRoot"
        for r in p.get("externalRefs", [])
        if r.get("referenceType") == "purl"
    )


def check_twins(r: Report, sbom_dir: Path, names: list[str]) -> None:
    bad = []
    for n in names:
        cdx, spdx = load(sbom_dir / f"{n}.cdx.json"), load(sbom_dir / f"{n}.spdx.json")
        nv_cdx = sorted(
            (c["name"], c.get("version", "")) for c in cdx.get("components", [])
        )
        nv_spdx = sorted(
            (p["name"], p.get("versionInfo", ""))
            for p in spdx["packages"]
            if p["SPDXID"] != "SPDXRef-DocumentRoot"
        )
        if purls_cdx(cdx) != purls_spdx(spdx) or nv_cdx != nv_spdx:
            bad.append(n)
    r.check(
        not bad,
        "CycloneDX and SPDX twins list the same components (name, version, purl)",
        f"{bad}",
    )


def check_aggregate(r: Report, sbom_dir: Path, names: list[str]) -> None:
    agg = load(sbom_dir / "eemt.cdx.json")
    found: dict[str, Json] = {}

    def walk(comps: list[Json]) -> None:
        for c in comps:
            found[c["bom-ref"]] = c
            walk(c.get("components", []))

    walk(agg.get("components", []))
    problems = []
    for n in names:
        c = found.get(n)
        doc = load(sbom_dir / f"{n}.cdx.json")
        if c is None:
            problems.append(f"{n} missing")
            continue
        ref = next(e for e in c["externalReferences"] if e["type"] == "bom")
        if (
            ref["url"] != f"urn:cdx:{doc['serialNumber'].removeprefix('urn:uuid:')}/"
            f"{doc.get('version', 1)}"
        ):
            problems.append(f"{n} serial")
        if ref["hashes"][0]["content"] != sbomlib.file_sha256(
            sbom_dir / f"{n}.cdx.json"
        ):
            problems.append(f"{n} sha256")
    if set(found) != set(names):
        problems.append(f"aggregate has {sorted(set(found) - set(names))} extra")
    spdx = load(sbom_dir / "eemt.spdx.json")
    refs = {
        e["externalDocumentId"].removeprefix("DocumentRef-"): e
        for e in spdx.get("externalDocumentRefs", [])
    }
    if set(refs) != set(names):
        problems.append("SPDX externalDocumentRefs != members")
    for n, e in refs.items():
        path = sbom_dir / f"{n}.spdx.json"
        if not path.exists():
            continue
        if e["spdxDocument"] != load(path)["documentNamespace"]:
            problems.append(f"{n} SPDX namespace")
        if e["checksum"]["checksumValue"] != sbomlib.file_sha1(path):
            problems.append(f"{n} SPDX sha1")
    r.check(
        not problems,
        f"aggregate eemt.* references the {len(names)} member documents consistently",
        f"{problems}",
    )


def check_privacy(r: Report, sbom_dir: Path, forbid: list[str]) -> None:
    hits = []
    for path in sorted(sbom_dir.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        for s in (*FORBIDDEN_ALWAYS, *[f for f in forbid if f]):
            if s in text:
                hits.append(f"{path.name}: {s!r}")
    r.check(
        not hits,
        "no image labels, image digests, scan paths or local names in sbom/*.json",
        "; ".join(hits[:10]),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--sbom-dir", type=Path, required=True)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--locks", type=Path, required=True)
    ap.add_argument("--forbid", action="append", default=[])
    args = ap.parse_args(argv)

    config = sbomlib.load_config(args.config)
    locks = [
        x.strip()
        for x in args.locks.read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    r = Report()
    check_conda(r, args.sbom_dir, args.root)
    check_pip(r, args.sbom_dir, args.root)
    names = check_manifest(r, config, args.sbom_dir, args.root, locks)
    check_twins(r, args.sbom_dir, names)
    check_aggregate(r, args.sbom_dir, names)
    check_privacy(r, args.sbom_dir, args.forbid)
    print(f"crosscheck: {len(r.passed)} passed, {len(r.failures)} failed")
    return 1 if r.failures else 0


if __name__ == "__main__":
    sys.exit(main())
