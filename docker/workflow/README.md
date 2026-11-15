# EEMT workflow image

`eemt:<version>` (version in [`docker/VERSION`](../VERSION)) runs the solar (`sol/`) and full
EEMT (`eemt/`) pipelines: GRASS GIS, GDAL, CCTools (Makeflow, Work Queue) and Python, all from
conda-forge, installed from a lock file in which every package is pinned by URL and sha256.
This directory replaces `docker/ubuntu/24.04/` (image `eemt:ubuntu24.04`).

| Component | Version |
|---|---|
| Python | 3.14.10 |
| GRASS GIS | 8.5.0 (`GISBASE=/opt/eemt/env/lib/grass85`) |
| GDAL / PROJ / GEOS | 3.12.3 / 9.7.1 / 3.14.1 |
| CCTools (`ndcctools`) | 7.17.6 |
| numpy / netCDF4 | 2.5.4 / 1.7.4 |
| Runtime base | `ubuntu:26.04`, pinned by digest, no apt packages added |

GDAL is held at 3.12.3 and PROJ at 9.7.1 by the conda-forge `grass 8.5.0` build, which
requires `libgdal-core >=3.12.3,<3.13` and `proj >=9.7.1,<9.8`.

## Build

From the repository root:

```bash
docker/build.sh                     # -> eemt:<docker/VERSION>, no other tag
docker/build.sh --no-cache --pull   # clean rebuild
```

`docker/build.sh` only builds this image and only applies the `eemt:<version>` tag (set
`EEMT_IMAGE_NAME` to change the repository part). The build fails if:

- the number of installed packages differs from the number in `conda-lock.yml`, or a package
  hash does not match (micromamba checks every sha256);
- the static `ENV` in the Dockerfile disagrees with `grass --config path`,
  `grass --config python_path` or the conda activation scripts (`GDAL_DATA`,
  `GDAL_DRIVER_PATH`, `PROJ_DATA`);
- a password file ends up in the image.

## Run

```bash
# Check the image (offline, any UID; uses a synthetic DEM unless --dem is given)
docker run --rm --network none --user "$(id -u):$(id -g)" \
  -v "$PWD/sol/examples:/in:ro" eemt:3.0.0 eemt-selftest --dem /in/mcn_10m.tif

# Solar workflow through the container wrapper
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD/sol/examples:/data/input:ro" -v "$PWD/test-output:/data/output" \
  eemt:3.0.0 python /opt/eemt/bin/run-solar-workflow.py \
  --dem /data/input/mcn_10m.tif --output /data/output --num-threads 2 --job-id test-001
```

The web interface picks the image from `EEMT_WORKFLOW_IMAGE` (set in `docker-compose.yml`).

### Any user ID

The image runs under any `--user UID:GID`, with or without a passwd entry:

- `HOME` (`/home/eemt`) and the working directory `/data` are mode 1777.
- The entrypoint sets `USER`/`LOGNAME` from the UID (Python's `getpass.getuser()` fails for
  an unknown UID otherwise) and, when `HOME` is not writable (read-only root filesystem),
  moves it to a new directory under `$TMPDIR` (default `/tmp`).
- The whole environment (PATH, `GISBASE`, GRASS Python path, `GDAL_DATA`, `PROJ_DATA`,
  `GDAL_DRIVER_PATH`, `PROJ_NETWORK=OFF`) is static `ENV`, so `docker exec`,
  `--entrypoint` and Kubernetes `command:` see the same environment as the entrypoint.

With `--read-only`, mount a writable `/tmp` (for example `--tmpfs /tmp`) and run work in a
writable directory: `makeflow` aborts, even for `--version`, when its working directory is
not writable.

### Layout

| Path | Contents |
|---|---|
| `/opt/eemt/env` | the conda environment (`conda-meta/` lists every package) |
| `/opt/eemt/bin` | `entrypoint.sh`, `eemt-selftest`, `run-solar-workflow.py`, `run-eemt-workflow.py` |
| `/opt/eemt/sol`, `/opt/eemt/eemt` | the two pipelines, as in the repository |
| `/opt/eemt/share/eemt` | `conda-lock.yml`, `environment.yml`, `VERSION` used for the build |
| `/opt/eemt/share/licenses` | licence files of every conda package that ships them (GRASS and CCTools `COPYING` included) |
| `/opt/eemt/share/doc/eemt` | the repository's `LICENSE` and `LICENSES/` |

No sample data is copied in: mount a DEM at run time. No password file is baked in: for Work
Queue, generate one per run (`--password FILE` on both `makeflow` and `work_queue_worker`).

## What is not in the image

| Missing | Consequence |
|---|---|
| SAGA GIS | Not packaged on conda-forge. `eemt/eemt/twi.sh` calls `saga_cmd` and cannot run; TWI needs a GRASS replacement (`r.watershed`/`r.topidx`), a science decision (SDR-016). |
| `r.sun.mp` and other GRASS add-ons | Not installed. `eemt/eemt/rsun.sh` still calls `r.sun.mp`; core `r.sun` has `nprocs=` (OpenMP, `libgomp` linked). |
| QGIS, PDAL tools, Jupyter, pandas, xarray, rasterio | No pipeline code uses them. `libpdal-core` is present only as a GRASS dependency. |
| opencv, ffmpeg, matplotlib | Needed only by the report script `scripts/eemt_zscore_animation.py`, which therefore does not run in this image. Adding them solves on Python 3.14 but adds 77 packages (Qt-free "headless" opencv, openvino, a GPL ffmpeg build) and changes the build of `snappy` and `blosc` under GDAL/netCDF; matplotlib with pillow alone adds 13 packages and changes nothing else. |
| `docker` and `psutil` Python packages | `scripts/start-worker.py` imports the web interface's workflow manager, which needs both, so the compose `distributed` worker cannot start in this image. |

## Updating the lock

Edit `environment.yml`, then re-solve in the digest-pinned micromamba image (conda-lock
4.1.0, linux-64) and rebuild:

```bash
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp/h -v "$PWD/docker/workflow:/w" -w /w \
  --entrypoint /bin/bash \
  mambaorg/micromamba@sha256:c3897a06f87c1c13f038188b3665a8c4162da62eac5109ae6a939d1e290dcefa -c \
  'export MAMBA_ROOT_PREFIX=/tmp/m; micromamba create -y -q -n lock -c conda-forge conda-lock=4.1.0 python=3.13 &&
   micromamba run -n lock conda-lock lock --micromamba -f environment.yml -p linux-64 --lockfile conda-lock.yml'
docker/build.sh
```

The lock file name must end in `-lock.yml`: micromamba treats any other name as a plain
environment file and creates an empty environment.

## Makeflow notes (CCTools 7.17.6)

- `--wq-keepalive-interval` no longer exists; use `--keepalive-interval`.
- A loopback Work Queue run works with `makeflow --batch-type=wq --port=0 --port-file=FILE
  --password=PW` and `work_queue_worker --password=PW --workdir=OTHER_DIR --single-shot
  127.0.0.1 <port>`.
- Do not use `makeflow --batch-type=dryrun`: it writes each command to `<file>.sh`, then
  treats the rule as failed because its outputs do not exist and spins at full CPU, never
  exiting, until it is killed (also with `--retry-count=0`). To check a workflow file
  without running it, use `makeflow_analyze -k <file>` (exit status 0 = syntax OK).
