#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
#
# Container-side driver of the toolchain-delta comparison (called by run.sh).
#
#   in_image.sh FLAVOUR TASK DEM EPSG OUTDIR
#
# FLAVOUR  new     the workflow image (docker/workflow): GRASS from the conda env
#          legacy  the pre-3.0 image (docker/ubuntu/24.04, last in bd19386). Its
#                  default environment points GISBASE at /usr/lib/grass84, which
#                  the apt package (GRASS 8.3.2) does not create; the override
#                  below is the one recorded in the validation notes.
# TASK     grass         grass_suite.sh, then grass_rsum.sh in a fresh location
#          gdal-default  new image: gdal_ops.sh with the image's default PATH
#          gdal-conda    legacy only: gdal_ops.sh with the image's default PATH
#                        (the conda env's GDAL 3.11.5 / PROJ 9.7.1, which the
#                        pipeline's Python code calls) plus PROJ_DATA pointing at
#                        that env's proj.db. Without PROJ_DATA (the image as
#                        shipped) this GDAL cannot resolve any EPSG code:
#                        "proj_create_from_database: Open of
#                        /opt/conda/envs/eemt-gis/share/proj failed".
#          gdal-apt      gdal_ops.sh with /usr/bin first (legacy only: the apt
#                        GDAL/PROJ that the legacy GRASS is linked to)
#          versions      g.version -rge, module interface descriptions, ldd
set -euo pipefail

flavour="$1"
task="$2"
dem="$3"
epsg="$4"
out="$5"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export USER="${USER:-eemt}" LOGNAME="${LOGNAME:-eemt}"
mkdir -p "${out}"

grass_exec() {
    # $1 = georeferenced file for the temporary location; rest = command
    local geo="$1"
    shift
    case "${flavour}" in
        new) grass --tmp-project "${geo}" --exec "$@" ;;
        legacy) /usr/bin/python3 -W ignore /usr/bin/grass --tmp-location "${geo}" --exec "$@" ;;
        *) echo "unknown flavour ${flavour}" >&2; exit 2 ;;
    esac
}

if [[ "${flavour}" == legacy && "${task}" != gdal-* ]]; then
    export GISBASE=/usr/lib/grass83
    export LD_LIBRARY_PATH=/usr/lib/grass83/lib
    export PATH="/usr/lib/grass83/bin:/usr/lib/grass83/scripts:${PATH}"
    export GRASS_PYTHON=/usr/bin/python3
    unset PYTHONPATH
fi

case "${task}" in
    grass)
        grass_exec "${dem}" bash "${here}/grass_suite.sh" "${dem}" "${out}"
        grass_exec "${out}/daily/total_sun_day_1.tif" \
            bash "${here}/grass_rsum.sh" "${out}/daily" "${out}"
        ;;
    gdal-default)
        bash "${here}/gdal_ops.sh" "${dem}" "${epsg}" "${out}"
        ;;
    gdal-conda)
        [[ "${flavour}" == legacy ]] || { echo "gdal-conda is for the legacy image" >&2; exit 2; }
        PROJ_DATA="$(cd "$(dirname "$(command -v gdalwarp)")/../share/proj" && pwd)" \
            bash "${here}/gdal_ops.sh" "${dem}" "${epsg}" "${out}"
        ;;
    gdal-apt)
        [[ "${flavour}" == legacy ]] || { echo "gdal-apt is for the legacy image" >&2; exit 2; }
        env -u GDAL_DATA -u GDAL_DRIVER_PATH -u PROJ_LIB -u PROJ_DATA -u PYTHONPATH \
            -u LD_LIBRARY_PATH PATH=/usr/bin:/bin \
            bash "${here}/gdal_ops.sh" "${dem}" "${epsg}" "${out}"
        ;;
    versions)
        # shellcheck disable=SC2016  # expanded inside the GRASS session
        grass_exec "${dem}" bash -c '
            set -euo pipefail
            out="$1"
            g.version -rge > "${out}/g.version.txt"
            for m in r.sun r.horizon r.slope.aspect r.series r.in.gdal r.out.gdal; do
                "${m}" --interface-description > "${out}/${m}.xml"
            done
            command -v r.sun > "${out}/which.txt"
            ldd "$(command -v r.sun)" | grep -E "gomp|gdal|proj" > "${out}/ldd-r.sun.txt" || true
        ' versions "${out}"
        ;;
    *)
        echo "unknown task ${task}" >&2
        exit 2
        ;;
esac
