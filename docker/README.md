# Container definitions

Every image is built with the repository root as the build context, for example:

```
docker build -t eemt-web -f docker/web-interface/Dockerfile .
```

| Directory | Contents |
|---|---|
| `ubuntu/24.04/` | Workflow image `eemt:ubuntu24.04` (GRASS GIS, CCTools, the `sol/` and `eemt/` pipelines); built by `ubuntu/24.04/build.sh` |
| `web-interface/` | Web interface image `eemt-web` |
| `minimal/` | Python-only image; `web-interface/Dockerfile` uses it as its base under the tag `eemt-test:minimal` |
| `docs/` | Documentation server image |
| `kubernetes/` | Helm chart and manifests (no image definitions) |

## Removed definitions

The GRASS GIS 7.2.2, 7.4.4 and 7.8.3 and CCTools Dockerfiles for CentOS 7, Ubuntu 18.04 and
Ubuntu 20.04 (`centos/`, `grass72/`, `grass74/`, `ubuntu/cctools/`, `ubuntu/grass72/`,
`ubuntu/grass74/`, `ubuntu/grass78/`) have been deleted. Those base images are past the end of
their standard support, and no script, compose file or workflow in the repository built or
referenced them. They remain in the git history; commit `40d8389` is the last one that contains
them (`git show 40d8389:docker/ubuntu/grass78/Dockerfile`).
