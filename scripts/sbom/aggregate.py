#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
"""Write the aggregate SBOM (sbom/eemt.{cdx,spdx}.json) and sbom/manifest.json.

    aggregate.py --config scripts/sbom/targets.toml --sbom-dir sbom --version V
                 --epoch-file sbom/SOURCE_DATE_EPOCH --tools TOOLS.json
                 --locks LOCKS.txt

Standard library only. The aggregate is hierarchical: its root (the EEMT release)
contains one component/package per member document, nested as targets.toml's
`parent` says (an image contains the environment installed in it). Each member
points at its own document: CycloneDX by BOM-Link (urn:cdx:<serial>/1) with the
file's SHA-256, SPDX by externalDocumentRefs with the file's SHA-1 (what SPDX 2.3
requires). manifest.json records, per target, the files, the inputs with their
sha256, the producing tools (version and image digest) and the component count, and
lists every tracked lock file (LOCKS.txt, one path per line, from `git ls-files`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sbomlib  # noqa: E402
from sbomlib import Json  # noqa: E402

PRODUCERS = {
    "image": ["syft", "normalize"],
    "conda": ["lock2cdx", "normalize", "cdx2spdx"],
    "pip": ["cyclonedx-py", "normalize", "cdx2spdx"],
    "cargo": ["cargo-metadata", "lock2cdx", "normalize", "cdx2spdx"],
    "lean": ["lock2cdx", "normalize", "cdx2spdx"],
}


def load(path: Path) -> Json:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def members(config: sbomlib.Config, sbom_dir: Path) -> list[sbomlib.Target]:
    """Targets with documents in sbom_dir. Every target except lean must have them."""
    out = []
    for t in config.targets.values():
        if t.kind == "aggregate":
            continue
        cdx, spdx = sbom_dir / f"{t.name}.cdx.json", sbom_dir / f"{t.name}.spdx.json"
        if cdx.exists() and spdx.exists():
            out.append(t)
        elif t.kind != "lean" or cdx.exists() or spdx.exists():
            raise SystemExit(f"aggregate: missing {cdx} or {spdx}")
    return out


def aggregate_cdx(
    config: sbomlib.Config,
    agg: sbomlib.Target,
    ms: list[sbomlib.Target],
    sbom_dir: Path,
    version: str,
    epoch: int,
) -> Json:
    names = {t.name for t in ms}
    comps: dict[str, Json] = {}
    for t in ms:
        doc = load(sbom_dir / f"{t.name}.cdx.json")
        root = doc["metadata"]["component"]
        serial = doc["serialNumber"].removeprefix("urn:uuid:")
        comps[t.name] = {
            "bom-ref": t.name,
            "type": root["type"],
            "name": root["name"],
            "version": root["version"],
            "description": root.get("description", ""),
            "supplier": root["supplier"],
            "licenses": root.get("licenses", []),
            "externalReferences": [
                {
                    "type": "bom",
                    "url": f"urn:cdx:{serial}/{doc.get('version', 1)}",
                    "comment": f"sbom/{t.name}.cdx.json",
                    "hashes": [
                        {
                            "alg": "SHA-256",
                            "content": sbomlib.file_sha256(
                                sbom_dir / f"{t.name}.cdx.json"
                            ),
                        }
                    ],
                }
            ],
            "properties": [
                {"name": "eemt:sbom-target", "value": t.name},
                {
                    "name": "eemt:component-count",
                    "value": str(len(doc.get("components", []))),
                },
            ],
        }
    deps: dict[str, list[str]] = {}
    top: list[Json] = []
    for t in ms:
        parent = t.parent if t.parent in names else ""
        if parent:
            comps[parent].setdefault("components", []).append(comps[t.name])
            deps.setdefault(parent, []).append(t.name)
        else:
            top.append(comps[t.name])
            deps.setdefault(agg.root_name, []).append(t.name)
    root_ref = f"{agg.root_name}@{version}"
    deps[root_ref] = deps.pop(agg.root_name, [])
    dependencies = [{"ref": ref, "dependsOn": sorted(d)} for ref, d in deps.items()]
    dependencies += [{"ref": n} for n in sorted(names - set(deps))]
    doc: Json = {
        "bomFormat": "CycloneDX",
        "specVersion": sbomlib.CDX_SPEC,
        "$schema": f"http://cyclonedx.org/schema/bom-{sbomlib.CDX_SPEC}.schema.json",
        "version": 1,
        "metadata": {
            "timestamp": sbomlib.iso_utc(epoch),
            "supplier": config.project_supplier.cdx(),
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "group": "eemt",
                        "name": "eemt-sbom-aggregate",
                    }
                ]
            },
            "component": {
                "bom-ref": root_ref,
                "type": agg.root_type,
                "name": agg.root_name,
                "version": version,
                "description": agg.description,
                "supplier": config.project_supplier.cdx(),
                "licenses": [{"expression": agg.root_license}],
            },
        },
        "components": top,
        "dependencies": dependencies,
    }
    doc["serialNumber"] = f"urn:uuid:{sbomlib.content_uuid(doc, ('serialNumber',))}"
    return sbomlib.canon(doc)


def aggregate_spdx(
    config: sbomlib.Config,
    agg: sbomlib.Target,
    ms: list[sbomlib.Target],
    sbom_dir: Path,
    version: str,
    epoch: int,
) -> Json:
    names = {t.name for t in ms}
    packages: list[Json] = [
        {
            "SPDXID": "SPDXRef-DocumentRoot",
            "name": agg.root_name,
            "versionInfo": version,
            "supplier": config.project_supplier.spdx(),
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": agg.root_license,
            "copyrightText": "NOASSERTION",
            "primaryPackagePurpose": "APPLICATION",
            "description": agg.description,
        }
    ]
    ext_refs: list[Json] = []
    rels: list[Json] = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": "SPDXRef-DocumentRoot",
        }
    ]
    for t in ms:
        path = sbom_dir / f"{t.name}.spdx.json"
        doc = load(path)
        root = next(p for p in doc["packages"] if p["SPDXID"] == "SPDXRef-DocumentRoot")
        sid = f"SPDXRef-Member-{t.name}"
        docref = f"DocumentRef-{t.name}"
        pkg = {
            k: root[k]
            for k in (
                "name",
                "versionInfo",
                "supplier",
                "licenseDeclared",
                "primaryPackagePurpose",
                "description",
            )
            if k in root
        }
        pkg.update(
            {
                "SPDXID": sid,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "comment": f"Described by sbom/{t.name}.spdx.json "
                f"({len(doc['packages']) - 1} packages).",
            }
        )
        packages.append(pkg)
        ext_refs.append(
            {
                "externalDocumentId": docref,
                "spdxDocument": doc["documentNamespace"],
                "checksum": {
                    "algorithm": "SHA1",
                    "checksumValue": sbomlib.file_sha1(path),
                },
            }
        )
        parent = (
            f"SPDXRef-Member-{t.parent}"
            if t.parent in names
            else "SPDXRef-DocumentRoot"
        )
        rels.append(
            {
                "spdxElementId": parent,
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": sid,
            }
        )
        rels.append(
            {
                "spdxElementId": sid,
                "relationshipType": "DESCRIBED_BY",
                "relatedSpdxElement": f"{docref}:SPDXRef-DOCUMENT",
            }
        )
    for lic in [p["licenseDeclared"] for p in packages]:
        if sbomlib.LICENSE_REF.search(lic):
            raise SystemExit(
                f"aggregate: member root licence must be an SPDX id: {lic}"
            )
    doc = {
        "spdxVersion": sbomlib.SPDX_SPEC,
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": agg.name,
        "creationInfo": {
            "created": sbomlib.iso_utc(epoch),
            "creators": sorted(
                [config.project_supplier.spdx(), "Tool: eemt-sbom-aggregate"]
            ),
        },
        "externalDocumentRefs": ext_refs,
        "packages": packages,
        "relationships": rels,
    }
    doc["documentNamespace"] = (
        f"{config.namespace}/{agg.name}-"
        f"{sbomlib.content_uuid(doc, ('documentNamespace',))}"
    )
    return sbomlib.canon(doc)


def tool_versions(doc: Json) -> dict[str, str]:
    """name -> version of the tools a CycloneDX document names in metadata.tools."""
    tools = doc.get("metadata", {}).get("tools", {})
    return {
        t["name"]: t["version"] for t in tools.get("components", []) if t.get("version")
    }


def manifest(
    config: sbomlib.Config,
    ms: list[sbomlib.Target],
    sbom_dir: Path,
    root: Path,
    version: str,
    epoch: int,
    tools: dict[str, Any],
    locks: list[str],
) -> Json:
    targets = []
    lock_users: dict[str, list[str]] = {}
    for t in ms:
        cdx = load(sbom_dir / f"{t.name}.cdx.json")
        spdx = load(sbom_dir / f"{t.name}.spdx.json")
        producers = {}
        for step in PRODUCERS[t.kind]:
            info = dict(tools[step])
            if step == "syft":
                info["version"] = tool_versions(cdx).get("syft", "")
            if step == "cyclonedx-py":
                info["version"] = tool_versions(cdx).get("cyclonedx-py", "")
            producers[step] = info
        inputs = [
            {"path": p, "sha256": sbomlib.file_sha256(root / p)}
            for p in t.inputs
            if (root / p).exists()
        ]
        if t.inputs:
            lock_users.setdefault(t.inputs[0], []).append(t.name)
        targets.append(
            {
                "name": t.name,
                "kind": t.kind,
                "parent": t.parent or "eemt",
                "cyclonedx": {
                    "path": f"sbom/{t.name}.cdx.json",
                    "spec": cdx["specVersion"],
                    "sha256": sbomlib.file_sha256(sbom_dir / f"{t.name}.cdx.json"),
                },
                "spdx": {
                    "path": f"sbom/{t.name}.spdx.json",
                    "spec": spdx["spdxVersion"],
                    "sha256": sbomlib.file_sha256(sbom_dir / f"{t.name}.spdx.json"),
                },
                "inputs": inputs,
                "producers": producers,
                "components": len(cdx.get("components", [])),
            }
        )
    lock_entries = []
    for path in sorted(locks):
        entry: Json = {
            "path": path,
            "sha256": sbomlib.file_sha256(root / path),
            "sbom": sorted(lock_users.get(path, [])),
        }
        if not entry["sbom"]:
            entry["reason"] = config.locks_without_sbom.get(path, "")
        lock_entries.append(entry)
    return {
        "aggregate": {"cyclonedx": "sbom/eemt.cdx.json", "spdx": "sbom/eemt.spdx.json"},
        "version": version,
        "source_date_epoch": epoch,
        "spec": {"cyclonedx": sbomlib.CDX_SPEC, "spdx": sbomlib.SPDX_SPEC},
        "tools": tools,
        "targets": targets,
        "locks": lock_entries,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--sbom-dir", type=Path, required=True)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--version", required=True)
    ap.add_argument("--epoch-file", type=Path, required=True)
    ap.add_argument(
        "--tools",
        type=Path,
        required=True,
        help="JSON: producer step -> {image, version, ...}",
    )
    ap.add_argument(
        "--locks", type=Path, required=True, help="tracked lock files, one per line"
    )
    args = ap.parse_args(argv)

    config = sbomlib.load_config(args.config)
    agg = next(t for t in config.targets.values() if t.kind == "aggregate")
    epoch = sbomlib.read_epoch(args.epoch_file)
    ms = sorted(members(config, args.sbom_dir), key=lambda t: t.name)
    tools = load(args.tools)
    locks = [
        line.strip()
        for line in args.locks.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for name, doc in (
        (
            f"{agg.name}.cdx.json",
            aggregate_cdx(config, agg, ms, args.sbom_dir, args.version, epoch),
        ),
        (
            f"{agg.name}.spdx.json",
            aggregate_spdx(config, agg, ms, args.sbom_dir, args.version, epoch),
        ),
        (
            "manifest.json",
            manifest(
                config, ms, args.sbom_dir, args.root, args.version, epoch, tools, locks
            ),
        ),
    ):
        (args.sbom_dir / name).write_text(
            sbomlib.dumps(doc), encoding="utf-8", newline="\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
