#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
"""Convert a normalised lock-derived CycloneDX document into SPDX 2.3 JSON.

    cdx2spdx.py IN.cdx.json OUT.spdx.json

Standard library only. Used for the documents made from lock files (conda, pip,
cargo, lean); image documents take their SPDX directly from syft. Unlike
`syft convert`, it keeps for every component: the purl (externalRefs), the package
hash (checksums; for pip, whose lock allows several distribution files, the allowed
SHA-256 values go into the package comment), the download URL (downloadLocation),
the licences (licenseDeclared, with LicenseRef declarations), the supplier and the
dependency edges (DEPENDS_ON). Run normalize.py on the result.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sbomlib  # noqa: E402
from sbomlib import Json  # noqa: E402

ALG = {
    "SHA-256": "SHA256",
    "SHA-1": "SHA1",
    "MD5": "MD5",
    "SHA-512": "SHA512",
    "SHA-384": "SHA384",
}
PURPOSE = {
    "application": "APPLICATION",
    "library": "LIBRARY",
    "container": "CONTAINER",
    "platform": "FRAMEWORK",
    "framework": "FRAMEWORK",
    "operating-system": "OPERATING-SYSTEM",
}


def license_expression(licenses: list[Json]) -> str:
    """CycloneDX licence choices -> one SPDX expression (several entries are ANDed)."""
    parts: list[str] = []
    for entry in licenses:
        if "expression" in entry:
            expr = str(entry["expression"]).strip()
            parts.append(f"({expr})" if len(licenses) > 1 and " " in expr else expr)
        else:
            lic = entry.get("license", {})
            if lic.get("id"):
                parts.append(lic["id"])
            elif lic.get("name"):
                parts.append(sbomlib.license_ref(lic["name"]))
    return " AND ".join(parts) if parts else "NOASSERTION"


def supplier(comp: Json) -> str:
    sup = comp.get("supplier") or {}
    return f"Organization: {sup['name']}" if sup.get("name") else "NOASSERTION"


def convert(doc: Json) -> Json:
    md = doc["metadata"]
    root = md["component"]
    ref_to_id: dict[str, str] = {root["bom-ref"]: "SPDXRef-DocumentRoot"}
    packages: list[Json] = [
        {
            "SPDXID": "SPDXRef-DocumentRoot",
            "name": root["name"],
            "versionInfo": root.get("version", ""),
            "supplier": supplier(root),
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": license_expression(root.get("licenses", [])),
            "copyrightText": "NOASSERTION",
            "primaryPackagePurpose": PURPOSE.get(root.get("type", ""), "APPLICATION"),
        }
    ]
    for i, comp in enumerate(doc.get("components", [])):
        sid = f"SPDXRef-Component-{i}"
        ref_to_id[comp["bom-ref"]] = sid
        dist = [
            r["url"]
            for r in comp.get("externalReferences", [])
            if r.get("type") == "distribution"
        ]
        pkg: Json = {
            "SPDXID": sid,
            "name": comp["name"],
            "versionInfo": comp.get("version", ""),
            "supplier": supplier(comp),
            "downloadLocation": dist[0]
            if len(dist) == 1 and "/simple/" not in dist[0]
            else "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": license_expression(comp.get("licenses", [])),
            "copyrightText": "NOASSERTION",
            "primaryPackagePurpose": PURPOSE.get(comp.get("type", ""), "LIBRARY"),
        }
        if comp.get("description"):
            pkg["description"] = comp["description"]
        if comp.get("purl"):
            pkg["externalRefs"] = [
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": comp["purl"],
                }
            ]
        checksums = [
            {"algorithm": ALG[h["alg"]], "checksumValue": h["content"]}
            for h in comp.get("hashes", [])
            if h["alg"] in ALG
        ]
        if checksums:
            pkg["checksums"] = checksums
        # pip: the hashed lock allows several files (wheels, sdist) per release.
        dist_hashes = sorted(
            h["content"]
            for r in comp.get("externalReferences", [])
            if r.get("type") == "distribution"
            for h in r.get("hashes", [])
            if h.get("alg") == "SHA-256"
        )
        if dist_hashes:
            pkg["comment"] = (
                "Distribution files allowed by the hashed lock (SHA-256): "
                + ", ".join(dist_hashes)
            )
        packages.append(pkg)

    rels: list[Json] = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": "SPDXRef-DocumentRoot",
        }
    ]
    for sid in sorted(set(ref_to_id.values()) - {"SPDXRef-DocumentRoot"}):
        rels.append(
            {
                "spdxElementId": "SPDXRef-DocumentRoot",
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": sid,
            }
        )
    for dep in doc.get("dependencies", []):
        src = ref_to_id.get(dep["ref"])
        if src is None or src == "SPDXRef-DocumentRoot":
            continue
        for t in dep.get("dependsOn", []):
            dst = ref_to_id.get(t)
            if dst and dst != src:
                rels.append(
                    {
                        "spdxElementId": src,
                        "relationshipType": "DEPENDS_ON",
                        "relatedSpdxElement": dst,
                    }
                )

    tools = [t for t in md.get("tools", {}).get("components", []) if t.get("name")]
    creators = sorted(
        {
            f"Tool: {t['name']}" + (f"-{t['version']}" if t.get("version") else "")
            for t in tools
        }
        | {"Tool: cdx2spdx"}
    )
    out: Json = {
        "spdxVersion": sbomlib.SPDX_SPEC,
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": root["name"],
        "documentNamespace": "https://invalid.example/replaced-by-normalize",
        "creationInfo": {
            "created": md.get("timestamp", "1970-01-01T00:00:00Z"),
            "creators": creators,
        },
        "packages": packages,
        "relationships": rels,
    }
    from normalize import declare_license_refs  # noqa: PLC0415 - shared helper

    declare_license_refs(out)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path)
    args = ap.parse_args(argv)
    with args.input.open(encoding="utf-8") as fh:
        doc = json.load(fh)
    if doc.get("bomFormat") != "CycloneDX":
        raise SystemExit(f"{args.input}: not CycloneDX JSON")
    args.output.write_text(sbomlib.dumps(convert(doc)), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
