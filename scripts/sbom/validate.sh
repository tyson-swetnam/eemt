#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
#
# Validate the committed SBOMs in sbom/. Every check is gating (exit status 1 if any
# fails), except the NTIA exemptions listed in NTIA_NONBLOCKING below.
#
#   scripts/sbom/validate.sh [--skip-image-scan]
#
#   1. CycloneDX: every sbom/*.cdx.json is CycloneDX 1.7 and valid against the strict
#      1.7 JSON schema (cyclonedx-python-lib).
#   2. SPDX: every sbom/*.spdx.json passes pyspdxtools (spdx-tools, SPDX 2.3).
#   3. NTIA minimum elements (ntia-conformance-checker) on every SPDX document.
#   4. Cross-checks (scripts/sbom/crosscheck.py): image conda set == conda lock, web
#      image PyPI set == web lock, every tracked lock file in sbom/manifest.json with its
#      sha256, CycloneDX/SPDX twins agree, aggregate consistent with its members, no
#      labels, image ids, user, host or home directory in the documents.
#   5. Secret scan of the built images (scripts/sbom/secret_scan.py on `docker export`):
#      no Work Queue password, .netrc, jobs.db, uploaded files, private keys. Needs the
#      images <EEMT_SBOM_IMAGE_PREFIX><name>:<docker/VERSION>; --skip-image-scan skips
#      it and says so.
#
# Environment: EEMT_SBOM_IMAGE_PREFIX, EEMT_SBOM_CACHE as in generate.sh. No network
# after the tool venv exists.
set -euo pipefail

# shellcheck source=scripts/sbom/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$SBOM_ROOT"

scan_images=1
for arg in "$@"; do
    case "$arg" in
        --skip-image-scan) scan_images=0 ;;
        *)
            echo "usage: $0 [--skip-image-scan]" >&2
            exit 2
            ;;
    esac
done

# Documents for which the NTIA check reports but does not gate: "<file>|<reason>".
# Empty: every document passes with honest suppliers (see sbom/README.md).
NTIA_NONBLOCKING=()

CONFIG=scripts/sbom/targets.toml
VERSION="$(tr -d '[:space:]' <docker/VERSION)"
PREFIX="${EEMT_SBOM_IMAGE_PREFIX:-}"
sbom_cache_init
sbom_py_env
mkdir -p "$SBOM_CACHE/work"

failed=()
step() { echo; echo "== $*"; }

step "CycloneDX 1.7 schema"
mapfile -t cdx < <(ls sbom/*.cdx.json)
sbom_py -- scripts/sbom/cdx_validate.py "${cdx[@]}" || failed+=("cyclonedx-schema")

step "SPDX 2.3 (pyspdxtools)"
for f in sbom/*.spdx.json; do
    if out="$(sbom_py -- -m spdx_tools.spdx.clitools.pyspdxtools -i "$f" 2>&1)"; then
        echo "ok    $f"
    else
        echo "FAIL  $f"
        printf '%s\n' "$out" | head -20
        failed+=("pyspdxtools:$f")
    fi
done

step "NTIA minimum elements (ntia-conformance-checker)"
for f in sbom/*.spdx.json; do
    reason=""
    for entry in "${NTIA_NONBLOCKING[@]}"; do
        [[ "${entry%%|*}" == "$f" ]] && reason="${entry#*|}"
    done
    if out="$(sbom_py -- -m ntia_conformance_checker.main --file "$f" 2>&1)" \
        && grep -q 'Conformant: True' <<<"$out"; then
        echo "ok    $f"
    elif [[ -n "$reason" ]]; then
        echo "WARN  $f (not gating: $reason)"
    else
        echo "FAIL  $f"
        printf '%s\n' "$out" | grep -E 'Conformant|False|Missing|^[a-z]+ \(' | head -20
        failed+=("ntia:$f")
    fi
done

step "Cross-checks"
git ls-files | grep -E '(^|/)(requirements[^/]*\.txt|[^/]*-lock\.yml|Cargo\.lock|lake-manifest\.json)$' \
    >"$SBOM_CACHE/work/locks.txt" || true
forbid=(--forbid "$HOME" --forbid "$SBOM_ROOT" --forbid "$SBOM_CACHE" --forbid "/home/$(id -un)/")
host="$(hostname -s 2>/dev/null || hostname)"
[[ ${#host} -ge 4 ]] && forbid+=(--forbid "$host")
images=()
while IFS='|' read -r name kind image build lock; do
    images+=("$name|${PREFIX}${image}:${VERSION}")
    if id="$(docker image inspect --format '{{.Id}}' "${PREFIX}${image}:${VERSION}" 2>/dev/null)"; then
        forbid+=(--forbid "${id#sha256:}")
    fi
done < <(sbom_py -- scripts/sbom/sbomlib.py list "$CONFIG" image)
sbom_py -- scripts/sbom/crosscheck.py --config "$CONFIG" --sbom-dir sbom --root . \
    --locks /cache/work/locks.txt "${forbid[@]}" || failed+=("crosscheck")

step "Secret scan of the images"
if ((scan_images)); then
    for entry in "${images[@]}"; do
        name="${entry%%|*}" ref="${entry#*|}"
        if ! docker image inspect "$ref" >/dev/null 2>&1; then
            echo "FAIL  $ref not found (build it, or pass --skip-image-scan)"
            failed+=("secret-scan:$name")
            continue
        fi
        cid="$(docker create --label eemt-work=1 "$ref" true)"
        if docker export "$cid" | sbom_py -i -- scripts/sbom/secret_scan.py --label "$ref" -; then
            :
        else
            failed+=("secret-scan:$name")
        fi
        docker rm "$cid" >/dev/null
    done
else
    echo "SKIPPED (--skip-image-scan): the image filesystems were not scanned"
fi

echo
if ((${#failed[@]})); then
    echo "validate: FAILED: ${failed[*]}"
    exit 1
fi
echo "validate: all checks passed"
