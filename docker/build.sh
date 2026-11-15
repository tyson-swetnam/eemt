#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
#
# Build the EEMT workflow image from docker/workflow/Dockerfile and tag it
# eemt:<version>, where <version> is the content of docker/VERSION. No other tag
# (no "latest", no alias) is applied.
#
#   docker/build.sh [extra docker build arguments...]
#
# Environment:
#   EEMT_IMAGE_NAME   repository part of the tag (default: eemt)
#
# Examples:
#   docker/build.sh                      # -> eemt:3.0.0
#   docker/build.sh --no-cache --pull    # clean rebuild
#   EEMT_IMAGE_NAME=eemt-work/eemt docker/build.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

version="$(tr -d '[:space:]' < docker/VERSION)"
if [[ ! "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]]; then
    echo "docker/VERSION does not hold a version: '${version}'" >&2
    exit 1
fi
revision="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
created="$(git log -1 --format=%cI 2>/dev/null || echo unknown)"
image="${EEMT_IMAGE_NAME:-eemt}:${version}"

echo "Building ${image} (revision ${revision}) from docker/workflow/Dockerfile"
docker build \
    -f docker/workflow/Dockerfile \
    --build-arg "EEMT_VERSION=${version}" \
    --build-arg "EEMT_REVISION=${revision}" \
    --build-arg "EEMT_CREATED=${created}" \
    -t "${image}" \
    "$@" \
    .

docker image inspect --format '{{.Id}} {{.Size}} bytes' "${image}"
echo "Check it with: docker run --rm ${image} eemt-selftest"
