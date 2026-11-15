# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Effective Energy and Mass Transfer (EEMT)** - Geospatial modeling toolkit for calculating energy flux in the Critical Zone. Combines topographic solar radiation (GRASS GIS r.sun) with DAYMET climate data to model soil formation, landscape evolution, and energy balance.

**Languages**: Python 3.11 (CCTools compatibility), Bash (GRASS wrappers)

## Architecture

The system has two independent workflow pipelines and a web frontend that orchestrates them via Docker containers:

```
Web Interface (FastAPI :5000)
    └── Docker Container (eemt:<docker/VERSION>, e.g. eemt:3.0.0)
            ├── Solar Workflow (sol/sol/) → Makeflow local batch
            └── EEMT Workflow (eemt/eemt/) → Makeflow + Work Queue
```

### Two-Pipeline Design
- **Solar pipeline** (`sol/sol/run-workflow`): Generates a Makeflow file with 365 daily r.sun tasks + 12 monthly aggregations. Runs in **local batch mode** (`makeflow --batch-type=local`).
- **EEMT pipeline** (`eemt/eemt/run-workflow`): Extends solar with DAYMET climate integration. Runs in **Work Queue mode** (`makeflow --batch-type=wq`) requiring `~/.eemt-makeflow-password`.

Both `run-workflow` scripts are Python-based Makeflow generators (not direct executors). They write `.mf` workflow definition files, then invoke `makeflow` to execute them.

### Web Interface
- `web-interface/app.py` - FastAPI with SQLite job tracking (`/tmp/jobs.db`), async job execution, progress streaming via "PROGRESS:", "STATUS:", "COMPLETED:", "ERROR:" markers
- `web-interface/containers/workflow_manager.py` - Docker orchestration with triple fallback (subprocess CLI → Python SDK → socket → mock mode). Supports LOCAL, MASTER, WORKER node types.
- Two-stage file upload: file uploaded first (UUID-named), then job submitted referencing stored file

### Container Architecture
- Workflow image `eemt:<docker/VERSION>` (`docker/workflow/Dockerfile`): a hash-locked conda-forge environment (`docker/workflow/conda-lock.yml`: Python 3.14, GRASS GIS 8.5.0, GDAL 3.12, CCTools/ndcctools 7.17.6) copied into a digest-pinned Ubuntu 26.04 runtime. Runs as any `--user UID:GID`. `eemt-selftest` checks it.
- Container entry points: `docker/workflow/container-scripts/run-solar-workflow.py` and `run-eemt-workflow.py` (installed in `/opt/eemt/bin`)
- Web image `eemt-web:<docker/VERSION>` (`docker/web-interface/Dockerfile`): pinned `python:3.14.10-slim-trixie` with the hashed `web-interface/requirements.txt` and the Docker CLI binary; it does not extend the workflow image. The web app starts workflow containers from `EEMT_WORKFLOW_IMAGE`.
- Every base image is pinned as `name:tag@sha256`, every package comes from a hashed lock, and `sbom/` holds the generated SBOMs. Details: `docker/README.md`.

### GRASS GIS Integration
Each workflow task creates a temporary GRASS location, runs r.sun or r.series, exports results as GeoTIFF, then cleans up. Shell scripts (`rsun.sh`, `rsum.sh`, `reemt.sh`, `metget.sh`, `twi.sh`) are copied to the output directory for task isolation - this is a Makeflow requirement.

## Build & Run Commands

```bash
# Docker Compose (recommended) - starts web interface on :5000
docker-compose up

# Build the images (tags <name>:<docker/VERSION> only)
docker/build.sh            # workflow image eemt:3.0.0
docker/build.sh web        # web image eemt-web:3.0.0
docker run --rm eemt:3.0.0 eemt-selftest

# Run web interface without Docker Compose
cd web-interface/ && pip install -r requirements.txt && python app.py

# Build and serve docs locally (uses Zensical, not plain mkdocs)
pip install zensical pymdown-extensions && zensical build

# Test solar workflow directly in container
docker run --rm \
  -v $(pwd)/sol/examples:/data/input:ro \
  -v $(pwd)/test-output:/data/output:rw \
  eemt:3.0.0 \
  python /opt/eemt/bin/run-solar-workflow.py \
  --dem /data/input/mcn_10m.tif \
  --output /data/output \
  --step 15 --num-threads 2 --job-id test-001

# Verify outputs: 365 daily files + 12 monthly sums
ls test-output/global/daily/total_sun_day_*.tif | wc -l    # 365
ls test-output/global/monthly/total_sun_*_sum.tif | wc -l  # 12

# Legacy direct execution (requires host GRASS + CCTools)
cd sol/sol/ && python run-workflow --step 15 --num_threads 2 ../examples/mcn_10m.tif

# Distributed execution (Work Queue)
python scripts/start-master.py   # Start master node
python scripts/start-worker.py   # Start worker node (connects to master)

# Linting and testing (dev dependencies: pip install --require-hashes -r requirements-dev.txt)
black --check .
flake8 .
mypy .
pytest
```

## Requirements Files and Locks

Edit the input, never the lock; regenerate with `scripts/locks/refresh.sh` (all tools run in digest-pinned containers) and check with `scripts/locks/check.sh` (offline):

- `web-interface/requirements.in` → `web-interface/requirements.txt` (hashed): FastAPI, uvicorn, docker SDK, psutil for the web app
- `requirements-docs.in` → `requirements-docs.txt` (hashed): Zensical docs toolchain
- `requirements-dev.in` → `requirements-dev.txt` (hashed): tests, lint and SBOM tools
- `docker/workflow/environment.yml` → `docker/workflow/conda-lock.yml`; `notebooks/environment*.yml` → `notebooks/*conda-lock.yml`
- `rsun/Cargo.lock` (cargo), GitHub Actions pinned by commit SHA, images by digest
- There is no root `requirements.txt` any more.

A lock change also means regenerating the SBOMs in the same branch:
`docker/build.sh && docker/build.sh web && scripts/sbom/generate.sh all --no-build && scripts/sbom/validate.sh`, then commit `sbom/` (see `docker/README.md`).

## CI/CD

- `.github/workflows/containers.yml` (pull requests, pushes to `master`): `scripts/locks/check.sh all`; builds the workflow and web images (no registry push), runs `eemt-selftest` and `pytest -m grass` in the workflow image, regenerates and validates the SBOMs and fails if the committed `sbom/` is out of date. The grype report and an SPDX 3.0 SBOM are attached as artifacts.
- `.github/workflows/reuse.yml`: `reuse lint` (every file needs an SPDX header, a `.license` sidecar or a `REUSE.toml` entry).
- `.github/workflows/ghpages.yml`: docs deploy on push to `master` or `2026_update` using `zensical build`.

## Key Parameters

| Parameter | Range | Description |
|-----------|-------|-------------|
| `step` | 3-15 min | Solar calculation time interval (lower = more accurate, slower) |
| `linke_value` | 1.0-8.0 | Atmospheric turbidity |
| `albedo_value` | 0.0-1.0 | Surface reflectance |
| `num_threads` | 1-512 | Parallel Makeflow tasks |
| `start_year/end_year` | 1980-present | DAYMET temporal range (EEMT only) |

## Data Flow

1. User uploads GeoTIFF DEM via web interface or mounts as Docker volume
2. `run-workflow` parses DEM metadata (projection, bounds, resolution) via `Tiff.py`/`parser.py`
3. Generates Makeflow `.mf` file defining task DAG
4. Makeflow executes shell script tasks (each creates temp GRASS location)
5. Solar: 365 daily GeoTIFFs → 12 monthly aggregations
6. EEMT: Downloads DAYMET data via ORNL DAAC API → combines with solar → EEMT products

**Climate data source**: `https://thredds.daac.ornl.gov/thredds/fileServer/ornldaac/1840/`

## Common Gotchas

- **Password file**: EEMT workflow requires `~/.eemt-makeflow-password` for Work Queue mode
- **Solar vs EEMT batch modes**: Solar uses `--batch-type=local`, EEMT uses `--batch-type=wq` - they are not interchangeable
- **Image environment comes from the lock**: GRASS, GDAL, CCTools and Python are conda-forge packages from `docker/workflow/conda-lock.yml`; change `environment.yml`, run `scripts/locks/refresh.sh conda`, rebuild, regenerate `sbom/`
- **Image tags**: only `<name>:<docker/VERSION>`; the old `eemt:ubuntu24.04` tag is gone
- **Duplicate Tiff.py/parser.py**: `sol/sol/` and `eemt/eemt/` each have their own copies - changes must be synchronized
- **Docker socket required**: Web interface needs `/var/run/docker.sock` mounted to manage workflow containers
- **No GPU acceleration**: Referenced in some docs but not implemented

## Docker Compose Profiles

- Default: `eemt-web` only (local execution)
- `distributed`: Adds `eemt-master` + `eemt-worker` services (Work Queue on port 9123)
- `docs`: Adds the Zensical doc server (`eemt-docs` image) on 127.0.0.1:8000
- `cleanup`: Adds the job cleanup service (a loop every `EEMT_CLEANUP_INTERVAL_SECONDS`; no cron in the image)

## Code Style

- **Python**: PEP 8, type hints
- **Bash**: Use `set -e` for error handling
- **Comments**: Explain scientific rationale, not just implementation

## Commit Convention

Use prefix tags: `FIX:`, `FEATURE:`, `REFACTOR:`, `UPDATE:`, `DOCS:`, `CLEANUP:`, `MODERNIZE:`

Commit after each logical change. Include which files changed and why. If workflow scripts or Dockerfiles changed, note whether container rebuild is needed.
