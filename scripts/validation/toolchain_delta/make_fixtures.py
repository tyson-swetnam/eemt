#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
#
# Copy of the convention-fixture generator written during the S1 experiments
# (unchanged below this header), kept here so the toolchain-delta comparison can
# be re-run on its own. The committed fixture generator is Area B's
# (tests/fixtures/grass/); when it lands, run.sh can point EEMT_TD_FIXTURES at its
# output instead.
"""Synthetic DEM fixtures for convention tests (GRASS r.sun vs rsun-core).

Writes, into the output directory given as argv[1]:
  plane_{N,E,S,W}.tif   200x200, 10 m, EPSG:32612, 30-degree plane whose DOWNSLOPE
                        direction (the direction the surface faces) is N/E/S/W.
  wall_{N,E,S,W}.tif    flat plain at 1000 m with a 150 m high, 3-cell wall along
                        the named side of the raster.
  <name>.f32            same data, raw little-endian float32, row-major, row 0 = north
  fixtures.json         grid metadata, centre latitude/longitude, probe points

The grid is centred on the central meridian of UTM 12N (E=500000) so grid north
equals true north (meridian convergence = 0 at the centre).

Needs numpy + GDAL python bindings (run inside a container that has them).
"""

import json
import math
import os
import sys

import numpy as np
from osgeo import gdal, osr

out = sys.argv[1]
os.makedirs(out, exist_ok=True)

ROWS = COLS = 200
RES = 10.0
EPSG = 32612
X0 = 500000.0 - COLS * RES / 2.0  # west edge
Y_TOP = 3597000.0 + ROWS * RES / 2.0  # north edge
SLOPE_DEG = 30.0
Z_PLANE_CENTRE = 1500.0
Z_PLAIN = 1000.0
WALL_H = 150.0
WALL_CELLS = 3

srs = osr.SpatialReference()
srs.ImportFromEPSG(EPSG)
wkt = srs.ExportToWkt()

# cell-centre coordinates
cols = np.arange(COLS)
rows = np.arange(ROWS)
xc = X0 + (cols + 0.5) * RES  # east increases with col
yc = Y_TOP - (rows + 0.5) * RES  # north decreases with row
X, Y = np.meshgrid(xc, yc)
# probe "centre" is the cell (100,100); use its centre coordinate as origin
CR, CC = 100, 100
xo, yo = xc[CC], yc[CR]
t = math.tan(math.radians(SLOPE_DEG))


def write(name, z):
    z = z.astype(np.float32)
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(os.path.join(out, name + ".tif"), COLS, ROWS, 1, gdal.GDT_Float32)
    ds.SetGeoTransform([X0, RES, 0.0, Y_TOP, 0.0, -RES])
    ds.SetProjection(wkt)
    b = ds.GetRasterBand(1)
    b.WriteArray(z)
    b.SetNoDataValue(-9999.0)
    ds.FlushCache()
    ds = None
    z.astype("<f4").tofile(os.path.join(out, name + ".f32"))


# Planes: the surface FACES the named direction, i.e. elevation falls towards it.
planes = {
    "plane_N": Z_PLANE_CENTRE - t * (Y - yo),  # lower to the north
    "plane_S": Z_PLANE_CENTRE + t * (Y - yo),  # lower to the south
    "plane_E": Z_PLANE_CENTRE - t * (X - xo),  # lower to the east
    "plane_W": Z_PLANE_CENTRE + t * (X - xo),  # lower to the west
}
for k, z in planes.items():
    write(k, z)

walls = {}
for side in "NESW":
    z = np.full((ROWS, COLS), Z_PLAIN)
    if side == "N":
        z[:WALL_CELLS, :] += WALL_H  # row 0 = north
    elif side == "S":
        z[-WALL_CELLS:, :] += WALL_H
    elif side == "W":
        z[:, :WALL_CELLS] += WALL_H  # col 0 = west
    elif side == "E":
        z[:, -WALL_CELLS:] += WALL_H
    walls["wall_" + side] = z
    write("wall_" + side, z)

# lat/lon of the centre cell
ll = osr.SpatialReference()
ll.ImportFromEPSG(4326)
ll.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
ct = osr.CoordinateTransformation(srs, ll)
lon, lat, _ = ct.TransformPoint(float(xo), float(yo))

# probe points: (row, col) and map coordinates. "near_X" is 7 cells (70 m) inside
# the inner face of the wall on side X, on the centre row/column.
NEAR = WALL_CELLS + 6
probes_rc = {
    "centre": (CR, CC),
    "near_N": (NEAR, CC),
    "near_S": (ROWS - 1 - NEAR, CC),
    "near_W": (CR, NEAR),
    "near_E": (CR, COLS - 1 - NEAR),
}
probes = {
    k: {"row": r, "col": c, "x": float(xc[c]), "y": float(yc[r])}
    for k, (r, c) in probes_rc.items()
}

meta = {
    "rows": ROWS,
    "cols": COLS,
    "res": RES,
    "epsg": EPSG,
    "west": X0,
    "north": Y_TOP,
    "slope_deg": SLOPE_DEG,
    "z_plane_centre": Z_PLANE_CENTRE,
    "z_plain": Z_PLAIN,
    "wall_height": WALL_H,
    "wall_cells": WALL_CELLS,
    "centre_lat_deg": lat,
    "centre_lon_deg": lon,
    "planes": sorted(planes),
    "walls": sorted(walls),
    "probes": probes,
    "note": "plane_X faces X (downslope towards X); wall_X has the wall on side X",
    "numpy": np.__version__,
    "gdal": gdal.__version__,
}
with open(os.path.join(out, "fixtures.json"), "w") as f:
    json.dump(meta, f, indent=1)
print(json.dumps(meta, indent=1))
