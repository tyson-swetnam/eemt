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

Python lock files are regenerated with `scripts/locks/pip-compile.sh` (uv in a container):
`web-interface/requirements.in` -> `web-interface/requirements.txt`, `requirements-docs.in` ->
`requirements-docs.txt`, `requirements-dev.in` -> `requirements-dev.txt`.

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
