# Container definitions

Every image is built with the repository root as the build context by `build.sh`, which
tags `<name>:<docker/VERSION>` and nothing else:

```
docker/build.sh            # workflow image   eemt:<VERSION>       (docker/workflow/Dockerfile)
docker/build.sh web        # web interface    eemt-web:<VERSION>   (docker/web-interface/Dockerfile)
docker/build.sh docs       # docs server      eemt-docs:<VERSION>  (docker/docs/Dockerfile)
```

Every `FROM` and `COPY --from` image is pinned by digest and every Python package comes from
a hashed lock file; `scripts/locks/check.sh` enforces the digests.

| Path | Contents |
|---|---|
| `workflow/` | Workflow image `eemt:<version>`: GRASS GIS 8.5, GDAL 3.12, CCTools 7.17, Python 3.14 (all from a hash-locked conda-forge environment) and the `sol/` and `eemt/` pipelines. See [`workflow/README.md`](workflow/README.md). |
| `web-interface/` | Web interface image `eemt-web:<version>`: Python 3.14 slim, the FastAPI app from `web-interface/` (packages from the hashed `web-interface/requirements.txt`), the Docker CLI binary from `docker:29.9.3-cli`, non-root user `eemt`. No cron: scheduled cleanup runs outside the container (compose `eemt-cleanup`, a Kubernetes CronJob or the host). |
| `docs/` | Documentation server image `eemt-docs:<version>`: Zensical from the hashed `requirements-docs.txt`; `zensical.toml` and `docs/` are mounted read-only at `/site-src`. |
| `VERSION` | The version of all three images |
| `build.sh` | Builds one image (`workflow`, `web` or `docs`) |
| `kubernetes/` | Helm chart and manifests (no image definitions) |

## Lock files

Every dependency set is resolved once into a lock file with a hash per package, and images
and CI install only from the locks:

| Input (edit this) | Lock (generated) | Resolver |
|---|---|---|
| `docker/workflow/environment.yml` | `docker/workflow/conda-lock.yml` | conda-lock 4.1.0, micromamba solver, linux-64 |
| `notebooks/environment.yml` | `notebooks/conda-lock.yml` | same |
| `notebooks/environment-minimal.yml` | `notebooks/minimal-conda-lock.yml` | same |
| `web-interface/requirements.in` | `web-interface/requirements.txt` | uv 0.13.8 `pip compile --generate-hashes`, CPython 3.14 manylinux |
| `requirements-docs.in` | `requirements-docs.txt` | same |
| `requirements-dev.in` | `requirements-dev.txt` | same (constrained by the web lock) |
| `uses:` tags in `.github/workflows/` | the commit SHA in the same line (`@<sha> # <tag>`) | `git ls-remote` |
| image tags in Dockerfiles and `scripts/locks/` | the digest in the same reference (`name:tag@sha256:...`) | `crane digest` |
| `rsun/Cargo.toml` files, `rsun/rust-toolchain.toml` | `rsun/Cargo.lock` | cargo (Area C's workspace; build with `--locked`) |

conda-lock runs from its own hash-locked environment (`scripts/locks/tools/locktool.yml` ->
`locktool-lock.yml`). Every resolver runs in a container pulled by digest
(`scripts/locks/lib.sh`); nothing is installed on the host. Lock file names end in
`-lock.yml`, because micromamba reads any other name as a plain environment file.

Refresh after editing an input (network access needed; set `EEMT_LOCK_CACHE=<dir>` to keep
the tool environment and download caches between runs):

```
scripts/locks/refresh.sh              # all: images, actions, conda, pip
scripts/locks/refresh.sh conda        # or pip, actions, images
scripts/locks/refresh.sh --upgrade    # conda: re-solve everything; pip: newest allowed releases
scripts/locks/check.sh                # offline gate: pins, digests, hashes, inputs refreshed
```

Without `--upgrade` a refresh changes only what its input requires: a conda lock whose
recorded input hash still matches is left alone, and uv keeps existing pins. Running it twice
with no upstream change leaves `git status` clean. Actions and images always follow the tag
written next to the pin; to move to a new version, edit that tag and refresh.

Rules:

- Never edit a lock by hand. Commit the input, its lock and `scripts/locks/inputs.sha256`
  (the input hashes `check.sh locks` compares) together.
- A change to any lock also means rebuilding the affected images and regenerating the
  committed SBOMs (`sbom/`) in the same branch, so that the SBOMs never describe an older
  lock. For that reason the repository has no Dependabot configuration: an automated bump
  would change a lock without its SBOM. Run `scripts/locks/refresh.sh --upgrade` on a
  schedule instead and review the diff.
- Security floors (Dependabot alerts) are written as lower bounds in the `.in` files, so a
  refresh can never go below them.
- Installing a notebook environment: `micromamba create -n eemt-gis -f
  notebooks/conda-lock.yml`. micromamba writes a temporary pip requirements file next to the
  lock, so the lock must be in a writable directory.
- Image builds call `scripts/locks/assert-conda-env.sh <lock> <prefix>` after installing a
  conda lock; it fails unless exactly the locked packages are installed.

Not covered yet: `lean4-verification/lake-manifest.json` is not committed
(`validation/findings.toml` F-A-031), and the build requirements in
`rsun/rsun-py/pyproject.toml` are unpinned ranges (F-A-032).

## SBOMs

The committed SBOMs in `sbom/` (CycloneDX 1.7 and SPDX 2.3 JSON, one pair per image, lock
file and the rsun workspace, plus an aggregate) are generated from the images and locks
above; [`sbom/README.md`](../sbom/README.md) describes every file. After any change to a
lock, an image input or `scripts/sbom/{normalize,lock2cdx,cdx2spdx}.py`:

```
docker/build.sh && docker/build.sh web     # eemt:<VERSION>, eemt-web:<VERSION>
scripts/sbom/generate.sh all --no-build    # rescan the images, regenerate sbom/
scripts/sbom/validate.sh                   # schema, SPDX, NTIA, cross-checks, secret scan
git status -- sbom/                        # commit what changed, in the same branch
scripts/sbom/vuln-report.sh                # grype report in build/sbom-vuln/ (not a gate)
```

`EEMT_SBOM_IMAGE_PREFIX=eemt-work/` makes the SBOM scripts use `eemt-work/eemt:<VERSION>`
instead; the documents do not depend on the prefix. `generate.sh` without `--no-build` runs
the two builds itself.

## Continuous integration

Nothing is pushed to a registry. Both workflows run on every pull request, on pushes to
`master` and by hand, with read-only repository permissions:

| Workflow | Job | What it does |
|---|---|---|
| `.github/workflows/containers.yml` | `locks` | `scripts/locks/check.sh all`: actions pinned by commit SHA, images by `name:tag@sha256`, downloads verified, lock files current and hashed |
| | `images-sbom` | Builds the workflow and web images (BuildKit, GitHub Actions cache written only from `master`, no attestations), runs `eemt-selftest` as the image user and as UID 12345, runs `pytest -m grass` inside the workflow image, then `generate.sh all --no-build`, `validate.sh` and the drift gate: the job fails if `sbom/` differs from what the scripts produce, prints the changed files and the commands above, and attaches the regenerated tree. The grype report and syft's SPDX 3.0 JSON are attached as artifacts (not gates, not committed). |
| `.github/workflows/reuse.yml` | `reuse` | `reuse lint` (fsfe/reuse 6.3.0 by digest); the `reuse spdx` report is attached as an artifact, never committed |

On a pull request that changes only paths that cannot reach an image (documentation, Lean,
equations, PDFs, notebooks, rsun, validation, top-level Markdown and a few configuration
files; the list is in the workflow), `images-sbom` skips the image build and still
regenerates, validates and drift-checks every SBOM made from a lock file. Pushes to `master`
always build. To run the same steps locally, use the commands in the comment at the top of
`containers.yml`.

## Compose

`docker-compose.yml` runs the web container as `${EEMT_UID:-1000}:${EEMT_GID:-1000}`, adds
the group of the Docker socket (`group_add: ${EEMT_DOCKER_GID}`) and passes the workflow image
to it as `EEMT_WORKFLOW_IMAGE=eemt:${EEMT_VERSION:-3.0.0}`:

```
EEMT_UID=$(id -u) EEMT_GID=$(id -g) EEMT_DOCKER_GID=$(stat -c %g /var/run/docker.sock) docker-compose up
```

`EEMT_DOCKER_GID` is also a build argument: the image's own user `eemt` is put in a `docker`
group with that GID, so `docker run` without `--user` reaches the socket too. The image's
`USER` is the numeric UID only; `UID:GID` would drop the supplementary group.

## Removed definitions

- `minimal/` (image `eemt-test:minimal`) was removed. It was an unpinned Python 3.12 image
  with the web app and a generated mock `run-solar-workflow.py`, and it was the base of the
  old web image. Nothing builds on it any more; a fake workflow image for web tests belongs
  to the test suite. The last commit that contains it is `a0a09d2`.
- `ubuntu/24.04/` (image `eemt:ubuntu24.04`) was replaced by `workflow/`. It installed
  Miniforge from a moving `releases/latest` URL and built CCTools from an unverified tarball,
  mixed apt GRASS 8.3.2 and GDAL 3.8 with a conda GDAL 3.11 and Python 3.11, set `GISBASE` to
  a directory that did not exist, ignored failed `g.extension` installs, installed QGIS
  (unused) and came to 6.9 GB (`validation/findings.toml` F-A-013 to F-A-018). The last
  commit that contains it is `bd19386` (`git show bd19386:docker/ubuntu/24.04/Dockerfile`).
- The GRASS GIS 7.2.2, 7.4.4 and 7.8.3 and CCTools Dockerfiles for CentOS 7, Ubuntu 18.04 and
  Ubuntu 20.04 (`centos/`, `grass72/`, `grass74/`, `ubuntu/cctools/`, `ubuntu/grass72/`,
  `ubuntu/grass74/`, `ubuntu/grass78/`) have been deleted. Those base images are past the end
  of their standard support, and no script, compose file or workflow in the repository built
  or referenced them. They remain in the git history; commit `40d8389` is the last one that
  contains them (`git show 40d8389:docker/ubuntu/grass78/Dockerfile`).
