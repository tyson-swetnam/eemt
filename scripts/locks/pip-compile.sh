#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
#
# Regenerate the hashed pip lock files from their .in sources with uv, run in a
# container pulled by digest (no host install):
#
#   web-interface/requirements.in  -> web-interface/requirements.txt   (web image)
#   requirements-docs.in           -> requirements-docs.txt            (docs image, CI)
#   requirements-dev.in            -> requirements-dev.txt             (tests, lint)
#
#   scripts/locks/pip-compile.sh            re-resolve, keeping current pins where allowed
#   scripts/locks/pip-compile.sh --upgrade  move every pin to the newest allowed release
#
# Target: CPython 3.14 on x86_64 manylinux (the python:3.14 slim images). The web lock is
# compiled first because requirements-dev.in is constrained by it.
set -euo pipefail

UV_IMAGE="ghcr.io/astral-sh/uv:0.13.8-python3.14-trixie-slim@sha256:81a1340fea1e72ad4a8e9a016128b0c61041cf182ea49fd788023e52842bc147"

ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
upgrade=()
[[ "${1:-}" == "--upgrade" ]] && upgrade=(--upgrade)

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

compile() {
    # compile <input .in> <output .txt>, both relative to the repository root
    docker run --rm --label eemt-work=1 \
        --user "$(id -u):$(id -g)" \
        -e HOME=/scratch -e UV_CACHE_DIR=/scratch/cache \
        -v "$ROOT:/repo" -v "$scratch:/scratch" -w /repo \
        "$UV_IMAGE" \
        uv pip compile "$1" -o "$2" \
            --generate-hashes \
            --python-version 3.14 \
            --python-platform x86_64-manylinux_2_28 \
            --no-strip-extras \
            --custom-compile-command "scripts/locks/pip-compile.sh" \
            "${upgrade[@]}"
}

compile web-interface/requirements.in web-interface/requirements.txt
compile requirements-docs.in requirements-docs.txt
compile requirements-dev.in requirements-dev.txt
