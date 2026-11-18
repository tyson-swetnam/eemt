#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
#
# Assert that a conda prefix holds exactly what a conda-lock file lists.
#
#   assert-conda-env.sh <lock-file> <prefix> [platform]     (platform default: linux-64)
#
# Guards against micromamba's empty-environment trap: given a lock file whose name does
# not end in "-lock.yml", `micromamba create -f` reads it as a plain environment file,
# may create an EMPTY (or wrong) environment and still exit 0. This script fails unless
#   - every conda package of the lock is installed with the locked name-version-build
#     (from conda-meta/*.json) and nothing else is, i.e. installed count == lock count;
#   - every pip package of the lock has a matching *.dist-info in site-packages.
# Called from Dockerfiles right after the install (docker/workflow/Dockerfile). Needs
# only bash, awk, sort and comm, so it runs in the micromamba and Ubuntu images alike.
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
    echo "usage: $0 <lock-file> <prefix> [platform]" >&2
    exit 2
fi
lock="$1"
prefix="$2"
platform="${3:-linux-64}"

[[ -f "$lock" ]] || { echo "assert-conda-env: no lock file $lock" >&2; exit 1; }

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

# Lock entries for this platform: "conda <name-version-build>" or "pip <name> <version>".
# Names of pip packages are normalised the way *.dist-info directories spell them
# (lower case, runs of - _ . become _).
awk -v want="$platform" '
    function flush() {
        if (name != "" && plat == want) {
            if (mgr == "conda") {
                n = split(url, parts, "/"); dist = parts[n]
                sub(/\.(conda|tar\.bz2)$/, "", dist)
                print "conda " dist
            } else if (mgr == "pip") {
                key = tolower(name); gsub(/[-_.]+/, "_", key)
                print "pip " key " " ver
            }
        }
        name = ""; ver = ""; mgr = ""; plat = ""; url = ""
    }
    /^package:/ { inpkg = 1; next }
    !inpkg { next }
    /^- name: / { flush(); name = $3; next }
    /^  version: / { ver = $2; gsub(/["\047]/, "", ver); next }
    /^  manager: / { mgr = $2; next }
    /^  platform: / { plat = $2; next }
    /^  url: / { url = $2; next }
    END { flush() }
' "$lock" >"$scratch/lock"

awk '$1 == "conda" {print $2}' "$scratch/lock" | LC_ALL=C sort >"$scratch/want-conda"
awk '$1 == "pip" {print $2 "-" $3}' "$scratch/lock" | LC_ALL=C sort >"$scratch/want-pip"

if [[ -d "$prefix/conda-meta" ]]; then
    find "$prefix/conda-meta" -maxdepth 1 -name '*.json' -printf '%f\n' |
        sed 's/\.json$//' | LC_ALL=C sort >"$scratch/have-conda"
else
    : >"$scratch/have-conda"
fi
find "$prefix"/lib/python*/site-packages -maxdepth 1 -name '*.dist-info' -printf '%f\n' 2>/dev/null |
    sed 's/\.dist-info$//' |
    awk '{ i = index($0, "-"); n = tolower(substr($0, 1, i - 1)); gsub(/[-_.]+/, "_", n); print n "-" substr($0, i + 1) }' |
    LC_ALL=C sort >"$scratch/have-pip" || true

locked=$(wc -l <"$scratch/want-conda")
installed=$(wc -l <"$scratch/have-conda")
pip_locked=$(wc -l <"$scratch/want-pip")
missing=$(LC_ALL=C comm -23 "$scratch/want-conda" "$scratch/have-conda")
extra=$(LC_ALL=C comm -13 "$scratch/want-conda" "$scratch/have-conda")
pip_missing=$(LC_ALL=C comm -23 "$scratch/want-pip" "$scratch/have-pip")

echo "assert-conda-env: $lock ($platform): conda locked=$locked installed=$installed; pip locked=$pip_locked"
status=0
if ((locked == 0)); then
    echo "assert-conda-env: the lock lists no conda package for $platform" >&2
    status=1
fi
if [[ -n "$missing" ]]; then
    echo "assert-conda-env: locked but not installed:" >&2
    head -20 <<<"$missing" | sed 's/^/  /' >&2
    status=1
fi
if [[ -n "$extra" ]]; then
    echo "assert-conda-env: installed but not in the lock:" >&2
    head -20 <<<"$extra" | sed 's/^/  /' >&2
    status=1
fi
if [[ -n "$pip_missing" ]]; then
    echo "assert-conda-env: pip packages locked but not installed:" >&2
    head -20 <<<"$pip_missing" | sed 's/^/  /' >&2
    status=1
fi
exit "$status"
