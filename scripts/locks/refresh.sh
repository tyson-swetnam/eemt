#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
#
# Regenerate lock files from their inputs. Every resolver runs in a container pulled by
# digest (scripts/locks/lib.sh); nothing is installed on the host.
#
#   scripts/locks/refresh.sh [--upgrade] [all|conda|pip|actions|images]
#
#   conda    conda-lock 4.1.0 (micromamba solver, linux-64) for each environment in
#            CONDA_LOCKS. Without --upgrade a lock whose recorded input hash still
#            matches its environment file is left as it is (conda-lock
#            --check-input-hash); a changed input is re-solved from scratch.
#   pip      uv pip compile --generate-hashes for each requirements .in in PIP_LOCKS
#            (CPython 3.14, x86_64 manylinux). Without --upgrade existing pins are kept
#            wherever the input still allows them.
#   actions  every `uses: owner/repo@<sha> # <tag>` in .github/: the tag in the comment
#            (or a bare `@<tag>`) is resolved to its commit SHA again.
#   images   every `name:tag@sha256:<digest>` in Dockerfiles, scripts/locks/*.sh,
#            scripts/sbom/*.sh and .github/workflows/: the tag is resolved to its
#            current digest (the multi-platform index digest).
#   all      all of the above (default).
#
#   --upgrade  conda: re-solve every environment; pip: move every pin to the newest
#              allowed release. Actions and images always follow the named tag; to
#              move to a new version, edit the tag, then refresh.
#
# Running it twice with no upstream change leaves `git status` clean. After conda or pip,
# scripts/locks/inputs.sha256 records the hash of each refreshed input
# (`scripts/locks/check.sh locks` compares it). Whoever changes a lock also regenerates
# the SBOMs in the same branch.
#
# Environment: EEMT_LOCK_CACHE=<dir> keeps the conda-lock environment and package/uv
# caches between runs (default: a temporary directory removed on exit).
# Network access is needed (conda-forge, PyPI, GitHub, container registries).
set -euo pipefail

ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
cd "$ROOT"
# shellcheck source=scripts/locks/lib.sh
source scripts/locks/lib.sh

upgrade=0
target=all
for arg in "$@"; do
    case "$arg" in
        --upgrade) upgrade=1 ;;
        all | conda | pip | actions | images) target="$arg" ;;
        *)
            echo "usage: $0 [--upgrade] [all|conda|pip|actions|images]" >&2
            exit 2
            ;;
    esac
done

if [[ -n "${EEMT_LOCK_CACHE:-}" ]]; then
    CACHE="$(mkdir -p "$EEMT_LOCK_CACHE" && cd "$EEMT_LOCK_CACHE" && pwd)"
else
    CACHE="$(mktemp -d)"
    trap 'rm -rf "$CACHE"' EXIT
fi
mkdir -p "$CACHE/home"

errors=0
container_no=0
run_tool() {
    # run_tool [docker run options...] <image> [command...]
    # Runs as the calling user, repository at /repo (working directory), cache at /cache.
    container_no=$((container_no + 1))
    docker run --rm --name "eemt-locks-$$-${container_no}" --label eemt-work=1 \
        --user "$(id -u):$(id -g)" -e HOME=/cache/home \
        -v "$ROOT:/repo" -v "$CACHE:/cache" -w /repo "$@"
}

# --- conda ---------------------------------------------------------------------

# Runs inside the micromamba image. Arguments: <check-input-hash flag or ""> then
# "<dir>|<input>|<lock>" entries.
# shellcheck disable=SC2016
CONDA_SCRIPT='
set -euo pipefail
check="$1"; shift
tool=/cache/locktool
tool_lock=/repo/'"$LOCKTOOL_LOCK"'
if [[ -f "$tool_lock" ]]; then
    want="$(sha256sum "$tool_lock" | cut -d" " -f1)"
    if [[ "$(cat "$tool/.eemt-lock-sha256" 2>/dev/null || true)" != "$want" ]]; then
        rm -rf "$tool"
        micromamba create -y -q -p "$tool" -f "$tool_lock"
        echo "$want" >"$tool/.eemt-lock-sha256"
    fi
else
    # First run only: no tool lock yet. Bootstrap from the pinned versions; the first
    # entry of CONDA_LOCKS then writes the tool lock, which later runs install from.
    rm -rf "$tool"
    micromamba create -y -q -p "$tool" -c conda-forge --override-channels python=3.13 conda-lock=4.1.0
    echo bootstrap >"$tool/.eemt-lock-sha256"
fi
micromamba run -p "$tool" conda-lock --version
for entry in "$@"; do
    IFS="|" read -r dir input lock <<<"$entry"
    echo "== conda: $dir/$input -> $dir/$lock"
    (cd "/repo/$dir" && micromamba run -p "$tool" conda-lock lock --micromamba $check \
        -f "$input" -p linux-64 --lockfile "$lock")
done
'

refresh_conda() {
    local check="--check-input-hash"
    ((upgrade)) && check=""
    run_tool --entrypoint /bin/bash \
        -e MAMBA_ROOT_PREFIX=/cache/mamba -e CONDA_PKGS_DIRS=/cache/pkgs \
        "$MICROMAMBA_IMAGE" -c "$CONDA_SCRIPT" bash "$check" "${CONDA_LOCKS[@]}"
    update_manifest conda
}

# --- pip -----------------------------------------------------------------------

refresh_pip() {
    local entry input output upgrade_flag=()
    ((upgrade)) && upgrade_flag=(--upgrade)
    for entry in "${PIP_LOCKS[@]}"; do
        IFS='|' read -r input output <<<"$entry"
        echo "== pip: $input -> $output"
        run_tool -e UV_CACHE_DIR=/cache/uv "$UV_IMAGE" \
            uv pip compile "$input" -o "$output" \
            --generate-hashes \
            --python-version 3.14 \
            --python-platform x86_64-manylinux_2_28 \
            --no-strip-extras \
            --custom-compile-command "scripts/locks/refresh.sh pip" \
            "${upgrade_flag[@]}"
    done
    update_manifest pip
}

# Rewrite the manifest lines of the inputs just refreshed; keep the others as they were,
# so that refreshing one kind never certifies an un-refreshed input of another kind.
update_manifest() {
    local kind="$1" entry dir input lock path
    local -A hashes=()
    local -a refreshed=()
    if [[ "$kind" == conda ]]; then
        for entry in "${CONDA_LOCKS[@]}"; do
            IFS='|' read -r dir input lock <<<"$entry"
            refreshed+=("$dir/$input")
        done
    else
        for entry in "${PIP_LOCKS[@]}"; do refreshed+=("${entry%%|*}"); done
    fi
    if [[ -f "$INPUTS_MANIFEST" ]]; then
        while read -r hash path; do
            if [[ -n "${path:-}" ]]; then hashes["$path"]="$hash"; fi
        done <"$INPUTS_MANIFEST"
    fi
    for path in "${refreshed[@]}"; do
        hashes["$path"]="$(sha256sum "$path" | cut -d' ' -f1)"
    done
    {
        while IFS= read -r path; do
            if [[ -n "${hashes[$path]:-}" ]]; then printf '%s  %s\n' "${hashes[$path]}" "$path"; fi
        done < <(lock_inputs | LC_ALL=C sort)
    } >"$INPUTS_MANIFEST.tmp"
    mv "$INPUTS_MANIFEST.tmp" "$INPUTS_MANIFEST"
}

# --- actions -------------------------------------------------------------------

declare -A action_sha=()
resolve_action() {
    # resolve_action <owner/repo> <tag>: sets RESOLVED to the tag's commit SHA (peeled for
    # annotated tags); returns 1 when the tag does not exist.
    local key="$1@$2" out sha
    if [[ -z "${action_sha[$key]:-}" ]]; then
        # -w /cache: outside the checkout, whose .git may point outside the container.
        out="$(run_tool -w /cache "$GIT_IMAGE" ls-remote "https://github.com/$1" \
            "refs/tags/$2" "refs/tags/$2^{}")" || return 1
        sha="$(awk '$2 ~ /\^\{\}$/ {p=$1} $2 !~ /\^\{\}$/ {t=$1} END {print (p != "" ? p : t)}' <<<"$out")"
        [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || return 1
        action_sha[$key]="$sha"
    fi
    RESOLVED="${action_sha[$key]}"
}

refresh_actions() {
    local file line tmp prefix path ref tag repo
    while IFS= read -r file; do
        tmp="$(mktemp)"
        while IFS= read -r line || [[ -n "$line" ]]; do
            if [[ "$line" =~ ^([[:space:]-]*uses:[[:space:]]*)([^[:space:]@#]+)@([^[:space:]#]+)([[:space:]]*#[[:space:]]*([^[:space:]]+))? ]] &&
                [[ "${BASH_REMATCH[2]}" != ./* && "${BASH_REMATCH[2]}" != docker://* ]]; then
                prefix="${BASH_REMATCH[1]}"
                path="${BASH_REMATCH[2]}"
                ref="${BASH_REMATCH[3]}"
                tag="${BASH_REMATCH[5]:-}"
                [[ "$ref" =~ ^[0-9a-f]{40}$ ]] || tag="$ref"
                repo="$(cut -d/ -f1-2 <<<"$path")"
                if [[ -z "$tag" ]]; then
                    echo "$file: $path@$ref has no '# <tag>' comment; cannot refresh" >&2
                    errors=$((errors + 1))
                elif resolve_action "$repo" "$tag"; then
                    line="${prefix}${path}@${RESOLVED} # ${tag}"
                else
                    echo "$file: cannot resolve tag $tag of $repo" >&2
                    errors=$((errors + 1))
                fi
            fi
            printf '%s\n' "$line"
        done <"$file" >"$tmp"
        if cmp -s "$tmp" "$file"; then rm -f "$tmp"; else
            cat "$tmp" >"$file" && rm -f "$tmp"
            echo "== actions: updated $file"
        fi
    done < <(git ls-files '.github/workflows/*.yml' '.github/workflows/*.yaml' '.github/actions/**/action.yml')
}

# --- images --------------------------------------------------------------------

refresh_images() {
    local file ref name_tag tag old new content
    local -A digest=()
    while IFS= read -r file; do
        while IFS= read -r ref; do
            name_tag="${ref%@*}"
            old="${ref##*@}"
            tag="${name_tag##*:}"
            if [[ "$name_tag" != *:* || "$tag" == */* ]]; then
                echo "$file: $ref names no tag; cannot refresh (write it as name:tag@sha256:...)" >&2
                errors=$((errors + 1))
                continue
            fi
            if [[ -z "${digest[$name_tag]:-}" ]]; then
                new="$(run_tool "$CRANE_IMAGE" digest "$name_tag")" || new=""
                if [[ ! "$new" =~ ^sha256:[0-9a-f]{64}$ ]]; then
                    echo "$file: cannot resolve $name_tag" >&2
                    errors=$((errors + 1))
                    continue
                fi
                digest[$name_tag]="$new"
            fi
            new="${digest[$name_tag]}"
            if [[ "$new" != "$old" ]]; then
                content="$(cat "$file"; printf x)"
                content="${content%x}"
                printf '%s' "${content//"$name_tag@$old"/"$name_tag@$new"}" >"$file"
                echo "== images: $file: $name_tag ${old:7:12} -> ${new:7:12}"
            fi
        done < <(grep -oE '[A-Za-z0-9][A-Za-z0-9._/:-]*@sha256:[0-9a-f]{64}' "$file" | sort -u)
    done < <(git ls-files | grep -E '(^|/)Dockerfile(\.[^/]+)?$|^scripts/(locks|sbom)/[^/]+\.sh$|^\.github/workflows/[^/]+\.ya?ml$' || true)
}

case "$target" in
    conda) refresh_conda ;;
    pip) refresh_pip ;;
    actions) refresh_actions ;;
    images) refresh_images ;;
    all)
        refresh_images
        refresh_actions
        refresh_conda
        refresh_pip
        ;;
esac

if ((errors > 0)); then
    echo "refresh: $errors error(s)" >&2
    exit 1
fi
echo "refresh: done ($target)"
git status --short
