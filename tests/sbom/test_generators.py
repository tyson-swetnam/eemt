# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
"""scripts/sbom/{lock2cdx,cdx2spdx,aggregate,crosscheck,secret_scan}.py."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import aggregate
import cdx2spdx
import crosscheck
import lock2cdx
import normalize
import sbomlib
import secret_scan

CONDA_LOCK = """\
version: 1
metadata:
  platforms: [linux-64]
package:
- name: zlib
  version: 1.3.1
  manager: conda
  platform: linux-64
  dependencies:
    libzlib: 1.3.1
  url: https://conda.anaconda.org/conda-forge/linux-64/zlib-1.3.1-hb9d3cd8_2.conda
  hash:
    md5: {md5}
    sha256: {sha_a}
- name: libzlib
  version: 1.3.1
  manager: conda
  platform: linux-64
  dependencies: {{}}
  url: https://conda.anaconda.org/conda-forge/linux-64/libzlib-1.3.1-hb9d3cd8_2.tar.bz2
  hash:
    md5: {md5}
    sha256: {sha_b}
- name: zlib
  version: 1.3.1
  manager: conda
  platform: osx-arm64
  dependencies: {{}}
  url: https://conda.anaconda.org/conda-forge/osx-arm64/zlib-1.3.1-h8359307_2.conda
  hash:
    md5: {md5}
    sha256: {sha_b}
- name: Some_Pkg
  version: 2.0.0
  manager: pip
  platform: linux-64
  dependencies:
    zlib: '*'
  url: https://files.pythonhosted.org/packages/x/some_pkg-2.0.0-py3-none-any.whl
  hash:
    sha256: {sha_c}
""".format(md5="0" * 32, sha_a="a" * 64, sha_b="b" * 64, sha_c="c" * 64)

CARGO_LOCK = """\
version = 4

[[package]]
name = "rsun-core"
version = "0.1.0"
dependencies = [
 "libm",
 "num-traits 0.2.19",
]

[[package]]
name = "libm"
version = "0.2.16"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "{a}"

[[package]]
name = "num-traits"
version = "0.2.19"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "{b}"
dependencies = [
 "libm",
]
""".format(a="a" * 64, b="b" * 64)

CARGO_METADATA = {
    "packages": [
        {
            "name": "rsun-core",
            "version": "0.1.0",
            "source": None,
            "license": "GPL-2.0-or-later",
            "description": "core",
        },
        {
            "name": "libm",
            "version": "0.2.16",
            "source": "registry+https://github.com/rust-lang/crates.io-index",
            "license": "MIT/Apache-2.0",
        },
        {
            "name": "num-traits",
            "version": "0.2.19",
            "source": "registry+https://github.com/rust-lang/crates.io-index",
            "license": "MIT OR Apache-2.0",
            "repository": "https://github.com/rust-num/num-traits",
        },
    ]
}

PROJECT = sbomlib.Supplier("EEMT project", "https://github.com/tyson-swetnam/eemt")


def opts(
    name: str = "env-test", root_license: str = "BSD-3-Clause"
) -> normalize.Options:
    return normalize.Options(
        name=name,
        root_name=name,
        version="3.0.0",
        epoch=0,
        root_type="application",
        root_license=root_license,
        description="",
        project_supplier=PROJECT,
        namespace="https://github.com/tyson-swetnam/eemt/sbom",
    )


def finish(
    raw: dict, name: str = "env-test", root_license: str = "BSD-3-Clause"
) -> tuple[dict, dict]:
    """The generate.sh pipeline for a lock-derived document."""
    cdx = normalize.normalize(raw, opts(name, root_license))
    spdx = normalize.normalize(
        cdx2spdx.convert(json.loads(sbomlib.dumps(cdx))), opts(name, root_license)
    )
    return cdx, spdx


def spdx_root(spdx: dict) -> dict:
    return next(p for p in spdx["packages"] if p["SPDXID"] == "SPDXRef-DocumentRoot")


# --- root licence -----------------------------------------------------------------


def test_root_without_licence_declares_none(tmp_path: Path):
    """An image or environment root bundles other licences: no root licence claimed."""
    lock = tmp_path / "x-lock.yml"
    lock.write_text(CONDA_LOCK)
    cdx, spdx = finish(lock2cdx.conda_bom(lock, "linux-64", []), root_license="")
    assert "licenses" not in cdx["metadata"]["component"]
    assert spdx_root(spdx)["licenseDeclared"] == "NOASSERTION"
    cdx, spdx = finish(lock2cdx.conda_bom(lock, "linux-64", []), root_license="MIT")
    assert cdx["metadata"]["component"]["licenses"] == [{"expression": "MIT"}]
    assert spdx_root(spdx)["licenseDeclared"] == "MIT"


def test_bundle_targets_declare_no_root_licence():
    """scripts/sbom/targets.toml: images, environments and the aggregate bundle
    third-party licences (GRASS GPL-2.0-or-later, CCTools GPL-2.0-only, GEOS
    LGPL-2.1-only, ...), so they must not declare a project licence for the whole."""
    root = Path(__file__).resolve().parents[2]
    config = sbomlib.load_config(root / "scripts" / "sbom" / "targets.toml")
    bundles = {
        t.name: t.root_license
        for t in config.targets.values()
        if t.kind in ("image", "aggregate", "conda", "pip")
    }
    assert bundles and all(v == "" for v in bundles.values()), bundles
    assert config.targets["cargo-rsun"].root_license == "GPL-2.0-or-later"


# --- conda ------------------------------------------------------------------------


def test_conda_lock_components(tmp_path: Path):
    lock = tmp_path / "x-lock.yml"
    lock.write_text(CONDA_LOCK)
    lic_doc = tmp_path / "image.cdx.json"
    zlib_purl = "pkg:conda/zlib@1.3.1?build=hb9d3cd8_2&channel=conda-forge&subdir=linux-64&type=conda"  # noqa: E501
    lic_doc.write_text(
        json.dumps(
            {
                "components": [
                    {"purl": zlib_purl, "licenses": [{"license": {"id": "Zlib"}}]}
                ]
            }
        )
    )
    raw = lock2cdx.conda_bom(lock, "linux-64", [lic_doc])
    by = {c["name"]: c for c in raw["components"]}
    assert set(by) == {"zlib", "libzlib", "Some_Pkg"}  # osx-arm64 entry excluded
    assert by["zlib"]["purl"] == zlib_purl
    assert by["libzlib"]["purl"].endswith("&type=tar.bz2")
    assert by["zlib"]["licenses"] == [{"license": {"id": "Zlib"}}]
    assert "licenses" not in by["libzlib"]  # no exact-purl match: no licence invented
    assert by["Some_Pkg"]["purl"] == "pkg:pypi/some-pkg@2.0.0"
    assert {"alg": "SHA-256", "content": "a" * 64} in by["zlib"]["hashes"]
    deps = {d["ref"]: d.get("dependsOn", []) for d in raw["dependencies"]}
    assert deps[zlib_purl] == [by["libzlib"]["purl"]]
    assert deps[by["Some_Pkg"]["purl"]] == [zlib_purl]

    cdx, spdx = finish(raw)
    sup = {c["name"]: c["supplier"]["name"] for c in cdx["components"]}
    assert sup == {
        "zlib": "conda-forge",
        "libzlib": "conda-forge",
        "Some_Pkg": "Python Package Index (PyPI)",
    }
    sp = {p["name"]: p for p in spdx["packages"]}
    assert {"algorithm": "SHA256", "checksumValue": "a" * 64} in sp["zlib"]["checksums"]
    assert sp["zlib"]["licenseDeclared"] == "Zlib"
    assert sp["zlib"]["downloadLocation"].endswith("zlib-1.3.1-hb9d3cd8_2.conda")
    assert crosscheck.purls_cdx(cdx) == crosscheck.purls_spdx(spdx)
    rels = {
        (r["spdxElementId"], r["relationshipType"], r["relatedSpdxElement"])
        for r in spdx["relationships"]
    }
    assert (sp["zlib"]["SPDXID"], "DEPENDS_ON", sp["libzlib"]["SPDXID"]) in rels


def test_crosscheck_reads_lock_and_sbom_alike(tmp_path: Path):
    lock = tmp_path / "x-lock.yml"
    lock.write_text(CONDA_LOCK)
    cdx, _ = finish(lock2cdx.conda_bom(lock, "linux-64", []))
    assert crosscheck.conda_set_from_lock(lock) == crosscheck.conda_set_from_sbom(cdx)


# --- cargo ------------------------------------------------------------------------


def test_cargo_lock_with_metadata(tmp_path: Path):
    lock = tmp_path / "Cargo.lock"
    lock.write_text(CARGO_LOCK)
    meta = tmp_path / "metadata.json"
    meta.write_text(json.dumps(CARGO_METADATA))
    raw = lock2cdx.cargo_bom(lock, meta, PROJECT)
    by = {c["name"]: c for c in raw["components"]}
    assert by["libm"]["purl"] == "pkg:cargo/libm@0.2.16"
    assert by["libm"]["hashes"] == [{"alg": "SHA-256", "content": "a" * 64}]
    assert by["libm"]["licenses"] == [
        {"expression": "MIT OR Apache-2.0"}
    ]  # legacy "/" syntax
    assert (
        "purl" not in by["rsun-core"]
        and by["rsun-core"]["supplier"]["name"] == "EEMT project"
    )
    deps = {d["ref"]: d.get("dependsOn", []) for d in raw["dependencies"]}
    assert deps["crate:rsun-core@0.1.0"] == [
        "pkg:cargo/libm@0.2.16",
        "pkg:cargo/num-traits@0.2.19",
    ]

    cdx, spdx = finish(raw, "cargo-test")
    sup = {c["name"]: c["supplier"]["name"] for c in cdx["components"]}
    assert sup == {
        "libm": "crates.io",
        "num-traits": "crates.io",
        "rsun-core": "EEMT project",
    }
    sp = {p["name"]: p for p in spdx["packages"]}
    assert sp["num-traits"]["licenseDeclared"] == "MIT OR Apache-2.0"
    assert sp["rsun-core"]["licenseDeclared"] == "GPL-2.0-or-later"


# --- lean -------------------------------------------------------------------------


def test_lean_manifest(tmp_path: Path):
    manifest = tmp_path / "lake-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": "1.3.0",
                "packages": [
                    {
                        "type": "git",
                        "url": "https://github.com/leanprover-community/mathlib4",
                        "rev": "56a005855ea17e0b0958f73ec801ae140b0a1679",
                        "name": "mathlib",
                        "inputRev": "v4.35.0",
                        "inherited": False,
                    },
                ],
            }
        )
    )
    tc = tmp_path / "lean-toolchain"
    tc.write_text("leanprover/lean4:v4.35.0\n")
    cdx, spdx = finish(lock2cdx.lean_bom(manifest, tc), "lean-test")
    by = {c["name"]: c for c in cdx["components"]}
    assert (
        by["mathlib"]["purl"]
        == "pkg:github/leanprover-community/mathlib4@56a005855ea17e0b0958f73ec801ae140b0a1679"  # noqa: E501
    )
    assert by["mathlib"]["supplier"]["name"] == "leanprover-community (GitHub)"
    assert by["lean4"]["version"] == "v4.35.0"
    assert len(spdx["packages"]) == 3


# --- pip / cdx2spdx -----------------------------------------------------------------


def test_pip_distribution_hashes_survive_conversion():
    raw = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "version": 1,
        "metadata": {
            "tools": {"components": [{"name": "cyclonedx-py", "version": "8.0.1"}]}
        },
        "components": [
            {
                "bom-ref": "requirements-L3",
                "type": "library",
                "name": "jinja2",
                "version": "3.1.6",
                "purl": "pkg:pypi/jinja2@3.1.6",
                "description": "requirements line 3: jinja2==3.1.6",
                "externalReferences": [
                    {
                        "type": "distribution",
                        "url": "https://pypi.org/simple/jinja2/",
                        "hashes": [
                            {"alg": "SHA-256", "content": "f" * 64},
                            {"alg": "SHA-256", "content": "e" * 64},
                        ],
                    }
                ],
                "licenses": [{"license": {"name": "BSD License"}}],
            }
        ],
        "dependencies": [{"ref": "requirements-L3"}],
    }
    cdx, spdx = finish(raw, "env-pip-test")
    c = cdx["components"][0]
    assert c["bom-ref"] == "pkg:pypi/jinja2@3.1.6" and "description" not in c
    p = next(p for p in spdx["packages"] if p["name"] == "jinja2")
    assert "e" * 64 in p["comment"] and "f" * 64 in p["comment"]
    assert p["downloadLocation"] == "NOASSERTION"  # an index page is not a download
    assert p["licenseDeclared"] == "LicenseRef-BSD-License"
    assert "LicenseRef-BSD-License" in {
        e["licenseId"] for e in spdx["hasExtractedLicensingInfos"]
    }
    assert "Tool: cyclonedx-py-8.0.1" in spdx["creationInfo"]["creators"]


def test_license_expression_joins_entries():
    assert cdx2spdx.license_expression([]) == "NOASSERTION"
    assert (
        cdx2spdx.license_expression(
            [{"license": {"id": "MIT"}}, {"expression": "Apache-2.0 OR BSD-3-Clause"}]
        )
        == "MIT AND (Apache-2.0 OR BSD-3-Clause)"
    )


# --- aggregate --------------------------------------------------------------------


def test_aggregate_references_members(tmp_path: Path):
    lock = tmp_path / "x-lock.yml"
    lock.write_text(CONDA_LOCK)
    sbom = tmp_path / "sbom"
    sbom.mkdir()
    cfg = tmp_path / "targets.toml"
    cfg.write_text("""
project = "eemt"
namespace = "https://github.com/tyson-swetnam/eemt/sbom"
supplier = { name = "EEMT project" }
[[target]]
name = "eemt"
kind = "aggregate"
root_type = "application"
description = "agg"
[[target]]
name = "env-a"
kind = "conda"
inputs = ["x-lock.yml"]
root_type = "application"
root_license = "BSD-3-Clause"
description = "a"
[[target]]
name = "env-b"
kind = "conda"
parent = "env-a"
inputs = ["x-lock.yml"]
root_type = "application"
root_license = "BSD-3-Clause"
description = "b"
""")
    for name in ("env-a", "env-b"):
        cdx, spdx = finish(lock2cdx.conda_bom(lock, "linux-64", []), name)
        (sbom / f"{name}.cdx.json").write_text(sbomlib.dumps(cdx))
        (sbom / f"{name}.spdx.json").write_text(sbomlib.dumps(spdx))
    config = sbomlib.load_config(cfg)
    agg = config.targets["eemt"]
    ms = aggregate.members(config, sbom)
    a_cdx = aggregate.aggregate_cdx(config, agg, ms, sbom, "3.0.0", 0)
    a_spdx = aggregate.aggregate_spdx(config, agg, ms, sbom, "3.0.0", 0)
    # No root_license in the aggregate's table: no licence claimed for the release.
    assert "licenses" not in a_cdx["metadata"]["component"]
    assert spdx_root(a_spdx)["licenseDeclared"] == "NOASSERTION"
    (sbom / "eemt.cdx.json").write_text(sbomlib.dumps(a_cdx))
    (sbom / "eemt.spdx.json").write_text(sbomlib.dumps(a_spdx))
    # Hierarchy: env-b is nested in env-a.
    assert [c["bom-ref"] for c in a_cdx["components"]] == ["env-a"]
    assert [c["bom-ref"] for c in a_cdx["components"][0]["components"]] == ["env-b"]
    r = crosscheck.Report()
    crosscheck.check_aggregate(r, sbom, ["env-a", "env-b"])
    assert not r.failures
    # Any change to a member breaks the reference.
    (sbom / "env-b.cdx.json").write_text(sbomlib.dumps(cdx) + " ")
    r = crosscheck.Report()
    crosscheck.check_aggregate(r, sbom, ["env-a", "env-b"])
    assert r.failures


# --- secret scan --------------------------------------------------------------------


def _tar(files: dict[str, bytes]) -> io.BytesIO:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf


def test_secret_scan_flags_credentials_state_and_keys():
    key = (
        b"-----BEGIN OPENSSH PRIVATE KEY-----\n"
        + b"QUFB" * 30
        + b"\n-----END OPENSSH PRIVATE KEY-----\n"
    )
    findings, files = secret_scan.scan(
        _tar(
            {
                "opt/eemt/sol/.eemt-makeflow-password": b"x",
                "app/jobs.db": b"x",
                "app/uploads/dem.tif": b"x",
                "root/.netrc": b"x",
                "home/u/.docker/config.json": b"{}",
                "etc/key.pem": key,
                "usr/lib/libssh2.so": b"-----BEGIN OPENSSH PRIVATE KEY-----",  # header only: a parser  # noqa: E501
                "app/app.py": b"print('ok')",
            }
        )
    )
    assert files == 8
    flagged = {f.split(":")[0] for f in findings}
    assert flagged == {
        "/opt/eemt/sol/.eemt-makeflow-password",
        "/app/jobs.db",
        "/app/uploads/dem.tif",
        "/root/.netrc",
        "/home/u/.docker/config.json",
        "/etc/key.pem",
    }


def test_secret_scan_clean_tree():
    findings, _ = secret_scan.scan(
        _tar({"app/uploads/.keep": b"", "opt/eemt/env/share/proj/proj.db": b"x"})
    )
    # An empty uploads/ marker is still a file in a job-data directory; proj.db is fine.
    assert findings == [
        "/app/uploads/.keep: file inside a job-data directory (uploads/ or results/)"
    ]
