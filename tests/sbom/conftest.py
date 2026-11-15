# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
"""Shared fixtures for the scripts/sbom/ tests: small synthetic syft-like scans."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "sbom"))

import normalize  # noqa: E402
import sbomlib  # noqa: E402

LAYER_A = "sha256:" + "a" * 64
LAYER_B = "sha256:" + "b" * 64


def _syft_json(ids: dict[str, str]) -> dict[str, Any]:
    """syft-json with a conda numpy, its python twin, a deb, two same-named binaries."""
    return {
        "artifacts": [
            {
                "id": ids["conda"],
                "name": "numpy",
                "version": "2.5.4",
                "type": "conda",
                "locations": [
                    {
                        "path": "/opt/env/conda-meta/numpy-2.5.4-py314h_0.json",
                        "layerID": LAYER_A,
                    }
                ],
                "metadata": {
                    "name": "numpy",
                    "version": "2.5.4",
                    "build": "py314h_0",
                    "channel": "conda-forge",
                    "subdir": "linux-64",
                    "fn": "numpy-2.5.4-py314h_0.conda",
                    "url": "https://conda.anaconda.org/conda-forge/linux-64/numpy-2.5.4-py314h_0.conda",
                    "sha256": "1" * 64,
                    "md5": "2" * 32,
                    "files": [
                        "lib/python3.14/site-packages/numpy-2.5.4.dist-info/METADATA",
                        "lib/python3.14/site-packages/numpy-2.5.4.dist-info/RECORD",
                    ],
                },
            },
            {
                "id": ids["python"],
                "name": "numpy",
                "version": "2.5.4",
                "type": "python",
                "locations": [
                    {
                        "path": "/opt/env/lib/python3.14/site-packages/numpy-2.5.4.dist-info/METADATA",  # noqa: E501
                        "layerID": LAYER_A,
                    },
                    {
                        "path": "/opt/env/lib/python3.14/site-packages/numpy-2.5.4.dist-info/RECORD",  # noqa: E501
                        "layerID": LAYER_A,
                    },
                ],
            },
            {
                "id": ids["deb"],
                "name": "libc6",
                "version": "2.43",
                "type": "deb",
                "locations": [{"path": "/var/lib/dpkg/status", "layerID": LAYER_B}],
            },
            {
                "id": ids["bin1"],
                "name": "Launcher",
                "version": "1.0",
                "type": "binary",
                "locations": [{"path": "/usr/local/lib/x/t32.exe", "layerID": LAYER_B}],
            },
            {
                "id": ids["bin2"],
                "name": "Launcher",
                "version": "1.0",
                "type": "binary",
                "locations": [{"path": "/usr/local/lib/x/t64.exe", "layerID": LAYER_B}],
            },
        ]
    }


def _props(paths: list[str], layer: str) -> list[dict[str, str]]:
    out = []
    for i, p in enumerate(paths):
        out.append({"name": f"syft:location:{i}:layerID", "value": layer})
        out.append({"name": f"syft:location:{i}:path", "value": p})
    return out


def _cdx(ids: dict[str, str], serial: str, stamp: str, label: str) -> dict[str, Any]:
    return {
        "$schema": "http://cyclonedx.org/schema/bom-1.7.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "serialNumber": serial,
        "version": 1,
        "metadata": {
            "timestamp": stamp,
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "author": "anchore",
                        "name": "syft",
                        "version": "1.57.1",
                    }
                ]
            },
            "component": {
                "bom-ref": "rootref" + ids["conda"],
                "type": "container",
                "name": "/s/img.tar",
                "version": "sha256:" + "c" * 64,
            },
            "properties": [{"name": "syft:image:labels:maintainer", "value": label}],
        },
        "components": [
            {
                "bom-ref": ids["conda"],
                "type": "library",
                "name": "numpy",
                "version": "2.5.4",
                "licenses": [{"license": {"id": "BSD-3-Clause"}}],
                "properties": _props(
                    ["/opt/env/conda-meta/numpy-2.5.4-py314h_0.json"], LAYER_A
                ),
            },
            {
                "bom-ref": f"pkg:pypi/numpy@2.5.4?package-id={ids['python']}",
                "type": "library",
                "name": "numpy",
                "version": "2.5.4",
                "purl": "pkg:pypi/numpy@2.5.4",
                "author": "NumPy developers",
                "properties": _props(
                    [
                        "/opt/env/lib/python3.14/site-packages/numpy-2.5.4.dist-info/METADATA",
                        "/opt/env/lib/python3.14/site-packages/numpy-2.5.4.dist-info/RECORD",
                    ],
                    LAYER_A,
                ),
            },
            {
                "bom-ref": f"pkg:deb/ubuntu/libc6@2.43?arch=amd64&distro=ubuntu-26.04&package-id={ids['deb']}",  # noqa: E501
                "type": "library",
                "name": "libc6",
                "version": "2.43",
                "purl": "pkg:deb/ubuntu/libc6@2.43?arch=amd64&distro=ubuntu-26.04",
                "licenses": [{"license": {"name": "Expat"}}],
                "properties": _props(["/var/lib/dpkg/status"], LAYER_B),
            },
            {
                "bom-ref": ids["bin1"],
                "type": "application",
                "name": "Launcher",
                "version": "1.0",
                "properties": _props(["/usr/local/lib/x/t32.exe"], LAYER_B),
            },
            {
                "bom-ref": ids["bin2"],
                "type": "application",
                "name": "Launcher",
                "version": "1.0",
                "properties": _props(["/usr/local/lib/x/t64.exe"], LAYER_B),
            },
            {"bom-ref": "file-" + ids["deb"], "type": "file", "name": "/etc/passwd"},
        ],
        "dependencies": [
            {
                "ref": f"pkg:pypi/numpy@2.5.4?package-id={ids['python']}",
                "dependsOn": [ids["bin1"]],
            },
            {
                "ref": ids["conda"],
                "dependsOn": [
                    f"pkg:deb/ubuntu/libc6@2.43?arch=amd64&distro=ubuntu-26.04&package-id={ids['deb']}"
                ],
            },
        ],
    }


def _spdx(ids: dict[str, str], ns: str, stamp: str) -> dict[str, Any]:
    def pkg(sid: str, name: str, version: str, **extra: Any) -> dict[str, Any]:
        base = {
            "SPDXID": sid,
            "name": name,
            "versionInfo": version,
            "supplier": "NOASSERTION",
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "copyrightText": "NOASSERTION",
        }
        base.update(extra)
        return base

    def purl(p: str) -> list[dict[str, str]]:
        return [
            {
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": p,
            }
        ]

    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "/s/img.tar",
        "documentNamespace": ns,
        "creationInfo": {
            "created": stamp,
            "creators": ["Organization: Anchore, Inc", "Tool: syft-1.57.1"],
        },
        "packages": [
            pkg(
                "SPDXRef-DocumentRoot-Image-img",
                "img",
                "sha256:" + "c" * 64,
                filesAnalyzed=True,
                checksums=[{"algorithm": "SHA256", "checksumValue": "c" * 64}],
                packageVerificationCode={"packageVerificationCodeValue": "0" * 40},
            ),
            pkg(
                f"SPDXRef-Package-conda-numpy-{ids['conda']}",
                "numpy",
                "2.5.4",
                licenseDeclared="BSD-3-Clause",
            ),
            pkg(
                f"SPDXRef-Package-python-numpy-{ids['python']}",
                "numpy",
                "2.5.4",
                supplier="Person: NumPy developers",
                externalRefs=purl("pkg:pypi/numpy@2.5.4"),
            ),
            pkg(
                f"SPDXRef-Package-deb-libc6-{ids['deb']}",
                "libc6",
                "2.43",
                licenseDeclared="LicenseRef-Expat",
                externalRefs=purl(
                    "pkg:deb/ubuntu/libc6@2.43?arch=amd64&distro=ubuntu-26.04"
                ),
            ),
            pkg(f"SPDXRef-Package-binary-Launcher-{ids['bin1']}", "Launcher", "1.0"),
            pkg(f"SPDXRef-Package-binary-Launcher-{ids['bin2']}", "Launcher", "1.0"),
        ],
        "files": [
            {
                "SPDXID": "SPDXRef-File-etc-passwd-" + ids["deb"],
                "fileName": "/etc/passwd",
                "comment": "layerID: " + LAYER_B,
            }
        ],
        "relationships": [
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": "SPDXRef-DocumentRoot-Image-img",
            },
            {
                "spdxElementId": f"SPDXRef-Package-deb-libc6-{ids['deb']}",
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": "SPDXRef-File-etc-passwd-" + ids["deb"],
            },
            {
                "spdxElementId": f"SPDXRef-Package-deb-libc6-{ids['deb']}",
                "relationshipType": "DEPENDENCY_OF",
                "relatedSpdxElement": f"SPDXRef-Package-python-numpy-{ids['python']}",
            },
        ],
    }


IDS_RUN1 = {
    "conda": "1111111111111111",
    "python": "2222222222222222",
    "deb": "3333333333333333",
    "bin1": "4444444444444444",
    "bin2": "5555555555555555",
}
IDS_RUN2 = {
    "conda": "aaaaaaaaaaaaaaaa",
    "python": "bbbbbbbbbbbbbbbb",
    "deb": "cccccccccccccccc",
    "bin1": "dddddddddddddddd",
    "bin2": "eeeeeeeeeeeeeeee",
}


@pytest.fixture
def scan1() -> dict[str, Any]:
    return {
        "cdx": _cdx(
            IDS_RUN1,
            "urn:uuid:11111111-1111-4111-8111-111111111111",
            "2026-11-15T01:00:00Z",
            "someone@example.org",
        ),
        "spdx": _spdx(
            IDS_RUN1, "https://anchore.com/syft/image/x-1", "2026-11-15T01:00:00Z"
        ),
        "syft": _syft_json(IDS_RUN1),
    }


@pytest.fixture
def scan2() -> dict[str, Any]:
    """The same image content, scanned again: other ids, serials, times, labels."""
    return {
        "cdx": _cdx(
            IDS_RUN2,
            "urn:uuid:22222222-2222-4222-8222-222222222222",
            "2026-11-16T09:30:00Z",
            "other@example.org",
        ),
        "spdx": _spdx(
            IDS_RUN2, "https://anchore.com/syft/image/x-2", "2026-11-16T09:30:00Z"
        ),
        "syft": _syft_json(IDS_RUN2),
    }


@pytest.fixture
def opts_factory():
    def make(
        syft: dict[str, Any] | None, name: str = "image-test"
    ) -> normalize.Options:
        return normalize.Options(
            name=name,
            root_name="test",
            version="3.0.0",
            epoch=1794757179,
            root_type="container",
            root_license="BSD-3-Clause",
            description="test image",
            project_supplier=sbomlib.Supplier(
                "EEMT project", "https://github.com/tyson-swetnam/eemt"
            ),
            namespace="https://github.com/tyson-swetnam/eemt/sbom",
            path_suppliers=[
                sbomlib.PathSupplier(
                    "/usr/local/", sbomlib.Supplier("Base image vendor")
                )
            ],
            syft=normalize.SyftIndex.from_syft(copy.deepcopy(syft)) if syft else None,
        )

    return make
