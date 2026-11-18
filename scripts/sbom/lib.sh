# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
#
# Shared by scripts/sbom/{generate,validate,vuln-report}.sh (sourced, not run).
# Tool images (digest-pinned; `scripts/locks/refresh.sh images` re-resolves the tags),
# the container runner and the Python tool environment.

SYFT_IMAGE="anchore/syft:v1.57.1@sha256:03215d0e98b661d0ecbc8575dbc14d65be1743b5de5df6410aecb7e80f95b060"
GRYPE_IMAGE="anchore/grype:v0.121.3@sha256:13113eeceab017260370cf540e1d25442c59665fd07d1b09b66d63bb0c282887"
PYTHON_IMAGE="python:3.14.10-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f"
RUST_IMAGE="rust:1.100.0-slim@sha256:e9d95b74296f7126a83ae3d041aeaba1ff4c478f13abc5cc034fcf7c131c3d4a"

# Python tools (cyclonedx-py, cyclonedx-python-lib, pyspdxtools, ntia-checker, pyyaml,
# pytest) come from this hashed lock, installed into a cached venv in PYTHON_IMAGE.
SBOM_PY_LOCK="requirements-dev.txt"

# Rust toolchain used for `cargo metadata` (licences of the crates in rsun/Cargo.lock).
# Naming it overrides rsun/rust-toolchain.toml, whose extra components (clippy, rustfmt,
# wasm32) would otherwise be downloaded first; metadata needs none of them.
SBOM_RUST_TOOLCHAIN="1.100.0"

SBOM_ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"

# sbom_cache_init: sets SBOM_CACHE (EEMT_SBOM_CACHE, or a temp dir removed on exit).
sbom_cache_init() {
    if [[ -n "${EEMT_SBOM_CACHE:-}" ]]; then
        SBOM_CACHE="$(mkdir -p "$EEMT_SBOM_CACHE" && cd "$EEMT_SBOM_CACHE" && pwd)"
    else
        SBOM_CACHE="$(mktemp -d)"
        # shellcheck disable=SC2064
        trap "rm -rf '$SBOM_CACHE'" EXIT
    fi
    mkdir -p "$SBOM_CACHE/home" "$SBOM_CACHE/tmp"
}

sbom_container_no=0
# sbom_run [docker run options...] <image> [command...]
# Runs as the calling user with the repository at /repo (working directory) and the
# cache at /cache. No published ports.
sbom_run() {
    sbom_container_no=$((sbom_container_no + 1))
    docker run --rm --name "eemt-sbom-$$-${sbom_container_no}" --label eemt-work=1 \
        --user "$(id -u):$(id -g)" -e HOME=/cache/home -e TMPDIR=/cache/tmp \
        -v "$SBOM_ROOT:/repo" -v "$SBOM_CACHE:/cache" -w /repo "$@"
}

# sbom_py_env: create (once per lock content) the venv /cache/venv-<sha256 of lock>.
sbom_py_env() {
    local want
    want="$(sha256sum "$SBOM_ROOT/$SBOM_PY_LOCK" | cut -c1-16)"
    SBOM_VENV="/cache/venv-$want"
    if [[ ! -f "$SBOM_CACHE/venv-$want/.complete" ]]; then
        echo "== python tools: installing $SBOM_PY_LOCK into $SBOM_VENV" >&2
        rm -rf "${SBOM_CACHE:?}/venv-$want"
        sbom_run -e PIP_DISABLE_PIP_VERSION_CHECK=1 -e PIP_NO_CACHE_DIR=1 "$PYTHON_IMAGE" \
            sh -c "python -m venv '$SBOM_VENV' && '$SBOM_VENV/bin/pip' install -q \
                   --require-hashes --no-deps -r '$SBOM_PY_LOCK'" >&2
        touch "$SBOM_CACHE/venv-$want/.complete"
    fi
}

# sbom_py [docker run options...] -- <args...>: run the tool venv's python in
# PYTHON_IMAGE with the network off.
sbom_py() {
    local opts=()
    while [[ $# -gt 0 && "$1" != -- ]]; do opts+=("$1"); shift; done
    [[ "${1:-}" == -- ]] && shift
    sbom_run --network none -e PYTHONDONTWRITEBYTECODE=1 "${opts[@]}" \
        "$PYTHON_IMAGE" "$SBOM_VENV/bin/python" "$@"
}
