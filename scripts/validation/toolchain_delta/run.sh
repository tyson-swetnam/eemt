#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
#
# Toolchain delta: run the same explicit GRASS and GDAL command sequence in the
# legacy workflow image and in the new one, then compare every output per pixel.
# Results: validation/toolchain-delta.md (SDR-025).
#
#   EEMT_LEGACY_IMAGE=<image> [EEMT_NEW_IMAGE=<image>] run.sh OUTDIR
#
# Environment
#   EEMT_LEGACY_IMAGE  required; the pre-3.0 workflow image (GRASS 8.3.2 from
#                      Ubuntu 24.04). It cannot be rebuilt, so it is not named here.
#   EEMT_NEW_IMAGE     default eemt:<docker/VERSION>
#   EEMT_TD_REPS       repetitions of the GRASS suite per image (default 2); rep 2
#                      is the same-image control
#   EEMT_TD_JOBS       containers run in parallel (default 8)
#   EEMT_TD_NPROCS     r.sun nprocs (default 4)
#   EEMT_TD_FIXTURES   directory with the convention fixtures (plane_*.tif,
#                      wall_*.tif); default: generate them with make_fixtures.py
#                      in the new image
#   EEMT_TD_GORDONGULCH  optional local copy of gordongulch_dem_10m_3dep_cog.tif
#                      (USGS 3DEP; https://data.cyverse.org/dav-anon/iplant/home/
#                      tswetnam/projects/eemt/gordongulch_dem_10m_3dep_cog.tif).
#                      Checked against its sha256 and added as a third real
#                      DEM. It has no EPSG code (a GRS80 UTM 13N BOUNDCRS), so
#                      the GDAL operations use the CRS stored in the file.
#
# OUTDIR layout
#   inputs/                 the DEMs fed to both images (mcn_10m + fixtures)
#   grass/<flavour>-r<n>/<dem>/   grass_suite.sh + grass_rsum.sh outputs
#   gdal/<variant>/<dem>/   gdal_ops.sh outputs; variants: new, legacy-conda
#                           (the legacy image's PATH: conda GDAL), legacy-apt
#   versions/<flavour>/     g.version -rge, module interface descriptions, ldd
#   compare/<pair>/<dem>.json and compare/*.log   compare.py results
#   summary.md, summary.json  summarize.py tables
#   logs/                   one log per container
#
# Containers run with --network none, as the calling user, read-only script
# mount; nothing is pulled, tagged or removed.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "${here}/../../.." && pwd)"

if [[ "${1:-}" == _job ]]; then
    # internal: _job IMAGE FLAVOUR TASK DEM EPSG OUTREL  (OUTDIR in $TD_OUT)
    shift
    image="$1" flavour="$2" task="$3" dem="$4" epsg="$5" outrel="$6"
    log="${TD_OUT}/logs/$(printf '%s' "${outrel}" | tr '/' '_').log"
    if docker run --rm --network none \
        --name "eemt-td-$$-${RANDOM}${RANDOM}" --label eemt-work=1 \
        --user "$(id -u):$(id -g)" -e HOME=/tmp -e TMPDIR=/tmp \
        -e EEMT_TD_NPROCS="${EEMT_TD_NPROCS:-4}" \
        -v "${here}:/td:ro" -v "${TD_OUT}:/w:rw" \
        "${image}" bash /td/in_image.sh "${flavour}" "${task}" \
        "/w/inputs/${dem}.tif" "${epsg}" "/w/${outrel}" > "${log}" 2>&1; then
        echo "ok   ${outrel}"
    else
        echo "FAIL ${outrel} (see ${log})"
        exit 1
    fi
    exit 0
fi

out="${1:?usage: EEMT_LEGACY_IMAGE=<image> run.sh OUTDIR}"
legacy="${EEMT_LEGACY_IMAGE:?set EEMT_LEGACY_IMAGE to the legacy workflow image}"
new="${EEMT_NEW_IMAGE:-eemt:$(cat "${repo}/docker/VERSION")}"
reps="${EEMT_TD_REPS:-2}"
jobs="${EEMT_TD_JOBS:-8}"

mkdir -p "${out}"
out="$(cd "${out}" && pwd)"
export TD_OUT="${out}"
mkdir -p "${out}"/{inputs,logs,compare}

for img in "${legacy}" "${new}"; do
    docker image inspect "${img}" --format "image ${img} = {{.Id}}" \
        || { echo "image ${img} not found locally" >&2; exit 1; }
done | tee "${out}/images.txt"

# --- inputs -------------------------------------------------------------------
cp "${repo}/sol/examples/mcn_10m.tif" "${out}/inputs/mcn_10m.tif"
if [[ -n "${EEMT_TD_FIXTURES:-}" ]]; then
    cp "${EEMT_TD_FIXTURES}"/{plane,wall}_?.tif "${out}/inputs/"
else
    docker run --rm --network none --name "eemt-td-$$-fixtures" --label eemt-work=1 \
        --user "$(id -u):$(id -g)" -e HOME=/tmp \
        -v "${here}:/td:ro" -v "${out}:/w:rw" "${new}" \
        python3 /td/make_fixtures.py /w/inputs/fixtures > "${out}/logs/fixtures.log" 2>&1
    cp "${out}"/inputs/fixtures/{plane,wall}_?.tif "${out}/inputs/"
fi
# name:EPSG of every DEM ("-": use the CRS stored in the file)
dems=(mcn_10m:26913)
if [[ -n "${EEMT_TD_GORDONGULCH:-}" ]]; then
    gg_sha=2a0a5f0b9a3a74550c208a1ecd54e1c3c26993e6c670928f8c5a1c73880644b9
    cp "${EEMT_TD_GORDONGULCH}" "${out}/inputs/gordongulch.tif"
    echo "${gg_sha}  ${out}/inputs/gordongulch.tif" | sha256sum -c - \
        || { echo "EEMT_TD_GORDONGULCH: sha256 mismatch" >&2; exit 1; }
    dems+=(gordongulch:-)
else
    echo "EEMT_TD_GORDONGULCH not set: Gordon Gulch DEM skipped"
fi
for f in plane_N plane_E plane_S plane_W wall_N wall_E wall_S wall_W; do
    dems+=("${f}:32612")
done
(cd "${out}/inputs" && sha256sum ./*.tif) | tee "${out}/inputs/SHA256SUMS"

# --- run ------------------------------------------------------------------------
joblist="${out}/jobs.txt"
: > "${joblist}"
for flavour in legacy new; do
    img="${legacy}"; [[ "${flavour}" == new ]] && img="${new}"
    echo "${img} ${flavour} versions mcn_10m 26913 versions/${flavour}" >> "${joblist}"
    for rep in $(seq 1 "${reps}"); do
        for d in "${dems[@]}"; do
            echo "${img} ${flavour} grass ${d%%:*} ${d##*:} grass/${flavour}-r${rep}/${d%%:*}" >> "${joblist}"
        done
    done
done
for d in "${dems[@]}"; do
    echo "${new} new gdal-default ${d%%:*} ${d##*:} gdal/new/${d%%:*}"
    echo "${legacy} legacy gdal-conda ${d%%:*} ${d##*:} gdal/legacy-conda/${d%%:*}"
    echo "${legacy} legacy gdal-apt ${d%%:*} ${d##*:} gdal/legacy-apt/${d%%:*}"
done >> "${joblist}"
echo "$(wc -l < "${joblist}") jobs, ${jobs} in parallel"
rc=0
xargs -P "${jobs}" -L 1 bash "${here}/run.sh" _job < "${joblist}" | tee "${out}/logs/jobs.log" || rc=$?
[[ ${rc} -eq 0 ]] || { echo "some jobs failed (see ${out}/logs/jobs.log)" >&2; exit 1; }

# --- compare --------------------------------------------------------------------
compare() {
    # $1 = pair name, $2 = A dir (relative to OUTDIR), $3 = B dir, $4 = dem
    mkdir -p "${out}/compare/$1"
    docker run --rm --network none --name "eemt-td-$$-cmp-${RANDOM}${RANDOM}" --label eemt-work=1 \
        --user "$(id -u):$(id -g)" -e HOME=/tmp \
        -v "${here}:/td:ro" -v "${out}:/w:rw" "${new}" \
        python3 /td/compare.py "/w/$2" "/w/$3" "/w/compare/$1/$4.json"
}
{
    for d in "${dems[@]}"; do
        dem="${d%%:*}"
        compare legacy-vs-new "grass/legacy-r1/${dem}" "grass/new-r1/${dem}" "${dem}"
        if [[ "${reps}" -ge 2 ]]; then
            compare legacy-r1-vs-r2 "grass/legacy-r1/${dem}" "grass/legacy-r2/${dem}" "${dem}"
            compare new-r1-vs-r2 "grass/new-r1/${dem}" "grass/new-r2/${dem}" "${dem}"
        fi
        compare gdal-legacy-apt-vs-new "gdal/legacy-apt/${dem}" "gdal/new/${dem}" "${dem}"
        compare gdal-legacy-conda-vs-new "gdal/legacy-conda/${dem}" "gdal/new/${dem}" "${dem}"
        compare gdal-legacy-apt-vs-legacy-conda "gdal/legacy-apt/${dem}" "gdal/legacy-conda/${dem}" "${dem}"
    done
} 2>&1 | tee "${out}/compare/compare.log"

docker run --rm --network none --name "eemt-td-$$-summary" --label eemt-work=1 \
    --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "${here}:/td:ro" -v "${out}:/w:rw" "${new}" \
    python3 /td/summarize.py /w "${reps}"
echo "done: ${out}/summary.md"
