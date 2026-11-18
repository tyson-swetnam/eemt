# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
#
# Shared by scripts/locks/refresh.sh and scripts/locks/check.sh (sourced, not run).
# Lists every lock input with the lock file generated from it, and the digest-pinned
# tool images that regenerate them.

# Tool images. `scripts/locks/refresh.sh images` re-resolves these tags too.
MICROMAMBA_IMAGE="mambaorg/micromamba:2.10.0-ubuntu24.04@sha256:c3897a06f87c1c13f038188b3665a8c4162da62eac5109ae6a939d1e290dcefa"
UV_IMAGE="ghcr.io/astral-sh/uv:0.13.8-python3.14-trixie-slim@sha256:81a1340fea1e72ad4a8e9a016128b0c61041cf182ea49fd788023e52842bc147"
GIT_IMAGE="alpine/git:v2.52.0@sha256:4a0e72d49596a1f5d3701aeedafdadc5c0da4062be4657c7bdc4017387f591cc"
CRANE_IMAGE="gcr.io/go-containerregistry/crane:v0.22.1@sha256:1f968817b95790bed063f71175aa6b8ff879fa17064020415f3e18bb6e6a36e1"
# Vulnerability scanner for `check.sh vulns` (OSV database: PyPI and crates.io advisories).
OSV_SCANNER_IMAGE="ghcr.io/google/osv-scanner:v2.7.1@sha256:a70ffffc2bfd33482953e5d6b64c6fcdb30239717a07b0f0d3ab2859ce3e4cde"

# Advisories `check.sh vulns` accepts, each with a reason and an expiry date.
VULN_ALLOWLIST="scripts/locks/osv-scanner.toml"

# conda-lock itself runs from a hash-locked environment (inputs below, first entry), so
# the solver version does not float between refreshes.
LOCKTOOL_LOCK="scripts/locks/tools/locktool-lock.yml"

# Conda environments: "<directory>|<input file>|<lock file>", paths relative to the
# directory. conda-lock records the input name in the lock ("sources:"), so it always
# runs from inside <directory>. Lock names must end in "-lock.yml": micromamba reads any
# other name as a plain environment file.
CONDA_LOCKS=(
    "scripts/locks/tools|locktool.yml|locktool-lock.yml"
    "docker/workflow|environment.yml|conda-lock.yml"
    "notebooks|environment.yml|conda-lock.yml"
    "notebooks|environment-minimal.yml|minimal-conda-lock.yml"
)

# pip: "<input .in>|<hashed .txt>", relative to the repository root, in compile order
# (requirements-dev.in is constrained by the web lock).
PIP_LOCKS=(
    "web-interface/requirements.in|web-interface/requirements.txt"
    "requirements-docs.in|requirements-docs.txt"
    "requirements-dev.in|requirements-dev.txt"
)

# sha256 of every lock input above, written by refresh.sh after it regenerated the
# locks. check.sh fails when an input no longer matches, i.e. it was edited without a
# refresh.
INPUTS_MANIFEST="scripts/locks/inputs.sha256"

# Exact pins (`- name=1.2.3`) in the other conda environments must name the release this
# lock has, so notebooks and the workflow image run the same GRASS/GDAL/PROJ/Python.
ALIGN_REFERENCE="docker/workflow/conda-lock.yml"

# Projects whose lock is produced by their own toolchain (not by refresh.sh):
# "<project file>|<lock file that must be tracked>|<exemption>". An exemption is
# "<finding id>:<YYYY-MM-DD>": a missing lock is reported as a note, and fails the check
# only after that date (UTC). Lean's lake-manifest.json is ignored by
# lean4-verification/.gitignore (Area B's file) until Area B commits it.
NATIVE_LOCKS=(
    "rsun/Cargo.toml|rsun/Cargo.lock|"
    "lean4-verification/lakefile.lean|lean4-verification/lake-manifest.json|F-A-031:2027-01-31"
)

# Every lock input, repository-relative (for the manifest).
lock_inputs() {
    local entry dir input lock
    for entry in "${CONDA_LOCKS[@]}"; do
        IFS='|' read -r dir input lock <<<"$entry"
        printf '%s/%s\n' "$dir" "$input"
    done
    for entry in "${PIP_LOCKS[@]}"; do
        printf '%s\n' "${entry%%|*}"
    done
}
