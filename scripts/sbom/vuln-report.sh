#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
#
# Vulnerability report for the two image SBOMs (grype). A report, not a gate: the
# result changes whenever the vulnerability database is republished, and conda
# coverage in public databases is incomplete. Output never goes under sbom/.
#
#   scripts/sbom/vuln-report.sh [OUTDIR]        (default: build/sbom-vuln, git-ignored)
#
# Writes OUTDIR/<target>.grype.json and OUTDIR/<target>.grype.txt for image-eemt and
# image-eemt-web, and prints the number of matches per severity. Exit status is 0
# when grype ran, whatever it found; non-zero only if the scan itself failed.
#
# Environment: EEMT_GRYPE_CACHE  grype database directory (default: a temporary
#              directory; the download is several GB, so keep one between runs).
set -euo pipefail

# shellcheck source=scripts/sbom/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$SBOM_ROOT"

OUT="${1:-build/sbom-vuln}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
case "$OUT/" in
    "$SBOM_ROOT/sbom/"*)
        echo "vuln-report: refusing to write under sbom/ ($OUT)" >&2
        exit 2
        ;;
esac

sbom_cache_init
DB="${EEMT_GRYPE_CACHE:-$SBOM_CACHE/grype-db}"
mkdir -p "$DB"
DB="$(cd "$DB" && pwd)"

grype() {
    sbom_container_no=$((sbom_container_no + 1))
    docker run --rm --name "eemt-grype-$$-${sbom_container_no}" --label eemt-work=1 \
        --user "$(id -u):$(id -g)" -e HOME=/tmp -e TMPDIR=/db/tmp \
        -e GRYPE_DB_CACHE_DIR=/db -e GRYPE_CHECK_FOR_APP_UPDATE=false \
        -v "$SBOM_ROOT/sbom:/sbom:ro" -v "$OUT:/out" -v "$DB:/db" \
        "$GRYPE_IMAGE" "$@"
}
mkdir -p "$DB/tmp"

grype db update
grype db status
for name in image-eemt image-eemt-web; do
    grype "sbom:/sbom/$name.cdx.json" -q -o "json=/out/$name.grype.json" -o "table=/out/$name.grype.txt"
done

# Counts per severity (python from the pinned image; the report is plain JSON).
docker run -i --rm --name "eemt-grype-$$-sum" --label eemt-work=1 --network none \
    --user "$(id -u):$(id -g)" -v "$OUT:/out:ro" "$PYTHON_IMAGE" python - <<'EOF'
import collections, json, pathlib
for f in sorted(pathlib.Path("/out").glob("*.grype.json")):
    d = json.loads(f.read_text())
    sev = collections.Counter(m["vulnerability"]["severity"] for m in d["matches"])
    fixable = sum(1 for m in d["matches"] if m["vulnerability"].get("fix", {}).get("state") == "fixed")
    types = collections.Counter(m["artifact"]["type"] for m in d["matches"])
    order = ["Critical", "High", "Medium", "Low", "Negligible", "Unknown"]
    print(f"{f.name.removesuffix('.grype.json')}: {len(d['matches'])} matches;",
          ", ".join(f"{k} {sev.get(k, 0)}" for k in order),
          f"| fix available {fixable} | by package type {dict(sorted(types.items()))}")
EOF
echo "vuln-report: written to $OUT (not gating)"
