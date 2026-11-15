#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
#
# Fail when a build input is not pinned to an immutable reference.
#
#   scripts/locks/check.sh actions     GitHub Actions `uses:` pinned to a 40-hex commit SHA
#   scripts/locks/check.sh images      Dockerfile `FROM` and `COPY --from=<image>` pinned as
#                                      name:tag@sha256:<digest> (the tag lets refresh.sh
#                                      re-resolve it)
#   scripts/locks/check.sh downloads   curl/wget/git-clone in Dockerfiles verified by checksum
#   scripts/locks/check.sh locks       every lock input has its lock and was refreshed since
#                                      its last edit; lock files are fully pinned and hashed;
#                                      Dockerfiles that install a conda lock assert the result
#   scripts/locks/check.sh all         every check above
#
# Run from anywhere inside the repository. No network, no containers. Exit status 1 on
# any violation.
set -euo pipefail

ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
cd "$ROOT"
# shellcheck source=scripts/locks/lib.sh
source scripts/locks/lib.sh

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
                elif [[ ! "${image%@*}" =~ :[A-Za-z0-9._-]+$ ]]; then
                    report "$file" "$line_no" "base image pinned without its tag (write name:tag@sha256:...): $image"
                fi
            # `COPY --from=<image>` pulls an image too, unless it names an earlier stage
            # (or a stage index).
            elif [[ "$line" =~ ^[[:space:]]*(COPY|ADD)[[:space:]].*--from=([^[:space:]]+) ]]; then
                image="${BASH_REMATCH[2]}"
                [[ "$image" =~ ^[0-9]+$ || -n "${stages[$image]:-}" ]] && continue
                if [[ ! "$image" =~ @sha256:[0-9a-f]{64}$ ]]; then
                    report "$file" "$line_no" "COPY --from image not pinned by digest: $image"
                elif [[ ! "${image%@*}" =~ :[A-Za-z0-9._-]+$ ]]; then
                    report "$file" "$line_no" "COPY --from image pinned without its tag: $image"
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

check_locks() {
    local entry dir input lock path hash want file
    local -A manifest=()

    # (1) Every input has its lock, and the input is unchanged since the last refresh
    #     (scripts/locks/inputs.sha256, written by refresh.sh).
    if [[ -f "$INPUTS_MANIFEST" ]]; then
        while read -r hash path; do
            if [[ -n "${path:-}" ]]; then manifest["$path"]="$hash"; fi
        done <"$INPUTS_MANIFEST"
    else
        report "$INPUTS_MANIFEST" 0 "missing; run scripts/locks/refresh.sh"
    fi
    for entry in "${CONDA_LOCKS[@]}"; do
        IFS='|' read -r dir input lock <<<"$entry"
        check_input "$dir/$input" "$dir/$lock" conda
    done
    for entry in "${PIP_LOCKS[@]}"; do
        IFS='|' read -r input lock <<<"$entry"
        check_input "$input" "$lock" pip
    done
    while IFS= read -r path; do
        lock_inputs | grep -qxF "$path" ||
            report "$INPUTS_MANIFEST" 0 "lists $path, which is not a lock input in scripts/locks/lib.sh"
    done < <(printf '%s\n' "${!manifest[@]}")

    # (2) pip locks: only `name==version` requirements, each with --hash=sha256.
    while IFS= read -r file; do
        check_requirements "$file"
    done < <(git ls-files | grep -E '(^|/)requirements[^/]*\.txt$' || true)

    # (3) conda locks: a sha256 for every package.
    while IFS= read -r file; do
        check_conda_lock "$file"
    done < <(git ls-files | grep -E '(^|/)[^/]*-lock\.yml$' || true)

    # (4) Exact pins shared with the workflow image are the same release in every
    #     other conda lock (e.g. notebooks: `gdal=3.12.3` must match the image's gdal).
    check_aligned

    # (5) Projects locked by their own toolchain have their lock tracked next to them.
    for entry in "${NATIVE_LOCKS[@]}"; do
        check_native "$entry"
    done

    # (6) A Dockerfile that installs a conda lock must assert the installed set with
    #     scripts/locks/assert-conda-env.sh (micromamba's empty-environment trap).
    while IFS= read -r file; do
        if grep -qE '\bmicromamba[[:space:]]+(create|install)\b' "$file" &&
            ! grep -q 'assert-conda-env' "$file"; then
            report "$file" "$(grep -nE '\bmicromamba[[:space:]]+(create|install)\b' "$file" | head -1 | cut -d: -f1)" \
                "micromamba install without scripts/locks/assert-conda-env.sh"
        fi
    done < <(git ls-files | grep -E '(^|/)Dockerfile(\.[^/]+)?$' || true)
}

check_input() {
    # check_input <input> <lock> <kind>; uses `manifest` from check_locks
    local input="$1" lock="$2" kind="$3" hash
    if ! git ls-files --error-unmatch "$input" >/dev/null 2>&1; then
        report "$input" 0 "$kind lock input listed in scripts/locks/lib.sh is not tracked"
        return
    fi
    git ls-files --error-unmatch "$lock" >/dev/null 2>&1 ||
        report "$lock" 0 "lock of $input is not tracked; run scripts/locks/refresh.sh $kind"
    hash="$(sha256sum "$input" | cut -d' ' -f1)"
    if [[ -z "${manifest[$input]:-}" ]]; then
        report "$input" 0 "not in $INPUTS_MANIFEST; run scripts/locks/refresh.sh $kind"
    elif [[ "${manifest[$input]}" != "$hash" ]]; then
        report "$input" 0 "changed since its last refresh; run scripts/locks/refresh.sh $kind"
    fi
}

check_requirements() {
    # Join backslash-continued lines; every logical line that is not a comment must be
    # `name[extras]==version [; marker]` (uv writes several extras as `[a, b]`) followed by at least one --hash=sha256:<64 hex>.
    local file="$1" line_no=0 start=0 logical="" line req
    while IFS= read -r line || [[ -n "$line" ]]; do
        line_no=$((line_no + 1))
        ((start == 0)) && start=$line_no
        if [[ "$line" == *\\ ]]; then
            logical+="${line%\\} "
            continue
        fi
        logical+="$line"
        logical="${logical%%#*}"
        logical="$(sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//' <<<"$logical")"
        if [[ -n "$logical" ]]; then
            req="${logical%% --hash=*}"
            if [[ ! "$req" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9._,[:space:]-]+\])?==[^[:space:]\;=\<\>!~*]+([[:space:]]*\;.*)?$ ]]; then
                report "$file" "$start" "not an exact '==' pin: ${req:0:80}"
            elif [[ ! "$logical" =~ --hash=sha256:[0-9a-f]{64} ]]; then
                report "$file" "$start" "no --hash=sha256 for: ${req:0:80}"
            fi
        fi
        logical=""
        start=0
    done <"$file"
}

check_conda_lock() {
    # Each package entry ("- name: ...") must carry "sha256: <64 hex>" under "hash:".
    local file="$1" bad
    bad="$(awk '
        function flush() { if (name != "" && !ok) print line_no ":" name; name = ""; ok = 0 }
        /^package:/ { inpkg = 1; next }
        !inpkg { next }
        /^- name: / { flush(); name = $3; line_no = NR; next }
        /^    sha256: [0-9a-f]{64}[[:space:]]*$/ { ok = 1 }
        END { flush() }
    ' "$file")"
    if ! grep -q '^- name: ' "$file"; then
        report "$file" 0 "conda lock lists no package"
    fi
    while IFS=: read -r ln name; do
        if [[ -n "$ln" ]]; then report "$file" "$ln" "package $name has no sha256"; fi
    done <<<"$bad"
}

lock_versions() {
    # lock_versions <conda lock>: "name version" for every conda package
    awk '
        /^package:/ { inpkg = 1; next }
        !inpkg { next }
        /^- name: / { name = $3; next }
        /^  version: / { v = $2; gsub(/["\047]/, "", v); ver = v; next }
        /^  manager: conda/ { if (name != "") print name, ver }
    ' "$1"
}

check_aligned() {
    local entry dir input lock name want got env_file
    local -A reference=()
    [[ -f "$ALIGN_REFERENCE" ]] || return 0
    while read -r name want; do reference["$name"]="$want"; done < <(lock_versions "$ALIGN_REFERENCE")
    for entry in "${CONDA_LOCKS[@]}"; do
        IFS='|' read -r dir input lock <<<"$entry"
        [[ "$dir/$lock" == "$ALIGN_REFERENCE" || "$dir" == scripts/locks/tools ]] && continue
        env_file="$dir/$input"
        [[ -f "$env_file" && -f "$dir/$lock" ]] || continue
        # Exact pins in the input: "  - name=1.2.3" (one '=', no operator, no wildcard).
        while read -r name; do
            want="${reference[$name]:-}"
            [[ -z "$want" ]] && continue
            got="$(lock_versions "$dir/$lock" | awk -v n="$name" '$1 == n {print $2}')"
            if [[ "$got" != "$want" ]]; then
                report "$dir/$lock" 0 "$name ${got:-absent} differs from $ALIGN_REFERENCE ($want)"
            fi
        done < <(sed -nE 's/^[[:space:]]*-[[:space:]]+([A-Za-z0-9._-]+)=[0-9][^=<>*,[:space:]]*([[:space:]]*#.*)?$/\1/p' "$env_file")
    done
}

check_native() {
    local project lock exemption finding until today
    IFS='|' read -r project lock exemption <<<"$1"
    git ls-files --error-unmatch "$project" >/dev/null 2>&1 || return 0
    git ls-files --error-unmatch "$lock" >/dev/null 2>&1 && return 0
    if [[ -n "$exemption" ]]; then
        finding="${exemption%%:*}"
        until="${exemption#*:}"
        today="$(date -u +%F)"
        if [[ "$today" < "$until" || "$today" == "$until" ]]; then
            echo "locks check: note: $lock is not tracked (exempt until $until, $finding)" >&2
            return 0
        fi
        report "$lock" 0 "not tracked next to $project (exemption $finding expired on $until)"
        return
    fi
    report "$lock" 0 "not tracked next to $project"
}

case "${1:-all}" in
    actions) check_actions ;;
    images) check_images ;;
    downloads) check_downloads ;;
    locks) check_locks ;;
    all)
        check_actions
        check_images
        check_downloads
        check_locks
        ;;
    *)
        echo "usage: $0 [actions|images|downloads|locks|all]" >&2
        exit 2
        ;;
esac

if ((violations > 0)); then
    echo "locks check: $violations violation(s)" >&2
    exit 1
fi
echo "locks check: ok (${1:-all})"
