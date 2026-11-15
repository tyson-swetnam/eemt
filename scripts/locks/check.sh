#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
#
# Fail when a build input is not pinned to an immutable reference.
#
#   scripts/locks/check.sh actions     GitHub Actions `uses:` pinned to a 40-hex commit SHA
#   scripts/locks/check.sh images      Dockerfile `FROM` lines pinned by @sha256 digest
#   scripts/locks/check.sh downloads   curl/wget/git-clone in Dockerfiles verified by checksum
#   scripts/locks/check.sh all         every check above
#
# Run from anywhere inside the repository. Exit status 1 on any violation.
set -euo pipefail

ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
cd "$ROOT"

violations=0
report() {
    # report <file> <line-number> <message>
    printf '%s:%s: %s\n' "$1" "$2" "$3" >&2
    violations=$((violations + 1))
}

check_actions() {
    local file line_no line ref
    while IFS= read -r file; do
        line_no=0
        while IFS= read -r line || [[ -n "$line" ]]; do
            line_no=$((line_no + 1))
            # Match `uses: owner/repo[/path]@ref`, ignoring comments and local (./) actions.
            if [[ "$line" =~ ^[[:space:]-]*uses:[[:space:]]*([^[:space:]#]+) ]]; then
                ref="${BASH_REMATCH[1]}"
                [[ "$ref" == ./* || "$ref" == docker://*@sha256:* ]] && continue
                if [[ ! "$ref" =~ @[0-9a-f]{40}$ ]]; then
                    report "$file" "$line_no" "action not pinned to a commit SHA: $ref"
                fi
            fi
        done <"$file"
    done < <(git ls-files '.github/workflows/*.yml' '.github/workflows/*.yaml' '.github/actions/**/action.yml')
}

check_images() {
    local file line_no line image
    declare -A stages
    while IFS= read -r file; do
        stages=()
        line_no=0
        while IFS= read -r line || [[ -n "$line" ]]; do
            line_no=$((line_no + 1))
            if [[ "$line" =~ ^[[:space:]]*FROM[[:space:]]+(--platform=[^[:space:]]+[[:space:]]+)?([^[:space:]]+)([[:space:]]+[Aa][Ss][[:space:]]+([^[:space:]]+))? ]]; then
                image="${BASH_REMATCH[2]}"
                [[ -n "${BASH_REMATCH[4]:-}" ]] && stages["${BASH_REMATCH[4]}"]=1
                # An earlier build stage or `scratch` needs no digest.
                [[ "$image" == scratch || -n "${stages[$image]:-}" ]] && continue
                if [[ ! "$image" =~ @sha256:[0-9a-f]{64}$ ]]; then
                    report "$file" "$line_no" "base image not pinned by digest: $image"
                fi
            fi
        done <"$file"
    done < <(git ls-files | grep -E '(^|/)Dockerfile(\.[^/]+)?$' || true)
}

check_downloads() {
    # A Dockerfile that fetches over the network must verify what it fetched in
    # the same file: a sha256 check for curl/wget, a full commit SHA for git.
    local file hits
    while IFS= read -r file; do
        hits="$(grep -nE '\b(curl|wget)\b[^#]*https?://|\bgit[[:space:]]+clone\b' "$file" || true)"
        [[ -z "$hits" ]] && continue
        if ! grep -qE 'sha256sum[[:space:]]+(-c|--check)|echo[[:space:]]+"?[0-9a-f]{64}' "$file"; then
            while IFS= read -r hit; do
                report "$file" "${hit%%:*}" "network fetch without a checksum check in this file"
            done <<<"$hits"
        fi
    done < <(git ls-files | grep -E '(^|/)Dockerfile(\.[^/]+)?$' || true)
}

case "${1:-all}" in
    actions) check_actions ;;
    images) check_images ;;
    downloads) check_downloads ;;
    all)
        check_actions
        check_images
        check_downloads
        ;;
    *)
        echo "usage: $0 [actions|images|downloads|all]" >&2
        exit 2
        ;;
esac

if ((violations > 0)); then
    echo "locks check: $violations violation(s)" >&2
    exit 1
fi
echo "locks check: ok (${1:-all})"
