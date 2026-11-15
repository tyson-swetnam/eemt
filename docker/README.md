# Container definitions

Every image is built with the repository root as the build context, for example:

```
docker/build.sh                                            # workflow image eemt:<docker/VERSION>
docker build -t eemt-web -f docker/web-interface/Dockerfile .
```

| Path | Contents |
|---|---|
| `workflow/` | Workflow image `eemt:<version>`: GRASS GIS 8.5, GDAL 3.12, CCTools 7.17, Python 3.14 (all from a hash-locked conda-forge environment) and the `sol/` and `eemt/` pipelines. See [`workflow/README.md`](workflow/README.md). |
| `VERSION` | The workflow image version; `build.sh` tags `eemt:<VERSION>` and nothing else |
| `build.sh` | Builds the workflow image |
| `web-interface/` | Web interface image `eemt-web` |
| `minimal/` | Python-only image; `web-interface/Dockerfile` uses it as its base under the tag `eemt-test:minimal` |
| `docs/` | Documentation server image |
| `kubernetes/` | Helm chart and manifests (no image definitions) |

`docker-compose.yml` runs the web container as `${EEMT_UID:-1000}:${EEMT_GID:-1000}` and
passes the workflow image to it as `EEMT_WORKFLOW_IMAGE=eemt:${EEMT_VERSION:-3.0.0}`. To launch
workflow containers the web container needs the group that owns the Docker socket:

```
EEMT_UID=$(id -u) EEMT_GID=$(stat -c %g /var/run/docker.sock) docker-compose up
```

## Removed definitions

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
