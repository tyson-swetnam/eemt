# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
"""scripts/sbom/normalize.py: idempotence, ordering, duplicate merging, stripping."""

from __future__ import annotations

import copy
import json
import random
from typing import Any

import normalize
import sbomlib


def run(doc: dict[str, Any], opts: normalize.Options) -> str:
    return sbomlib.dumps(normalize.normalize(copy.deepcopy(doc), opts))


def comps(text: str) -> list[dict[str, Any]]:
    return json.loads(text)["components"]


def pkgs(text: str) -> list[dict[str, Any]]:
    return [
        p for p in json.loads(text)["packages"] if p["SPDXID"] != "SPDXRef-DocumentRoot"
    ]


# --- determinism ----------------------------------------------------------------


def test_cdx_idempotent(scan1, opts_factory):
    once = run(scan1["cdx"], opts_factory(scan1["syft"]))
    twice = run(json.loads(once), opts_factory(None))
    assert once == twice
    assert run(json.loads(once), opts_factory(scan1["syft"])) == once


def test_spdx_idempotent(scan1, opts_factory):
    once = run(scan1["spdx"], opts_factory(scan1["syft"]))
    assert run(json.loads(once), opts_factory(None)) == once


def test_rescan_with_new_ids_serials_times_labels_is_identical(
    scan1, scan2, opts_factory
):
    for fmt in ("cdx", "spdx"):
        a = run(scan1[fmt], opts_factory(scan1["syft"]))
        b = run(scan2[fmt], opts_factory(scan2["syft"]))
        assert a == b, fmt


def test_component_order_does_not_matter(scan1, opts_factory):
    ref = run(scan1["cdx"], opts_factory(scan1["syft"]))
    rng = random.Random(7)
    for _ in range(5):
        doc = copy.deepcopy(scan1["cdx"])
        rng.shuffle(doc["components"])
        for c in doc["components"]:
            rng.shuffle(c.get("properties", []))
        rng.shuffle(doc["dependencies"])
        assert run(doc, opts_factory(scan1["syft"])) == ref
    spdx_ref = run(scan1["spdx"], opts_factory(scan1["syft"]))
    doc = copy.deepcopy(scan1["spdx"])
    doc["packages"].reverse()
    doc["relationships"].reverse()
    assert run(doc, opts_factory(scan1["syft"])) == spdx_ref


def test_output_is_sorted_and_newline_terminated(scan1, opts_factory):
    text = run(scan1["cdx"], opts_factory(scan1["syft"]))
    assert text.endswith("}\n")
    data = json.loads(text)
    assert data == sbomlib.canon(data)  # every array already in canonical order
    assert text == json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def test_epoch_and_content_derived_ids(scan1, opts_factory):
    opts = opts_factory(scan1["syft"])
    cdx = json.loads(run(scan1["cdx"], opts))
    assert cdx["metadata"]["timestamp"] == "2026-11-15T15:39:39Z"
    assert cdx["serialNumber"].startswith("urn:uuid:")
    assert cdx["serialNumber"] != scan1["cdx"]["serialNumber"]
    spdx = json.loads(run(scan1["spdx"], opts))
    assert spdx["creationInfo"]["created"] == "2026-11-15T15:39:39Z"
    assert spdx["documentNamespace"].startswith(
        "https://github.com/tyson-swetnam/eemt/sbom/image-test-"
    )
    # A content change changes the serial number.
    other = copy.deepcopy(scan1["cdx"])
    other["components"][2]["version"] = "2.44"
    assert json.loads(run(other, opts))["serialNumber"] != cdx["serialNumber"]


# --- stripping ------------------------------------------------------------------


def test_labels_layers_package_ids_files_and_image_id_removed(scan1, opts_factory):
    for fmt in ("cdx", "spdx"):
        text = run(scan1[fmt], opts_factory(scan1["syft"]))
        for needle in (
            "syft:image:labels",
            "someone@example.org",
            "layerID",
            "package-id=",
            "/etc/passwd",
            "c" * 64,
            "/s/img.tar",
            "Anchore, Inc",
        ):
            assert needle not in text, (fmt, needle)
    cdx = json.loads(run(scan1["cdx"], opts_factory(scan1["syft"])))
    assert all(c["type"] != "file" for c in cdx["components"])
    root = cdx["metadata"]["component"]
    assert (root["name"], root["version"], root["bom-ref"]) == (
        "test",
        "3.0.0",
        "test@3.0.0",
    )


# --- merging and enrichment -------------------------------------------------------


def test_python_twin_of_conda_package_is_merged(scan1, opts_factory):
    cdx = comps(run(scan1["cdx"], opts_factory(scan1["syft"])))
    assert [c["name"] for c in cdx].count("numpy") == 1
    numpy = next(c for c in cdx if c["name"] == "numpy")
    assert (
        numpy["purl"]
        == "pkg:conda/numpy@2.5.4?build=py314h_0&channel=conda-forge&subdir=linux-64&type=conda"  # noqa: E501
    )
    assert {"alg": "SHA-256", "content": "1" * 64} in numpy["hashes"]
    assert {"alg": "MD5", "content": "2" * 32} in numpy["hashes"]
    assert {"name": normalize.MERGED_PROP, "value": "pkg:pypi/numpy@2.5.4"} in numpy[
        "properties"
    ]
    spdx = pkgs(run(scan1["spdx"], opts_factory(scan1["syft"])))
    assert [p["name"] for p in spdx].count("numpy") == 1
    sp = next(p for p in spdx if p["name"] == "numpy")
    assert sp["downloadLocation"].endswith("numpy-2.5.4-py314h_0.conda")
    assert sp["comment"] == "Also catalogued as: pkg:pypi/numpy@2.5.4"


def test_dependencies_of_merged_component_are_dropped(scan1, opts_factory):
    # syft attaches python requirements to one of several same-named dists at
    # random, so the merged twin's edge (numpy -> Launcher) must not survive.
    data = json.loads(run(scan1["cdx"], opts_factory(scan1["syft"])))
    conda_ref = next(c["bom-ref"] for c in data["components"] if c["name"] == "numpy")
    libc_ref = next(c["bom-ref"] for c in data["components"] if c["name"] == "libc6")
    deps = {d["ref"]: d.get("dependsOn", []) for d in data["dependencies"]}
    assert deps[conda_ref] == [libc_ref]
    every = {c["bom-ref"] for c in data["components"]} | {"test@3.0.0"}
    assert set(deps) <= every and all(set(v) <= every for v in deps.values())


def test_without_syft_index_nothing_is_merged(scan1, opts_factory):
    cdx = comps(run(scan1["cdx"], opts_factory(None)))
    assert [c["name"] for c in cdx].count("numpy") == 2


def test_same_name_binaries_get_distinct_stable_refs(scan1, scan2, opts_factory):
    a = [
        c["bom-ref"]
        for c in comps(run(scan1["cdx"], opts_factory(scan1["syft"])))
        if c["name"] == "Launcher"
    ]
    b = [
        c["bom-ref"]
        for c in comps(run(scan2["cdx"], opts_factory(scan2["syft"])))
        if c["name"] == "Launcher"
    ]
    assert len(set(a)) == 2 and sorted(a) == sorted(b)


# --- suppliers and licences ---------------------------------------------------------


def test_suppliers_are_distributors_not_the_project(scan1, opts_factory):
    cdx = {c["name"]: c for c in comps(run(scan1["cdx"], opts_factory(scan1["syft"])))}
    assert cdx["numpy"]["supplier"]["name"] == "conda-forge"
    assert cdx["libc6"]["supplier"]["name"] == "Ubuntu (Canonical Ltd.)"
    assert cdx["Launcher"]["supplier"]["name"] == "Base image vendor"  # path rule
    for c in cdx.values():
        assert c["supplier"]["name"] != "EEMT project"
    spdx = {p["name"]: p for p in pkgs(run(scan1["spdx"], opts_factory(scan1["syft"])))}
    assert spdx["libc6"]["supplier"] == "Organization: Ubuntu (Canonical Ltd.)"


def test_pypi_author_moves_to_originator(scan1, opts_factory):
    # Without the index the python twin is not merged, so its syft supplier is visible.
    # (Same-named binaries cannot be told apart without the index: drop them here.)
    doc = copy.deepcopy(scan1["spdx"])
    doc["packages"] = [p for p in doc["packages"] if p["name"] != "Launcher"]
    py = next(p for p in pkgs(run(doc, opts_factory(None))) if p.get("originator"))
    assert py["supplier"] == "Organization: Python Package Index (PyPI)"
    assert py["originator"] == "Person: NumPy developers"


def test_license_refs_are_declared(scan1, opts_factory):
    data = json.loads(run(scan1["spdx"], opts_factory(scan1["syft"])))
    declared = {e["licenseId"] for e in data["hasExtractedLicensingInfos"]}
    assert "LicenseRef-Expat" in declared
    assert all(e["extractedText"] for e in data["hasExtractedLicensingInfos"])


def test_operating_system_package_added_to_spdx(scan1, opts_factory):
    raw = copy.deepcopy(scan1["syft"])
    raw["distro"] = {
        "id": "ubuntu",
        "versionID": "26.04",
        "prettyName": "Ubuntu 26.04 LTS",
    }
    opts = opts_factory(None)
    opts.syft = normalize.SyftIndex.from_syft(raw)
    spdx = pkgs(run(scan1["spdx"], opts))
    os_pkg = next(
        p for p in spdx if p.get("primaryPackagePurpose") == "OPERATING-SYSTEM"
    )
    assert (os_pkg["name"], os_pkg["versionInfo"], os_pkg["supplier"]) == (
        "ubuntu",
        "26.04",
        "Organization: Ubuntu (Canonical Ltd.)",
    )


def test_same_named_packages_without_index_are_an_error(scan1, opts_factory):
    import pytest

    with pytest.raises(SystemExit, match="non-unique component identities"):
        run(scan1["spdx"], opts_factory(None))


def test_conda_meta_depends_become_edges(scan1, opts_factory):
    syft = json.loads(json.dumps(scan1["syft"]))
    conda = syft["artifacts"][0]
    zlib = json.loads(json.dumps(conda))
    zlib.update(id="9999999999999999", name="zlib", version="1.3.1")
    zlib["metadata"] = dict(
        conda["metadata"],
        name="zlib",
        version="1.3.1",
        fn="zlib-1.3.1-h_0.conda",
        files=[],
        depends=[],
    )
    zlib["locations"] = [{"path": "/opt/env/conda-meta/zlib-1.3.1-h_0.json"}]
    conda["metadata"]["depends"] = ["zlib >=1.3", "__glibc >=2.17"]
    syft["artifacts"].append(zlib)
    cdx = json.loads(json.dumps(scan1["cdx"]))
    cdx["components"].append(
        {"bom-ref": zlib["id"], "type": "library", "name": "zlib", "version": "1.3.1"}
    )
    data = json.loads(run(cdx, opts_factory(syft)))
    ref = {c["name"]: c["bom-ref"] for c in data["components"]}
    deps = {d["ref"]: d.get("dependsOn", []) for d in data["dependencies"]}
    assert ref["zlib"] in deps[ref["numpy"]]
