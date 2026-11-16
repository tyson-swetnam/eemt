#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
#
# GRASS part of the toolchain-delta comparison. Runs INSIDE a GRASS session
# whose temporary location was created from the DEM (see in_image.sh), and
# writes every result as a GeoTIFF with the pipeline's export options
# (r.out.gdal -c createopt=COMPRESS=LZW, as in sol/sol/rsun.sh).
#
#   grass_suite.sh DEM OUTDIR
#
# The parameters are explicit and identical for every image, so that any
# difference between images comes from the toolchain, not from defaults:
#   r.sun step=0.25 (decimal hours = 15 minutes) linke_value=3.0
#   albedo_value=0.2 nprocs=$EEMT_TD_NPROCS (default 4)
#
# Outputs (relative to OUTDIR):
#   terrain/{slope,aspect}.tif              r.slope.aspect (as rsun_prep.sh)
#   day/d<D>_{glob,insol,beam,diff,refl}.tif r.sun mode 2, days 1 80 172 266 355
#   inst/d<D>_t<H>_{glob,beam,diff,refl,incid}.tif
#                                           r.sun mode 1, days 80 355, 09/12/15 h
#   horizon/hor_<AAA>.tif                   r.horizon step=45 (8 directions)
#   daily/{total,hours}_sun_day_<D>.tif     r.sun mode 2, days 1..31 (January),
#                                           input of the monthly sum (rsum.sh)
set -euo pipefail

dem="$1"
out="$2"
nprocs="${EEMT_TD_NPROCS:-4}"
step=0.25
linke=3.0
albedo=0.2

export GRASS_VERBOSE=0
mkdir -p "${out}"/{terrain,day,inst,horizon,daily}

# Record which GRASS produced this tree (summarize.py refuses a legacy-vs-new
# comparison whose two trees report the same version).
g.version -rge > "${out}/g.version.txt"
ldd "$(command -v r.sun)" > "${out}/ldd-r.sun.txt"

export_tif() {
    # $1 = raster map, $2 = output file; same options as the pipeline scripts
    r.out.gdal createopt="COMPRESS=LZW" -c input="$1" output="$2" --quiet
}

# --- import and terrain (sol/sol/rsun_prep.sh, sol/sol/rsun.sh) ------------
r.in.gdal input="${dem}" output=dem
g.region -sa raster=dem
r.slope.aspect elevation=dem slope=slope_dec aspect=aspect_dec
export_tif slope_dec "${out}/terrain/slope.tif"
export_tif aspect_dec "${out}/terrain/aspect.tif"

rsun_common=(elevation=dem aspect=aspect_dec slope=slope_dec
             linke_value="${linke}" albedo_value="${albedo}" nprocs="${nprocs}")

# --- r.sun mode 2 (daily sums) on five days --------------------------------
for day in 1 80 172 266 355; do
    r.sun "${rsun_common[@]}" day="${day}" step="${step}" \
        glob_rad=glob insol_time=insol beam_rad=beam diff_rad=diff refl_rad=refl \
        --overwrite
    for q in glob insol beam diff refl; do
        export_tif "${q}" "${out}/day/d${day}_${q}.tif"
    done
done

# --- r.sun mode 1 (instantaneous irradiance, W m-2) -------------------------
for day in 80 355; do
    for hour in 9 12 15; do
        r.sun "${rsun_common[@]}" day="${day}" time="${hour}" \
            glob_rad=glob beam_rad=beam diff_rad=diff refl_rad=refl incidout=incid \
            --overwrite
        for q in glob beam diff refl incid; do
            export_tif "${q}" "$(printf '%s/inst/d%s_t%02d_%s.tif' "${out}" "${day}" "${hour}" "${q}")"
        done
    done
done

# --- r.horizon, 8 directions -------------------------------------------------
r.horizon elevation=dem step=45 output=hor
# Map names carry the angle; their exact format may differ between versions,
# so normalise them to hor_<three-digit integer degrees>.
g.list type=raster pattern='hor_*' > "${out}/horizon/names.txt"
while read -r name; do
    angle="$(printf '%s\n' "${name#hor_}" | sed 's/_/./')"
    angle_int=$((10#${angle%.*}))   # 10#: "045" is decimal, not octal
    export_tif "${name}" "$(printf '%s/horizon/hor_%03d.tif' "${out}" "${angle_int}")"
done < "${out}/horizon/names.txt"

# --- January daily sums, the input of the monthly r.series (rsum.sh) --------
# Map names and export paths follow sol/sol/rsun.sh (total_sun / hours_sun).
for day in $(seq 1 31); do
    r.sun "${rsun_common[@]}" day="${day}" step="${step}" \
        glob_rad=total_sun insol_time=hours_sun --overwrite
    export_tif total_sun "${out}/daily/total_sun_day_${day}.tif"
    export_tif hours_sun "${out}/daily/hours_sun_day_${day}.tif"
done
