#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
#
# Regenerate the committed SBOMs in sbom/ (CycloneDX 1.7 JSON and SPDX 2.3 JSON).
#
#   scripts/sbom/generate.sh [all|images|static|aggregate] [--no-build]
#
#   images     build eemt and eemt-web (docker/build.sh) unless --no-build, `docker
#              save` them and scan the tarballs with syft -> sbom/image-*.json
#   static     documents made from lock files: conda (lock2cdx.py), pip (cyclonedx-py),
#              cargo (Cargo.lock + `cargo metadata`), lean (only if
#              lean4-verification/lake-manifest.json is tracked) -> sbom/env-*.json,
#              sbom/cargo-rsun.*.json, sbom/lean-eemtverify.*.json
#   aggregate  sbom/eemt.{cdx,spdx}.json and sbom/manifest.json from the documents above
#   all        images, static, aggregate (default)
#
# Every document is passed through scripts/sbom/normalize.py, so running this twice
# leaves `git diff -- sbom/` empty. Then run scripts/sbom/validate.sh.
#
# Environment:
#   EEMT_SBOM_IMAGE_PREFIX  repository prefix of the images to build/scan, e.g.
#                           "eemt-work/" -> eemt-work/eemt:<VERSION> (default: none)
#   EEMT_SBOM_CACHE         keep the Python tool venv, cargo registry and scratch files
#                           here (default: a temporary directory, removed on exit)
#
# Network: the first run installs the Python tools (requirements-dev.txt, hashed) and
# `cargo metadata` downloads the crates of rsun/Cargo.lock; nothing else needs it.
set -euo pipefail

# shellcheck source=scripts/sbom/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$SBOM_ROOT"

target=all
build=1
for arg in "$@"; do
    case "$arg" in
        all | images | static | aggregate) target="$arg" ;;
        --no-build) build=0 ;;
        *)
            echo "usage: $0 [all|images|static|aggregate] [--no-build]" >&2
            exit 2
            ;;
    esac
done

CONFIG=scripts/sbom/targets.toml
EPOCH_FILE=sbom/SOURCE_DATE_EPOCH
VERSION="$(tr -d '[:space:]' <docker/VERSION)"
PREFIX="${EEMT_SBOM_IMAGE_PREFIX:-}"
[[ -f "$EPOCH_FILE" ]] || { echo "missing $EPOCH_FILE" >&2; exit 1; }

sbom_cache_init
WORK="$SBOM_CACHE/work"
rm -rf "$WORK"
mkdir -p "$WORK" sbom
sbom_py_env

# targets KIND -> lines "name|kind|image|build|lock"
targets() { sbom_py -- scripts/sbom/sbomlib.py list "$CONFIG" "$1"; }

normalize() {
    # normalize <target> <in> <out> [extra args]
    local name="$1" in="$2" out="$3"
    shift 3
    sbom_py -- scripts/sbom/normalize.py "$in" "$out" --config "$CONFIG" --target "$name" \
        --epoch-file "$EPOCH_FILE" --version "$VERSION" "$@"
}

# CycloneDX (raw, in $WORK) -> normalised CycloneDX and SPDX twin in sbom/.
finish_from_cdx() {
    local name="$1"
    normalize "$name" "/cache/work/$name.cdx.raw.json" "sbom/$name.cdx.json"
    sbom_py -- scripts/sbom/cdx2spdx.py "sbom/$name.cdx.json" "/cache/work/$name.spdx.raw.json"
    normalize "$name" "/cache/work/$name.spdx.raw.json" "sbom/$name.spdx.json"
    echo "   sbom/$name.cdx.json sbom/$name.spdx.json"
}

do_images() {
    local name kind image buildtarget lock ref
    while IFS='|' read -r name kind image buildtarget lock; do
        ref="${PREFIX}${image}:${VERSION}"
        echo "== image: $name ($ref)"
        if ((build)); then
            EEMT_IMAGE_NAME="${PREFIX}${image}" docker/build.sh "$buildtarget" --label eemt-work=1 >&2
        fi
        docker image inspect "$ref" >/dev/null
        docker save "$ref" -o "$WORK/$name.tar"
        sbom_run -e SYFT_CHECK_FOR_APP_UPDATE=false -e SYFT_FILE_METADATA_SELECTION=none \
            "$SYFT_IMAGE" scan "docker-archive:/cache/work/$name.tar" -q \
            --select-catalogers +conda-meta-cataloger \
            --source-name "$image" --source-version "$VERSION" \
            -o "syft-json=/cache/work/$name.syft.json" \
            -o "cyclonedx-json@1.7=/cache/work/$name.cdx.raw.json" \
            -o "spdx-json@2.3=/cache/work/$name.spdx.raw.json"
        rm -f "$WORK/$name.tar"
        normalize "$name" "/cache/work/$name.cdx.raw.json" "sbom/$name.cdx.json" --syft-json "/cache/work/$name.syft.json"
        normalize "$name" "/cache/work/$name.spdx.raw.json" "sbom/$name.spdx.json" --syft-json "/cache/work/$name.syft.json"
        echo "   sbom/$name.cdx.json sbom/$name.spdx.json"
    done < <(targets image)
}

do_static() {
    local name kind image buildtarget lock lic=()
    # Conda locks carry no licences; take them from the image SBOM's conda-meta records
    # for packages with the identical purl (name, version, build).
    for f in sbom/image-*.cdx.json; do
        [[ -f "$f" ]] && lic+=(--licenses-from "$f")
    done
    [[ ${#lic[@]} -gt 0 ]] || echo "warning: no image SBOM yet; conda documents get no licences" >&2

    while IFS='|' read -r name kind image buildtarget lock; do
        echo "== $kind: $name ($lock)"
        case "$kind" in
            conda)
                sbom_py -- scripts/sbom/lock2cdx.py conda "$lock" "/cache/work/$name.cdx.raw.json" "${lic[@]}"
                ;;
            pip)
                sbom_py -e "SOURCE_DATE_EPOCH=$(cat "$EPOCH_FILE")" -- -m cyclonedx_py requirements \
                    --sv 1.7 --output-reproducible -o "/cache/work/$name.cdx.raw.json" "$lock"
                ;;
            cargo)
                # Crate licences come from the crates' own manifests, which `cargo
                # metadata` reads after downloading them (network).
                sbom_run -e CARGO_HOME=/cache/cargo -e "RUSTUP_TOOLCHAIN=$SBOM_RUST_TOOLCHAIN" \
                    -w "/repo/$(dirname "$lock")" "$RUST_IMAGE" \
                    sh -c 'cargo --version > /cache/work/cargo.version &&
                           cargo metadata --format-version 1 --locked --all-features -q' \
                    >"$WORK/$name.metadata.json"
                sbom_py -- scripts/sbom/lock2cdx.py cargo "$lock" "/cache/work/$name.metadata.json" \
                    "/cache/work/$name.cdx.raw.json" --config "$CONFIG"
                ;;
            lean)
                if ! git ls-files --error-unmatch "$lock" >/dev/null 2>&1; then
                    echo "   skipped: $lock is not tracked"
                    rm -f "sbom/$name.cdx.json" "sbom/$name.spdx.json"
                    continue
                fi
                sbom_py -- scripts/sbom/lock2cdx.py lean "$lock" "$(dirname "$lock")/lean-toolchain" \
                    "/cache/work/$name.cdx.raw.json"
                ;;
            *) continue ;;
        esac
        finish_from_cdx "$name"
    done < <(targets "" | grep -E '^[^|]+\|(conda|pip|cargo|lean)\|')
}

do_aggregate() {
    echo "== aggregate: sbom/eemt.{cdx,spdx}.json, sbom/manifest.json"
    local cargo_version=""
    [[ -f "$WORK/cargo.version" ]] && cargo_version="$(cat "$WORK/cargo.version")"
    if [[ -z "$cargo_version" ]]; then
        cargo_version="$(sbom_run --network none -e "RUSTUP_TOOLCHAIN=$SBOM_RUST_TOOLCHAIN" "$RUST_IMAGE" cargo --version)"
    fi
    local py_sha
    py_sha="$(sha256sum "$SBOM_PY_LOCK" | cut -d' ' -f1)"
    cat >"$WORK/tools.json" <<EOF
{
  "syft": {"image": "$SYFT_IMAGE"},
  "cyclonedx-py": {"image": "$PYTHON_IMAGE", "lock": "$SBOM_PY_LOCK", "lock_sha256": "$py_sha"},
  "cargo-metadata": {"image": "$RUST_IMAGE", "version": "$cargo_version"},
  "lock2cdx": {"path": "scripts/sbom/lock2cdx.py", "image": "$PYTHON_IMAGE", "sha256": "$(sha256sum scripts/sbom/lock2cdx.py | cut -d' ' -f1)"},
  "cdx2spdx": {"path": "scripts/sbom/cdx2spdx.py", "image": "$PYTHON_IMAGE", "sha256": "$(sha256sum scripts/sbom/cdx2spdx.py | cut -d' ' -f1)"},
  "normalize": {"path": "scripts/sbom/normalize.py", "image": "$PYTHON_IMAGE", "sha256": "$(sha256sum scripts/sbom/normalize.py | cut -d' ' -f1)"}
}
EOF
    git ls-files | grep -E '(^|/)(requirements[^/]*\.txt|[^/]*-lock\.yml|Cargo\.lock|lake-manifest\.json)$' \
        >"$WORK/locks.txt" || true
    sbom_py -- scripts/sbom/aggregate.py --config "$CONFIG" --sbom-dir sbom --root . \
        --version "$VERSION" --epoch-file "$EPOCH_FILE" \
        --tools /cache/work/tools.json --locks /cache/work/locks.txt
}

case "$target" in
    images) do_images ;;
    static) do_static ;;
    aggregate) do_aggregate ;;
    all)
        do_images
        do_static
        do_aggregate
        ;;
esac
echo "generate: done ($target). Check with scripts/sbom/validate.sh"
