#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
"""Validate CycloneDX JSON files against the strict schema of their specVersion.

    cdx_validate.py FILE...

Needs cyclonedx-python-lib[json-validation] (requirements-dev.txt). Exit status 1 if
any file is invalid or is not CycloneDX 1.7.
"""

from __future__ import annotations

import json
import sys

from cyclonedx.schema import SchemaVersion
from cyclonedx.validation.json import JsonStrictValidator

REQUIRED = "1.7"


def main(paths: list[str]) -> int:
    failed = 0
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
        spec = json.loads(raw).get("specVersion")
        if spec != REQUIRED:
            print(f"{path}: specVersion {spec!r}, expected {REQUIRED}")
            failed += 1
            continue
        error = JsonStrictValidator(SchemaVersion.from_version(spec)).validate_str(raw)
        if error is None:
            print(f"{path}: valid CycloneDX {spec}")
        else:
            print(f"{path}: INVALID: {error!r}"[:2000])
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
