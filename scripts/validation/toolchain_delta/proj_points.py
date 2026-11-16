#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
"""Transform a 41 x 41 grid of points spanning a DEM with the local PROJ.

Prints JSON to stdout: the source points and their coordinates in the Daymet
LCC definition and in EPSG:3857, as computed by osgeo.osr (the PROJ the local
GDAL is linked to). compare.py diffs these between toolchains, which separates
a change in the coordinate operation from a change in gdalwarp's resampling.

    proj_points.py DEM EPSG DAYMET_PROJ

EPSG "-" means: use the CRS stored in the DEM.
"""
from __future__ import annotations

import json
import sys

from osgeo import gdal, osr


def srs_from(definition: str) -> osr.SpatialReference:
    """Build a SpatialReference with x = easting/longitude axis order."""
    srs = osr.SpatialReference()
    srs.SetFromUserInput(definition)
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return srs


def main(argv: list[str]) -> int:
    dem, epsg, daymet = argv[1], argv[2], argv[3]
    ds = gdal.Open(dem)
    x0, dx, _, y0, _, dy = ds.GetGeoTransform()
    w, h = ds.RasterXSize * dx, ds.RasterYSize * dy
    n = 40
    points = [(x0 + w * i / n, y0 + h * j / n) for j in range(n + 1) for i in range(n + 1)]

    src = srs_from(ds.GetProjection() if epsg == "-" else "EPSG:" + epsg)
    result: dict[str, object] = {"source": [list(p) for p in points]}
    for key, target in (("daymet", daymet), ("epsg3857", "EPSG:3857")):
        ct = osr.CoordinateTransformation(src, srs_from(target))
        result[key] = [list(ct.TransformPoint(x, y)[:2]) for x, y in points]
    json.dump(result, sys.stdout, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
