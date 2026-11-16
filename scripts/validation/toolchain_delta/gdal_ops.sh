#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
#
# GDAL part of the toolchain-delta comparison: the GDAL command lines the
# pipelines run outside GRASS, with the GDAL/PROJ found first on PATH (the
# caller selects the stack by setting PATH, see in_image.sh).
#
#   gdal_ops.sh DEM EPSG OUTDIR
#
# EPSG is the DEM's EPSG code, or "-" for a DEM without one: then -s_srs is
# omitted and the CRS stored in the file is used (Tiff.warp itself would fail
# on such a DEM, since it always passes -s_srs EPSG:<region>).
#
# Outputs (relative to OUTDIR):
#   warp_daymet.tif   sol/sol/Tiff.py Tiff.warp("DAYMET"):
#                     gdalwarp -s_srs EPSG:<epsg> -overwrite -t_srs <DAYMET_proj>
#                              -r bilinear -of GTiff
#   warp_3857_lzw.tif sol/sol/parser.py: gdalwarp -overwrite -t_srs EPSG:3857
#                     -r bilinear -of GTiff -dstnodata nan, then
#                     gdal_translate -co compress=LZW
#   lzw.tif           gdal_translate -co COMPRESS=LZW of the DEM itself
#   diag_warp_daymet_et0.tif   diagnostic: warp_daymet with -et 0
#   diag_warp_daymet_near.tif  diagnostic: warp_daymet with -r near (if it is
#                     identical while warp_daymet differs, the source pixel each
#                     output pixel maps to is unchanged and the difference is in
#                     the bilinear arithmetic, not in the coordinate operation)
#   projwin.tif       gdal_translate -projwin ULX ULY LRX LRY -of GTiff (the
#                     eemt/eemt/metget.sh form) on the inner half of the DEM
#   points.json       a 41 x 41 grid of points over the DEM transformed EPSG:<epsg> ->
#                     DAYMET_proj and -> EPSG:3857 with the same PROJ (osgeo.osr)
#   versions.txt      gdalinfo --version, projinfo release, binaries used
set -euo pipefail

dem="$1"
epsg="$2"
out="$3"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Verbatim from sol/sol/Tiff.py (Tiff.DAYMET_proj).
daymet_proj="+proj=lcc +lat_1=25 +lat_2=60 +lat_0=42.5 +lon_0=-100 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"

mkdir -p "${out}"
{
    echo "gdalwarp: $(command -v gdalwarp)"
    echo "gdal_translate: $(command -v gdal_translate)"
    echo "python3: $(command -v python3)"
    gdalinfo --version
    projinfo 2>&1 | head -n 1 || true
    python3 -c 'from osgeo import gdal, osr; print("osgeo", gdal.__version__, "PROJ", ".".join(map(str, (osr.GetPROJVersionMajor(), osr.GetPROJVersionMinor(), osr.GetPROJVersionMicro()))))'
} > "${out}/versions.txt" 2>&1

if [[ "${epsg}" == - ]]; then
    s_srs=()
    src_def="$(gdalsrsinfo -o wkt1 --single-line "${dem}")"
else
    s_srs=(-s_srs "EPSG:${epsg}")
    src_def="EPSG:${epsg}"
fi

# Which coordinate operations PROJ offers for the pipeline's reprojection
# (projinfo accepts a PROJ string as a CRS only with +type=crs).
projinfo -s "${src_def}" -t "${daymet_proj} +type=crs" --spatial-test intersects \
    -o PROJ > "${out}/projinfo_daymet.txt" 2>&1 || true
projinfo -s "${src_def}" -t EPSG:3857 --spatial-test intersects \
    -o PROJ > "${out}/projinfo_3857.txt" 2>&1 || true

gdalwarp "${s_srs[@]}" -overwrite -t_srs "${daymet_proj}" \
    -r bilinear -of GTiff -q "${dem}" "${out}/warp_daymet.tif"

# Diagnostics only (not pipeline commands): the same warp with the exact
# transformer (-et 0) instead of gdalwarp's default approximation (0.125 px),
# and with nearest-neighbour resampling.
gdalwarp "${s_srs[@]}" -overwrite -t_srs "${daymet_proj}" \
    -r bilinear -of GTiff -et 0 -q "${dem}" "${out}/diag_warp_daymet_et0.tif"
gdalwarp "${s_srs[@]}" -overwrite -t_srs "${daymet_proj}" \
    -r near -of GTiff -q "${dem}" "${out}/diag_warp_daymet_near.tif"

gdalwarp -overwrite -t_srs EPSG:3857 -r bilinear -of GTiff -dstnodata nan -q \
    "${dem}" "${out}/warp_3857_tmp.tif"
gdal_translate -co compress=LZW -q "${out}/warp_3857_tmp.tif" "${out}/warp_3857_lzw.tif"
rm -f "${out}/warp_3857_tmp.tif"

gdal_translate -co COMPRESS=LZW -q "${dem}" "${out}/lzw.tif"

# Inner half of the DEM, from its geotransform.
read -r ulx uly lrx lry < <(python3 - "${dem}" <<'PY'
import sys
from osgeo import gdal
ds = gdal.Open(sys.argv[1])
x0, dx, _, y0, _, dy = ds.GetGeoTransform()
w, h = ds.RasterXSize * dx, ds.RasterYSize * dy
print(repr(x0 + w / 4), repr(y0 + h / 4), repr(x0 + 3 * w / 4), repr(y0 + 3 * h / 4))
PY
)
gdal_translate -projwin "${ulx}" "${uly}" "${lrx}" "${lry}" -of GTiff -q \
    "${dem}" "${out}/projwin.tif"

python3 "${here}/proj_points.py" "${dem}" "${epsg}" "${daymet_proj}" > "${out}/points.json"
