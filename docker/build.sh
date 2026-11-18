#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
#
# Build one EEMT image and tag it <name>:<version>, where <version> is the content
# of docker/VERSION. No other tag (no "latest", no alias) is applied.
#
#   docker/build.sh [workflow|web|docs] [extra docker build arguments...]
#
#   workflow (default)  docker/workflow/Dockerfile       -> eemt:<version>
#   web                 docker/web-interface/Dockerfile  -> eemt-web:<version>
#   docs                docker/docs/Dockerfile           -> eemt-docs:<version>
#
# Environment:
#   EEMT_IMAGE_NAME   repository part of the tag (default: eemt, eemt-web or eemt-docs)
#
# Examples:
#   docker/build.sh                      # -> eemt:3.0.0
#   docker/build.sh --no-cache --pull    # clean rebuild of the workflow image
#   docker/build.sh web --build-arg EEMT_DOCKER_GID="$(stat -c %g /var/run/docker.sock)"
#   EEMT_IMAGE_NAME=eemt-work/eemt docker/build.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

target=workflow
case "${1:-}" in
    workflow | web | docs)
        target="$1"
        shift
        ;;
esac
case "${target}" in
    workflow) dockerfile=docker/workflow/Dockerfile name=eemt ;;
    web) dockerfile=docker/web-interface/Dockerfile name=eemt-web ;;
    docs) dockerfile=docker/docs/Dockerfile name=eemt-docs ;;
esac

version="$(tr -d '[:space:]' < docker/VERSION)"
if [[ ! "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]]; then
    echo "docker/VERSION does not hold a version: '${version}'" >&2
    exit 1
fi
revision="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
created="$(git log -1 --format=%cI 2>/dev/null || echo unknown)"
image="${EEMT_IMAGE_NAME:-${name}}:${version}"

echo "Building ${image} (revision ${revision}) from ${dockerfile}"
docker build \
    -f "${dockerfile}" \
    --build-arg "EEMT_VERSION=${version}" \
    --build-arg "EEMT_REVISION=${revision}" \
    --build-arg "EEMT_CREATED=${created}" \
    -t "${image}" \
    "$@" \
    .

docker image inspect --format '{{.Id}} {{.Size}} bytes' "${image}"
case "${target}" in
    workflow) echo "Check it with: docker run --rm ${image} eemt-selftest" ;;
    web) echo "Run it with: docker-compose up eemt-web (see docker-compose.yml for EEMT_UID/EEMT_GID/EEMT_DOCKER_GID)" ;;
    docs) echo "Serve the docs with: docker-compose --profile docs up eemt-docs" ;;
esac
