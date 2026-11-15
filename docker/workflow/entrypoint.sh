#!/bin/bash
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
#
# Entrypoint of the EEMT workflow image. The environment itself (PATH, GISBASE,
# GDAL/PROJ settings) is static ENV in the Dockerfile; this script only fixes up
# what depends on the UID the container runs as:
# - USER and LOGNAME: getpass.getuser() and GRASS need a name, and a UID passed
#   with `--user` may have no passwd entry.
# - HOME: must be writable (GRASS writes its settings there). When it is not, for
#   example with a read-only root filesystem, a private directory under
#   ${TMPDIR:-/tmp} is used instead.
set -euo pipefail

name="$(id -un 2>/dev/null)" || name="eemt-$(id -u)"
export USER="${name}" LOGNAME="${name}"

if [[ -z "${HOME:-}" || ! -d "${HOME}" || ! -w "${HOME}" ]]; then
    HOME="$(mktemp -d "${TMPDIR:-/tmp}/eemt-home.XXXXXX")"
    export HOME
fi

exec "$@"
