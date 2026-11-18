#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
"""Normalise a CycloneDX 1.7 or SPDX 2.3 JSON SBOM so that it is reproducible.

    normalize.py IN OUT --config scripts/sbom/targets.toml --target NAME
                 --epoch-file sbom/SOURCE_DATE_EPOCH --version 3.0.0
                 [--syft-json SCAN.syft.json] [--keep-files]

Standard library only. What it does (sbom/README.md, "Normalisation"):

- timestamps come from sbom/SOURCE_DATE_EPOCH; the CycloneDX serialNumber and the
  SPDX documentNamespace are uuid5 values of the canonical content;
- file components/files, image layer ids, syft package-id qualifiers, image labels,
  the image id/digest and the scanned tarball path are removed;
- conda packages found in an image get a pkg:conda purl, SHA-256/MD5 hashes and their
  download URL from the image's own conda-meta records (via the syft-json scan);
- a non-conda component whose every file belongs to a conda package (the python
  dist-info, binaries and vendored launchers inside that package) is merged into it;
- suppliers: the distributing registry (conda-forge, PyPI, crates.io, Ubuntu,
  Debian) or a per-target path rule; never the project for third-party packages;
- bom-refs and SPDXIDs are rebuilt from the purl (or type/name/version and file
  locations), references are rewritten, every LicenseRef is declared;
- keys and arrays are sorted; two-space indent; trailing newline.

Applying it twice gives the same bytes as applying it once.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sbomlib  # noqa: E402
from sbomlib import Json, Supplier  # noqa: E402

LAYER_PROP = re.compile(r"^syft:location:\d+:layerID$")
LOCATION_PROP = re.compile(r"^syft:location:\d+:path$")
SYFT_ID = re.compile(r"^[0-9a-f]{16}$")
SPDX_SYFT_ID = re.compile(r"-([0-9a-f]{16})$")
MERGED_PROP = "eemt:merged-duplicate"
NORMALIZER = "eemt-sbom-normalize"


# --- syft-json side input -------------------------------------------------------


@dataclass
class Artifact:
    id: str
    type: str
    name: str
    version: str
    paths: list[str]
    conda: dict[str, Any] | None = None


@dataclass
class SyftIndex:
    """What normalisation needs from syft's native output of the same scan."""

    artifacts: dict[str, Artifact] = field(default_factory=dict)
    owner: dict[str, str] = field(
        default_factory=dict
    )  # merged artifact id -> conda id
    distro: dict[str, Any] = field(default_factory=dict)  # the image's /etc/os-release
    conda_deps: dict[str, set[str]] = field(default_factory=dict)  # id -> depends ids

    @classmethod
    def load(cls, path: Path) -> SyftIndex:
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        return cls.from_syft(raw)

    @classmethod
    def from_syft(cls, raw: Json) -> SyftIndex:
        index = cls()
        index.distro = dict(raw.get("distro") or {})
        file_owner: dict[str, set[str]] = {}
        for a in raw.get("artifacts", []):
            paths = sorted({loc["path"] for loc in a.get("locations", [])})
            art = Artifact(a["id"], a["type"], a["name"], a.get("version", ""), paths)
            if a["type"] == "conda":
                meta = a.get("metadata", {})
                art.conda = meta
                # conda-meta/<dist>.json lives in <prefix>/conda-meta/
                prefix = posixpath.dirname(posixpath.dirname(paths[0])) if paths else ""
                # conda-meta lists the package's files in "files" and/or "paths_data".
                rels = set(meta.get("files") or [])
                rels |= {
                    p["_path"] for p in (meta.get("paths_data") or {}).get("paths", [])
                }
                for f in rels:
                    file_owner.setdefault(posixpath.join(prefix, f), set()).add(a["id"])
            index.artifacts[a["id"]] = art
        for art in index.artifacts.values():
            if art.type == "conda" or not art.paths:
                continue
            per_path = [file_owner.get(p, set()) for p in art.paths]
            if not all(per_path):
                continue  # some file is not from any conda package: keep the component
            owners = set().union(*per_path)
            if len(owners) == 1:
                index.owner[art.id] = next(iter(owners))
            else:
                # e.g. syft's "python" binary match spans bin/python3.14 (conda
                # "python") and lib/libpython3.14.so (conda "libpython"): merge into
                # the conda package of the same name, if there is exactly one.
                same = [o for o in owners if index.artifacts[o].name == art.name]
                if len(same) == 1:
                    index.owner[art.id] = same[0]
        # Dependency edges between conda packages, from each package's own conda-meta
        # "depends" (match specs: the first word is the package name).
        conda_by_name = {
            a.name: a.id for a in index.artifacts.values() if a.conda is not None
        }
        for a in index.artifacts.values():
            if a.conda is None:
                continue
            for spec in a.conda.get("depends") or []:
                dep = conda_by_name.get(str(spec).split()[0]) if spec else None
                if dep and dep != a.id:
                    index.conda_deps.setdefault(a.id, set()).add(dep)
        return index

    def conda_identity(self, art: Artifact) -> tuple[str, list[dict[str, str]], str]:
        """(purl, CycloneDX hashes, download url) of a conda artifact."""
        m = art.conda or {}
        purl = sbomlib.conda_purl(
            m.get("name", art.name),
            m.get("version", art.version),
            m.get("build", ""),
            m.get("channel", "conda-forge"),
            m.get("subdir", ""),
            m.get("fn", m.get("url", "")),
        )
        hashes = []
        if m.get("sha256"):
            hashes.append({"alg": "SHA-256", "content": m["sha256"]})
        if m.get("md5"):
            hashes.append({"alg": "MD5", "content": m["md5"]})
        return purl, hashes, m.get("url", "")


# --- options --------------------------------------------------------------------


@dataclass
class Options:
    name: str  # document name (the target)
    root_name: str  # name of the described component
    version: str
    epoch: int
    root_type: str
    root_license: str
    description: str
    project_supplier: Supplier
    namespace: str
    path_suppliers: list[sbomlib.PathSupplier] = field(default_factory=list)
    syft: SyftIndex | None = None
    keep_files: bool = False


def supplier_for(
    opts: Options, purl: str | None, ctype: str, name: str, paths: list[str]
) -> Supplier | None:
    return sbomlib.default_supplier(purl, ctype, name) or sbomlib.path_supplier(
        opts.path_suppliers, paths
    )


def ref_key(purl: str | None, ctype: str, name: str, version: str) -> str:
    return purl or f"{ctype}:{name}@{version}"


def unique_refs(keys: dict[str, tuple[str, list[str]]]) -> dict[str, str]:
    """old id -> stable id; keys that collide get a suffix from their file locations."""
    count: dict[str, int] = {}
    for key, _ in keys.values():
        count[key] = count.get(key, 0) + 1
    out: dict[str, str] = {}
    for old, (key, paths) in keys.items():
        new = key if count[key] == 1 else f"{key}#{sbomlib.sha256_hex(*paths)[:12]}"
        out[old] = new
    if len(set(out.values())) != len(out):
        dup = sorted({v for v in out.values() if list(out.values()).count(v) > 1})
        raise SystemExit(f"normalize: non-unique component identities: {dup[:5]}")
    return out


# --- CycloneDX ------------------------------------------------------------------


def _cdx_syft_id(comp: Json, index: SyftIndex | None) -> str | None:
    if index is None:
        return None
    ref = comp.get("bom-ref", "")
    m = re.search(r"[?&]package-id=([0-9a-f]+)", ref)
    sid = m.group(1) if m else ref
    return sid if sid in index.artifacts else None


def _cdx_paths(comp: Json) -> list[str]:
    return sorted(
        p["value"] for p in comp.get("properties", []) if LOCATION_PROP.match(p["name"])
    )


def _merge_into(target: Json, value: str) -> None:
    props = target.setdefault("properties", [])
    entry = {"name": MERGED_PROP, "value": value}
    if entry not in props:
        props.append(entry)


def _cdx_already_normalized(doc: Json) -> bool:
    tools = doc.get("metadata", {}).get("tools", {})
    comps = tools.get("components", []) if isinstance(tools, dict) else []
    return any(t.get("name") == NORMALIZER for t in comps)


def normalize_cdx(doc: Json, opts: Options) -> Json:
    index = opts.syft
    comps_in = [
        c
        for c in doc.get("components", [])
        if opts.keep_files or c.get("type") != "file"
    ]
    by_old: dict[str, Json] = {}
    syft_of: dict[str, str] = {}
    for c in comps_in:
        old = c.get("bom-ref") or sbomlib.sha256_hex(json.dumps(c, sort_keys=True))
        c["bom-ref"] = old
        by_old[old] = c
        sid = _cdx_syft_id(c, index)
        if sid:
            syft_of[old] = sid

    # Clean, enrich conda packages.
    for old, c in by_old.items():
        c["properties"] = [
            p for p in c.get("properties", []) if not LAYER_PROP.match(p["name"])
        ]
        if "purl" in c:
            c["purl"] = sbomlib.strip_qualifier(c["purl"], "package-id")
        sid = syft_of.get(old)
        if index and sid and index.artifacts[sid].conda is not None:
            purl, hashes, url = index.conda_identity(index.artifacts[sid])
            c["purl"] = purl
            if hashes:
                c["hashes"] = hashes
            if url:
                refs = [
                    r
                    for r in c.get("externalReferences", [])
                    if r.get("type") != "distribution"
                ]
                refs.append({"type": "distribution", "url": url})
                c["externalReferences"] = refs

    # Merge duplicates (files owned by a conda package) into that package.
    conda_ref = {
        sid: old
        for old, sid in syft_of.items()
        if index and index.artifacts[sid].conda is not None
    }
    redirect: dict[str, str] = {}
    if index:
        for old, sid in syft_of.items():
            owner = index.owner.get(sid)
            if owner and owner in conda_ref:
                c = by_old[old]
                _merge_into(
                    by_old[conda_ref[owner]],
                    ref_key(c.get("purl"), c["type"], c["name"], c.get("version", "")),
                )
                redirect[old] = conda_ref[owner]
    kept = {old: c for old, c in by_old.items() if old not in redirect}

    # Suppliers.
    for old, c in kept.items():
        sid = syft_of.get(old)
        paths = index.artifacts[sid].paths if (index and sid) else _cdx_paths(c)
        sup = supplier_for(
            opts, c.get("purl"), c.get("type", ""), c.get("name", ""), paths
        )
        if sup is not None:
            c["supplier"] = sup.cdx()
        if not c["properties"]:
            del c["properties"]
        # cyclonedx-py's description quotes the requirement line and its line number.
        if str(c.get("description", "")).startswith("requirements line "):
            del c["description"]

    # Stable bom-refs.
    keys = {}
    for old, c in kept.items():
        sid = syft_of.get(old)
        paths = index.artifacts[sid].paths if (index and sid) else _cdx_paths(c)
        keys[old] = (
            ref_key(
                c.get("purl"),
                c.get("type", ""),
                c.get("name", ""),
                c.get("version", ""),
            ),
            paths,
        )
    new_ref = unique_refs(keys)
    if _cdx_already_normalized(doc):
        # Re-normalising keeps the stable refs (idempotence).
        new_ref = {old: old for old in kept}
    for old, c in kept.items():
        c["bom-ref"] = new_ref[old]

    md = doc.setdefault("metadata", {})
    old_root = (md.get("component") or {}).get("bom-ref")
    root_ref = f"{opts.root_name}@{opts.version}"

    def resolve(ref: str) -> str | None:
        if ref == old_root:
            return root_ref
        if ref in redirect:
            # Edges of merged duplicates are dropped, not moved: syft resolves a
            # python requirement to one of several same-named dists at random (e.g.
            # tomli in site-packages vs. setuptools/_vendor). Conda packages get
            # their edges from conda-meta below instead.
            return None
        return new_ref.get(ref)

    deps: dict[str, set[str]] = {}
    for d in doc.get("dependencies", []):
        src = resolve(d["ref"])
        if src is None:
            continue
        targets = deps.setdefault(src, set())
        for t in d.get("dependsOn", []):
            dst = resolve(t)
            if dst is not None and dst != src:
                targets.add(dst)
    if index:
        for a, bs in index.conda_deps.items():
            if a not in conda_ref or conda_ref[a] not in new_ref:
                continue
            src = new_ref[conda_ref[a]]
            for b in bs:
                if b in conda_ref and conda_ref[b] in new_ref:
                    deps.setdefault(src, set()).add(new_ref[conda_ref[b]])
    if root_ref not in deps:
        # The root depends on (contains) every component of the document.
        deps[root_ref] = set(new_ref.values())
    doc["dependencies"] = [
        {"ref": ref, "dependsOn": sorted(t)} if t else {"ref": ref}
        for ref, t in sorted(deps.items())
    ]
    for ref in set(new_ref.values()) - set(deps):
        doc["dependencies"].append({"ref": ref})

    root: Json = {
        "bom-ref": root_ref,
        "type": opts.root_type,
        "name": opts.root_name,
        "version": opts.version,
        "supplier": opts.project_supplier.cdx(),
    }
    if opts.root_license:
        root["licenses"] = [{"expression": opts.root_license}]
    if opts.description:
        root["description"] = opts.description
    md["component"] = root
    md["timestamp"] = sbomlib.iso_utc(opts.epoch)
    md["supplier"] = opts.project_supplier.cdx()
    props = [
        p for p in md.get("properties", []) if not p["name"].startswith("syft:image:")
    ]
    props = [p for p in props if p["name"] != "cdx:reproducible"]
    if props:
        md["properties"] = props
    else:
        md.pop("properties", None)
    tools = md.setdefault("tools", {}).setdefault("components", [])
    mine = {"type": "application", "group": "eemt", "name": NORMALIZER}
    if mine not in tools:
        tools.append(mine)
    for t in tools:
        # cyclonedx-py lists its own project URLs; keep only name and version.
        t.pop("externalReferences", None)
        t.pop("description", None)
        t.pop("licenses", None)

    doc["components"] = list(kept.values())
    doc["bomFormat"] = "CycloneDX"
    doc["specVersion"] = sbomlib.CDX_SPEC
    doc["$schema"] = f"http://cyclonedx.org/schema/bom-{sbomlib.CDX_SPEC}.schema.json"
    doc["version"] = 1
    doc.pop("serialNumber", None)
    doc["serialNumber"] = f"urn:uuid:{sbomlib.content_uuid(doc, ('serialNumber',))}"
    return sbomlib.canon(doc)


# --- SPDX -----------------------------------------------------------------------


def _spdx_purl(pkg: Json) -> str | None:
    for r in pkg.get("externalRefs", []):
        if r.get("referenceType") == "purl":
            return r["referenceLocator"]
    return None


def _spdx_set_purl(pkg: Json, purl: str) -> None:
    refs = [r for r in pkg.get("externalRefs", []) if r.get("referenceType") != "purl"]
    refs.append(
        {
            "referenceCategory": "PACKAGE-MANAGER",
            "referenceType": "purl",
            "referenceLocator": purl,
        }
    )
    pkg["externalRefs"] = refs


def _spdx_ctype(pkg: Json, art: Artifact | None) -> str:
    if art is not None:
        return art.type
    purl = _spdx_purl(pkg)
    p = sbomlib.parse_purl(purl) if purl else None
    if p is not None:
        return p.type
    return str(pkg.get("primaryPackagePurpose", "package")).lower()


SPDX_ALG = {"SHA-256": "SHA256", "MD5": "MD5", "SHA-1": "SHA1", "SHA-512": "SHA512"}


def _spdxid(key: str, name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9.\-]+", "-", name).strip("-")[:40] or "pkg"
    return f"SPDXRef-Pkg-{slug}-{sbomlib.sha256_hex(key)[:20]}"


def declare_license_refs(doc: Json) -> None:
    """Every LicenseRef used must be declared in hasExtractedLicensingInfos."""
    used: set[str] = set()
    for pkg in doc.get("packages", []):
        for k in ("licenseDeclared", "licenseConcluded"):
            used.update(sbomlib.LICENSE_REF.findall(str(pkg.get(k, ""))))
        for v in pkg.get("licenseInfoFromFiles", []):
            used.update(sbomlib.LICENSE_REF.findall(str(v)))
    have = {e["licenseId"]: e for e in doc.get("hasExtractedLicensingInfos", [])}
    out = []
    for ref in sorted(used):
        entry = have.get(ref)
        if entry is None:
            entry = {
                "licenseId": ref,
                "name": ref[len("LicenseRef-") :],
                "extractedText": (
                    "The package metadata names the licence "
                    f"'{ref[len('LicenseRef-') :]}', "
                    "which is not an SPDX License List identifier. Its text is not "
                    "included in this document."
                ),
            }
        out.append(entry)
    if out:
        doc["hasExtractedLicensingInfos"] = out
    else:
        doc.pop("hasExtractedLicensingInfos", None)


def normalize_spdx(doc: Json, opts: Options) -> Json:
    index = opts.syft
    describes = {
        r["relatedSpdxElement"]
        for r in doc.get("relationships", [])
        if r.get("spdxElementId") == "SPDXRef-DOCUMENT"
        and r.get("relationshipType") == "DESCRIBES"
    } | set(doc.get("documentDescribes", []))
    root_ids = {
        p["SPDXID"]
        for p in doc["packages"]
        if p["SPDXID"] in describes or p["SPDXID"] == "SPDXRef-DocumentRoot"
    }
    if len(root_ids) != 1:
        raise SystemExit(
            f"normalize: expected one described root package, found {sorted(root_ids)}"
        )
    old_root = root_ids.pop()

    # syft writes the image's operating system as a CycloneDX component but not as an
    # SPDX package; add it from the same scan so that both documents list it.
    if (
        index
        and index.distro.get("id")
        and not any(
            p.get("primaryPackagePurpose") == "OPERATING-SYSTEM"
            for p in doc["packages"]
        )
    ):
        d = index.distro
        os_pkg: Json = {
            "SPDXID": "SPDXRef-OperatingSystem",
            "name": d["id"],
            "versionInfo": d.get("versionID", ""),
            "supplier": "NOASSERTION",
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "copyrightText": "NOASSERTION",
            "primaryPackagePurpose": "OPERATING-SYSTEM",
        }
        if d.get("prettyName"):
            os_pkg["description"] = d["prettyName"]
        doc["packages"].append(os_pkg)
        doc.setdefault("relationships", []).append(
            {
                "spdxElementId": old_root,
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": "SPDXRef-OperatingSystem",
            }
        )

    pkgs: dict[str, Json] = {}
    art_of: dict[str, Artifact] = {}
    for p in doc["packages"]:
        pkgs[p["SPDXID"]] = p
        m = SPDX_SYFT_ID.search(p["SPDXID"])
        if index and m and m.group(1) in index.artifacts:
            art_of[p["SPDXID"]] = index.artifacts[m.group(1)]

    conda_pkg: dict[str, str] = {}
    for sid, p in pkgs.items():
        if sid == old_root:
            continue
        purl = _spdx_purl(p)
        if purl:
            _spdx_set_purl(p, sbomlib.strip_qualifier(purl, "package-id"))
        art = art_of.get(sid)
        if index and art and art.conda is not None:
            purl, hashes, url = index.conda_identity(art)
            _spdx_set_purl(p, purl)
            p["checksums"] = [
                {"algorithm": SPDX_ALG[h["alg"]], "checksumValue": h["content"]}
                for h in hashes
            ]
            if url:
                p["downloadLocation"] = url
            conda_pkg[art.id] = sid
        if not opts.keep_files:
            p["filesAnalyzed"] = False
            p.pop("packageVerificationCode", None)
            p.pop("licenseInfoFromFiles", None)
            p.pop("hasFiles", None)

    redirect: dict[str, str] = {}
    if index:
        for sid, art in art_of.items():
            owner = index.owner.get(art.id)
            if owner and owner in conda_pkg and sid != old_root:
                p = pkgs[sid]
                parent = pkgs[conda_pkg[owner]]
                note = ref_key(
                    _spdx_purl(p), art.type, p["name"], p.get("versionInfo", "")
                )
                merged = sorted(
                    set(
                        parent.get("comment", "")
                        .removeprefix("Also catalogued as: ")
                        .split("; ")
                    )
                    - {""}
                    | {note}
                )
                parent["comment"] = "Also catalogued as: " + "; ".join(merged)
                redirect[sid] = conda_pkg[owner]

    kept = {
        sid: p for sid, p in pkgs.items() if sid not in redirect and sid != old_root
    }
    keys = {}
    for sid, p in kept.items():
        art = art_of.get(sid)
        purl = _spdx_purl(p)
        ctype = _spdx_ctype(p, art)
        paths = art.paths if art else []
        sup = supplier_for(
            opts,
            purl,
            "operating-system"
            if p.get("primaryPackagePurpose") == "OPERATING-SYSTEM"
            else ctype,
            p["name"],
            paths,
        )
        if sup is not None:
            current = p.get("supplier", "NOASSERTION")
            if current not in ("NOASSERTION", sup.spdx()) and "originator" not in p:
                p["originator"] = current
            p["supplier"] = sup.spdx()
        keys[sid] = (ref_key(purl, ctype, p["name"], p.get("versionInfo", "")), paths)
    if f"Tool: {NORMALIZER}" in doc.get("creationInfo", {}).get("creators", []):
        # Re-normalising keeps the stable ids (idempotence); file locations, which
        # disambiguate same-named packages, are only known from the syft scan.
        new_id = {sid: sid for sid in keys}
    else:
        new_ref = unique_refs(keys)
        new_id = {sid: _spdxid(key, pkgs[sid]["name"]) for sid, key in new_ref.items()}
    if len(set(new_id.values())) != len(new_id):
        raise SystemExit("normalize: SPDXID collision")
    root_id = "SPDXRef-DocumentRoot"
    new_id[old_root] = root_id
    for sid, p in kept.items():
        p["SPDXID"] = new_id[sid]

    root = pkgs[old_root]
    for k in (
        "checksums",
        "externalRefs",
        "packageVerificationCode",
        "hasFiles",
        "licenseInfoFromFiles",
        "sourceInfo",
    ):
        root.pop(k, None)
    root.update(
        {
            "SPDXID": root_id,
            "name": opts.root_name,
            "versionInfo": opts.version,
            "supplier": opts.project_supplier.spdx(),
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseDeclared": opts.root_license or "NOASSERTION",
            "licenseConcluded": "NOASSERTION",
            "copyrightText": "NOASSERTION",
            "primaryPackagePurpose": "CONTAINER"
            if opts.root_type == "container"
            else "APPLICATION",
        }
    )
    if opts.description:
        root["description"] = opts.description

    def resolve(ref: str) -> str | None:
        if (
            ref == "SPDXRef-DOCUMENT"
            or ref.startswith("DocumentRef-")
            or ref in ("NONE", "NOASSERTION")
        ):
            return ref
        if ref in redirect:
            return None  # edges of merged duplicates are dropped (see normalize_cdx)
        return new_id.get(ref)

    rels: set[tuple[str, str, str]] = set()
    for r in doc.get("relationships", []):
        a, b = resolve(r["spdxElementId"]), resolve(r["relatedSpdxElement"])
        if a is None or b is None or a == b:
            continue
        rels.add((a, r["relationshipType"], b))
    if index:
        # conda-meta "depends": B DEPENDENCY_OF A (syft's direction for SPDX).
        for dependent, needs in index.conda_deps.items():
            if dependent not in conda_pkg or conda_pkg[dependent] not in new_id:
                continue
            for dep in needs:
                if dep in conda_pkg and conda_pkg[dep] in new_id:
                    rels.add(
                        (
                            new_id[conda_pkg[dep]],
                            "DEPENDENCY_OF",
                            new_id[conda_pkg[dependent]],
                        )
                    )
    rels.add(("SPDXRef-DOCUMENT", "DESCRIBES", root_id))
    if not any(a == root_id and t == "CONTAINS" for a, t, _ in rels):
        for sid in kept:
            rels.add((root_id, "CONTAINS", new_id[sid]))
    doc["relationships"] = [
        {"spdxElementId": a, "relationshipType": t, "relatedSpdxElement": b}
        for a, t, b in sorted(rels)
    ]
    doc["packages"] = [root, *kept.values()]
    if not opts.keep_files:
        doc.pop("files", None)
    doc.pop("documentDescribes", None)
    declare_license_refs(doc)

    ci = doc.setdefault("creationInfo", {})
    ci["created"] = sbomlib.iso_utc(opts.epoch)
    creators = {c for c in ci.get("creators", []) if c.startswith("Tool: ")}
    creators |= {opts.project_supplier.spdx(), f"Tool: {NORMALIZER}"}
    ci["creators"] = sorted(creators)
    doc["spdxVersion"] = sbomlib.SPDX_SPEC
    doc["dataLicense"] = "CC0-1.0"
    doc["SPDXID"] = "SPDXRef-DOCUMENT"
    doc["name"] = opts.name
    doc.pop("documentNamespace", None)
    doc["documentNamespace"] = (
        f"{opts.namespace}/{opts.name}-"
        f"{sbomlib.content_uuid(doc, ('documentNamespace',))}"
    )
    return sbomlib.canon(doc)


# --- entry point ----------------------------------------------------------------


def normalize(doc: Json, opts: Options) -> Json:
    if doc.get("bomFormat") == "CycloneDX":
        return normalize_cdx(doc, opts)
    if str(doc.get("spdxVersion", "")).startswith("SPDX-2"):
        return normalize_spdx(doc, opts)
    raise SystemExit("normalize: neither CycloneDX nor SPDX 2.x JSON")


def options_from_config(
    config: sbomlib.Config,
    target: str,
    version: str,
    epoch: int,
    syft: SyftIndex | None,
    keep_files: bool = False,
) -> Options:
    t = config.targets[target]
    return Options(
        name=t.name,
        root_name=t.root_name,
        version=version,
        epoch=epoch,
        root_type=t.root_type,
        root_license=t.root_license,
        description=t.description,
        project_supplier=config.project_supplier,
        namespace=config.namespace,
        path_suppliers=t.path_suppliers,
        syft=syft,
        keep_files=keep_files,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--epoch-file", type=Path, required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--syft-json", type=Path)
    ap.add_argument("--keep-files", action="store_true")
    args = ap.parse_args(argv)

    config = sbomlib.load_config(args.config)
    syft = SyftIndex.load(args.syft_json) if args.syft_json else None
    opts = options_from_config(
        config,
        args.target,
        args.version,
        sbomlib.read_epoch(args.epoch_file),
        syft,
        args.keep_files,
    )
    with args.input.open(encoding="utf-8") as fh:
        doc = json.load(fh)
    out = normalize(doc, opts)
    args.output.write_text(sbomlib.dumps(out), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
