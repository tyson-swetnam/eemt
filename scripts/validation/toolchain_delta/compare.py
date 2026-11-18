#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
"""Per-pixel comparison of two result trees of the toolchain-delta run.

    compare.py A_DIR B_DIR OUT_JSON

Every *.tif under A_DIR is paired with the file at the same relative path under
B_DIR (a file present on one side only is reported). For each pair:

  dtype, size, geotransform and CRS equality (osr IsSame), nodata values;
  NULL masks (NaN or the band's nodata value) and whether they are equal;
  over pixels valid in both: number of differing pixels, max |a - b|, max
  |a - b| / max(|a|, |b|), max |a - b| in units of the spacing of the stored
  type at max(|a|, |b|) ("max_ulp"; 1 = adjacent representable values), the
  pixel of the largest absolute difference;
  bit_identical: same dtype, size, geotransform, CRS, nodata value, NULL masks
  and identical bytes of valid pixels.
  When sizes differ but origin and cell size agree, the statistics are computed
  over the common upper-left window ("overlap", "overlap_identical").

points.json files (coordinate transforms, see proj_points.py) are compared as
max |dx|, |dy| per target CRS.

Values are compared in float64 after reading each band in its own type.
Writes OUT_JSON and prints one summary line per differing file.
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Any

import numpy as np
from osgeo import gdal, osr

gdal.UseExceptions()


def read(path: str) -> dict[str, Any]:
    ds = gdal.Open(path)
    band = ds.GetRasterBand(1)
    arr = band.ReadAsArray()
    nodata = band.GetNoDataValue()
    null = ~np.isfinite(arr) if arr.dtype.kind == "f" else np.zeros(arr.shape, bool)
    if nodata is not None and not math.isnan(nodata):
        null |= arr == nodata
    srs = osr.SpatialReference()
    wkt = ds.GetProjection()
    if wkt:
        srs.ImportFromWkt(wkt)
    return {
        "arr": arr,
        "null": null,
        "dtype": str(arr.dtype),
        "size": [ds.RasterXSize, ds.RasterYSize],
        "gt": list(ds.GetGeoTransform()),
        "srs": srs if wkt else None,
        "nodata": None if nodata is None else repr(nodata),
    }


def compare_rasters(pa: str, pb: str) -> dict[str, Any]:
    a, b = read(pa), read(pb)
    r: dict[str, Any] = {
        "dtype": [a["dtype"], b["dtype"]],
        "size": [a["size"], b["size"]],
        "gt_equal": a["gt"] == b["gt"],
        "gt": [a["gt"], b["gt"]] if a["gt"] != b["gt"] else a["gt"],
        "crs_same": bool(
            a["srs"] is not None and b["srs"] is not None and a["srs"].IsSame(b["srs"])
        ),
        "nodata": [a["nodata"], b["nodata"]],
    }
    r["metadata_equal"] = bool(
        r["gt_equal"]
        and r["crs_same"]
        and a["nodata"] == b["nodata"]
        and a["dtype"] == b["dtype"]
    )
    r["bit_identical"] = False
    if a["size"] != b["size"]:
        r["comparable"] = False
        if not r["gt_equal"]:
            return r
        # Same origin and cell size: compare the common upper-left window.
        cols = min(a["size"][0], b["size"][0])
        rows = min(a["size"][1], b["size"][1])
        r["overlap"] = [cols, rows]
        for x in (a, b):
            x["arr"] = x["arr"][:rows, :cols]
            x["null"] = x["null"][:rows, :cols]
        r.update(pixel_stats(a, b, overlap=True))
        return r
    r["comparable"] = True
    r.update(pixel_stats(a, b, overlap=False))
    r["bit_identical"] = bool(r["bit_identical"] and r["metadata_equal"])
    return r


def pixel_stats(a: dict[str, Any], b: dict[str, Any], overlap: bool) -> dict[str, Any]:
    """Per-pixel statistics of two equally shaped arrays (see module doc)."""
    r: dict[str, Any] = {}
    na, nb = a["null"], b["null"]
    r["n_cells"] = int(na.size)
    r["n_null"] = [int(na.sum()), int(nb.sum())]
    r["null_mask_equal"] = bool(np.array_equal(na, nb))
    r["n_null_mismatch"] = int((na != nb).sum())
    both = ~na & ~nb
    va = a["arr"].astype(np.float64)
    vb = b["arr"].astype(np.float64)
    r["n_both_valid"] = int(both.sum())
    diff = np.zeros(va.shape, bool)
    diff[both] = va[both] != vb[both]
    r["n_diff"] = int(diff.sum())
    absd = np.zeros(va.shape)
    absd[both] = np.abs(va[both] - vb[both])
    den = np.maximum(np.abs(va), np.abs(vb))
    rel = np.zeros(va.shape)
    sel = both & (den > 0)
    rel[sel] = absd[sel] / den[sel]
    r["max_abs"] = float(absd.max()) if both.any() else 0.0
    r["max_rel"] = float(rel.max()) if both.any() else 0.0
    # Difference in units of the spacing of the stored type (ULPs).
    stored = np.dtype(a["dtype"]) if a["dtype"] == b["dtype"] else np.dtype(np.float64)
    if stored.kind == "f":
        spacing = np.spacing(den.astype(stored)).astype(np.float64)
    else:
        spacing = np.ones(den.shape)
    ulp = np.zeros(va.shape)
    ulp[both] = absd[both] / spacing[both]
    r["max_ulp"] = float(ulp.max()) if both.any() else 0.0
    if r["n_diff"]:
        i, j = np.unravel_index(int(np.argmax(absd)), absd.shape)
        r["max_abs_at"] = {
            "row": int(i),
            "col": int(j),
            "a": float(va[i, j]),
            "b": float(vb[i, j]),
        }
        r["mean_abs_of_differing"] = float(absd[diff].mean())
    if both.any():
        r["range_a"] = [
            float(va[both].min()),
            float(va[both].mean()),
            float(va[both].max()),
        ]
    same_bytes = a["dtype"] == b["dtype"] and np.array_equal(
        a["arr"][both].view(np.uint8), b["arr"][both].view(np.uint8)
    )
    # bit_identical is never true for an overlap-only comparison.
    r["bit_identical"] = bool(r["null_mask_equal"] and same_bytes and not overlap)
    if overlap:
        r["overlap_identical"] = bool(r["null_mask_equal"] and same_bytes)
    return r


def compare_points(pa: str, pb: str) -> dict[str, Any]:
    with open(pa) as fa, open(pb) as fb:
        a, b = json.load(fa), json.load(fb)
    r: dict[str, Any] = {"kind": "points"}
    for key in ("daymet", "epsg3857"):
        pa_, pb_ = np.array(a[key], np.float64), np.array(b[key], np.float64)
        d = np.abs(pa_ - pb_)
        dx, dy = float(d[:, 0].max()), float(d[:, 1].max())
        ulp = float((d / np.spacing(np.maximum(np.abs(pa_), np.abs(pb_)))).max())
        r[key] = {
            "max_abs_dx_m": dx,
            "max_abs_dy_m": dy,
            "max_ulp": ulp,
            "n_points": len(a[key]),
            "n_differ": sum(1 for p, q in zip(a[key], b[key], strict=False) if p != q),
            "identical": a[key] == b[key],
        }
    r["bit_identical"] = all(r[k]["identical"] for k in ("daymet", "epsg3857"))
    return r


def files(root: str) -> set[str]:
    found = set()
    for d, _, names in os.walk(root):
        for n in names:
            if n.endswith(".tif") or n == "points.json":
                found.add(os.path.relpath(os.path.join(d, n), root))
    return found


def main(argv: list[str]) -> int:
    a_dir, b_dir, out = argv[1], argv[2], argv[3]
    fa, fb = files(a_dir), files(b_dir)
    results: dict[str, Any] = {
        "a": a_dir,
        "b": b_dir,
        "only_a": sorted(fa - fb),
        "only_b": sorted(fb - fa),
        "files": {},
    }
    for rel in sorted(fa & fb):
        pa, pb = os.path.join(a_dir, rel), os.path.join(b_dir, rel)
        if rel.endswith("points.json"):
            results["files"][rel] = compare_points(pa, pb)
        else:
            results["files"][rel] = compare_rasters(pa, pb)
    n = len(results["files"])
    nid = sum(1 for v in results["files"].values() if v["bit_identical"])
    results["summary"] = {"compared": n, "bit_identical": nid, "not_identical": n - nid}
    with open(out, "w") as f:
        json.dump(results, f, indent=1)
    print(
        f"{a_dir} vs {b_dir}: {n} compared, {nid} bit-identical, "
        f"only_a={len(results['only_a'])} only_b={len(results['only_b'])}"
    )
    for rel, v in results["files"].items():
        if not v["bit_identical"]:
            brief = {
                k: v.get(k)
                for k in (
                    "dtype",
                    "size",
                    "metadata_equal",
                    "gt_equal",
                    "crs_same",
                    "overlap",
                    "overlap_identical",
                    "null_mask_equal",
                    "n_diff",
                    "max_abs",
                    "max_rel",
                    "max_ulp",
                    "daymet",
                    "epsg3857",
                )
                if k in v
            }
            print(f"  DIFF {rel}: {json.dumps(brief)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
