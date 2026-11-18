#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
#
# Monthly sum of the toolchain-delta comparison, following sol/sol/rsum.sh:
# a fresh GRASS location created from a daily GeoTIFF, r.in.gdal of every daily
# file, then r.series -n method=sum and r.out.gdal -c createopt=COMPRESS=LZW.
# Runs INSIDE a GRASS session whose temporary location was created from the
# first daily GeoTIFF (see in_image.sh).
#
#   grass_rsum.sh DAILY_DIR OUTDIR
#
# Difference from rsum.sh: its `g.remove -f "*"` is omitted (GRASS 8 rejects
# it, and the location is new and empty anyway).
set -euo pipefail

daily="$1"
out="$2"
export GRASS_VERBOSE=0
mkdir -p "${out}/monthly"

name=""
for tif in "${daily}"/total_sun_day_*.tif "${daily}"/hours_sun_day_*.tif; do
    name="$(basename "${tif}" .tif)"
    r.in.gdal input="${tif}" output="${name}"
done
g.region -s raster="${name}"

r.series -n input="$(g.list type=raster pattern='total_sun_day_*' separator=,)" \
    output=total_sun_1_sum method=sum
r.series -n input="$(g.list type=raster pattern='hours_sun_day_*' separator=,)" \
    output=hours_sun_1_sum method=sum

r.out.gdal createopt="COMPRESS=LZW" -c input=total_sun_1_sum \
    output="${out}/monthly/total_sun_1_sum.tif" --quiet
r.out.gdal createopt="COMPRESS=LZW" -c input=hours_sun_1_sum \
    output="${out}/monthly/hours_sun_1_sum.tif" --quiet
