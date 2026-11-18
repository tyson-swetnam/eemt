#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
"""Summarise a toolchain-delta run (the OUTDIR of run.sh) as tables.

    summarize.py OUTDIR REPS

Writes OUTDIR/summary.md and OUTDIR/summary.json and prints summary.md.

Guards (exit 1): a GRASS result tree without g.version.txt; a legacy-vs-new
pair whose two trees report the same GRASS version (the comparison would be
vacuous); a file present in only one tree of any pair; an r.sun that is not
linked to libgomp.

Differences are results, not errors: they never change the exit status.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any


def kv_file(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    with open(path) as f:
        for line in f:
            if "=" in line:
                k, v = line.rstrip("\n").split("=", 1)
                out[k] = v
    return out


def group_of(rel: str) -> str:
    """Product group of a result file, e.g. day/d80_glob.tif -> day/glob."""
    parts = rel.split("/")
    name = parts[-1].removesuffix(".tif").removesuffix(".json")
    if len(parts) == 1:
        return name
    top = parts[0]
    if top == "day":
        return "day/" + name.split("_", 1)[1]
    if top == "inst":
        return "inst/" + name.split("_", 2)[2]
    if top == "horizon":
        return "horizon"
    if top == "daily":
        return "daily/" + re.sub(r"_day_\d+$", "", name)
    if top == "monthly":
        return "monthly/" + re.sub(r"_\d+_sum$", "", name)
    return top + "/" + name


def fmt(x: float | None) -> str:
    if x is None:
        return "-"
    if x == 0:
        return "0"
    return f"{x:.3g}"


def main(argv: list[str]) -> int:
    root, reps = argv[1], int(argv[2])
    errors: list[str] = []
    cmp_root = os.path.join(root, "compare")
    pairs = sorted(
        d for d in os.listdir(cmp_root) if os.path.isdir(os.path.join(cmp_root, d))
    )
    dems = sorted(
        f.removesuffix(".json")
        for f in os.listdir(os.path.join(cmp_root, "legacy-vs-new"))
    )

    # --- toolchain versions --------------------------------------------------
    versions: dict[str, Any] = {}
    for flavour in ("legacy", "new"):
        d = os.path.join(root, "versions", flavour)
        gv = kv_file(os.path.join(d, "g.version.txt"))
        with open(os.path.join(d, "ldd-r.sun.txt")) as f:
            ldd = f.read()
        with open(os.path.join(d, "which.txt")) as f:
            which = f.read().strip()
        versions[flavour] = {
            k: gv.get(k) for k in ("version", "build_date", "proj", "gdal", "geos")
        }
        versions[flavour]["r.sun"] = which
        versions[flavour]["libgomp"] = "libgomp" in ldd
        if "libgomp" not in ldd:
            errors.append(f"{flavour}: r.sun is not linked to libgomp")
    gdal_versions: dict[str, str] = {}
    for variant in sorted(os.listdir(os.path.join(root, "gdal"))):
        with open(os.path.join(root, "gdal", variant, "mcn_10m", "versions.txt")) as f:
            gdal_versions[variant] = f.read().strip()

    # --- per-tree GRASS version guard -----------------------------------------
    tree_versions: dict[str, str] = {}
    for flavour in ("legacy", "new"):
        for rep in range(1, reps + 1):
            for dem in dems:
                p = os.path.join(
                    root, "grass", f"{flavour}-r{rep}", dem, "g.version.txt"
                )
                if not os.path.exists(p):
                    errors.append(f"missing {p}")
                    continue
                tree_versions[f"{flavour}-r{rep}/{dem}"] = kv_file(p).get(
                    "version", "?"
                )
    for dem in dems:
        a = tree_versions.get(f"legacy-r1/{dem}")
        b = tree_versions.get(f"new-r1/{dem}")
        if a is not None and a == b:
            errors.append(f"{dem}: legacy and new trees both report GRASS {a}")

    # --- per pair, per DEM ----------------------------------------------------
    table: dict[str, Any] = {}
    for pair in pairs:
        table[pair] = {}
        for dem in dems:
            with open(os.path.join(cmp_root, pair, dem + ".json")) as f:
                d = json.load(f)
            if d["only_a"] or d["only_b"]:
                errors.append(
                    f"{pair}/{dem}: only_a={d['only_a']} only_b={d['only_b']}"
                )
            groups: dict[str, dict[str, Any]] = {}
            for rel, v in d["files"].items():
                g = groups.setdefault(
                    group_of(rel),
                    {
                        "files": 0,
                        "identical": 0,
                        "n_diff": 0,
                        "max_abs": 0.0,
                        "max_rel": 0.0,
                        "max_ulp": 0.0,
                        "null_masks_equal": True,
                        "metadata_equal": True,
                        "sizes_differ": 0,
                        "examples": [],
                    },
                )
                g["files"] += 1
                g["identical"] += int(v["bit_identical"])
                if v.get("kind") == "points":
                    for key in ("daymet", "epsg3857"):
                        g["n_diff"] += v[key]["n_differ"]
                        g["max_abs"] = max(
                            g["max_abs"], v[key]["max_abs_dx_m"], v[key]["max_abs_dy_m"]
                        )
                        g["max_ulp"] = max(g["max_ulp"], v[key].get("max_ulp", 0.0))
                    continue
                g["n_diff"] += v.get("n_diff", 0)
                g["max_abs"] = max(g["max_abs"], v.get("max_abs", 0.0))
                g["max_rel"] = max(g["max_rel"], v.get("max_rel", 0.0))
                g["max_ulp"] = max(g["max_ulp"], v.get("max_ulp", 0.0))
                g["null_masks_equal"] &= bool(v.get("null_mask_equal", False))
                g["metadata_equal"] &= bool(v.get("metadata_equal", False))
                if v["size"][0] != v["size"][1]:
                    g["sizes_differ"] += 1
                    g["examples"].append(
                        {
                            "file": rel,
                            "size": v["size"],
                            "overlap_identical": v.get("overlap_identical"),
                        }
                    )
                if (
                    not v["bit_identical"]
                    and len(g["examples"]) < 3
                    and "max_abs_at" in v
                ):
                    g["examples"].append(
                        {
                            "file": rel,
                            "n_diff": v["n_diff"],
                            "n_both_valid": v["n_both_valid"],
                            "max_abs_at": v["max_abs_at"],
                        }
                    )
            table[pair][dem] = {"summary": d["summary"], "groups": groups}

    # --- markdown ------------------------------------------------------------
    lines: list[str] = ["# Toolchain delta summary", ""]
    lines += [
        "## GRASS (g.version -rge inside each image)",
        "",
        "| | GRASS | build date | PROJ | GDAL | GEOS | r.sun | libgomp |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for fl, v in versions.items():
        lines.append(
            f"| {fl} | {v['version']} | {v['build_date']} | {v['proj']} | "
            f"{v['gdal']} | {v['geos']} | `{v['r.sun']}` | {v['libgomp']} |"
        )
    lines += ["", "## GDAL command-line stacks (gdal_ops.sh versions.txt)", ""]
    for variant, text in gdal_versions.items():
        lines.append(f"- **{variant}**: " + "; ".join(text.splitlines()))
    lines += [
        "",
        "## Files compared and bit-identical, per pair and DEM",
        "",
        "| pair | " + " | ".join(dems) + " |",
        "|---|" + "---|" * len(dems),
    ]
    for pair in pairs:
        cells = [
            f"{table[pair][d]['summary']['bit_identical']}/"
            f"{table[pair][d]['summary']['compared']}"
            for d in dems
        ]
        lines.append(f"| {pair} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Differences (groups with at least one non-identical file)",
        "",
        "| pair | DEM | group | identical/files | differing pixels | max abs | "
        "max rel | max ULP | NULL masks equal | metadata equal | sizes differ |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    any_diff = False
    for pair in pairs:
        for dem in dems:
            for gname, g in sorted(table[pair][dem]["groups"].items()):
                if g["identical"] == g["files"]:
                    continue
                any_diff = True
                lines.append(
                    f"| {pair} | {dem} | {gname} | {g['identical']}/{g['files']} | "
                    f"{g['n_diff']} | {fmt(g['max_abs'])} | {fmt(g['max_rel'])} | "
                    f"{fmt(g['max_ulp'])} | {g['null_masks_equal']} | "
                    f"{g['metadata_equal']} | {g['sizes_differ']} |"
                )
    if not any_diff:
        lines.append("| (none) | | | | | | | | | | |")
    if errors:
        lines += ["", "## GUARD FAILURES", ""] + [f"- {e}" for e in errors]
    md = "\n".join(lines) + "\n"

    with open(os.path.join(root, "summary.md"), "w") as f:
        f.write(md)
    with open(os.path.join(root, "summary.json"), "w") as f:
        json.dump(
            {
                "versions": versions,
                "gdal_versions": gdal_versions,
                "tree_versions": tree_versions,
                "pairs": table,
                "errors": errors,
            },
            f,
            indent=1,
        )
    sys.stdout.write(md)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
