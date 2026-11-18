# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
#
# SPDX-License-Identifier: BSD-3-Clause

# EEMT documentation server image: eemt-docs:<docker/VERSION>.
#
# Zensical (requirements-docs.txt, hashed) on a digest-pinned Python slim image. The
# image holds no documentation: zensical.toml and docs/ are bind-mounted read-only at
# /site-src (docker-compose.yml service eemt-docs), and the site is built into the
# writable /site-src/site and /site-src/.cache inside the container.
#
#   docker/build.sh docs                         # -> eemt-docs:<docker/VERSION>
#   docker-compose --profile docs up eemt-docs   # http://127.0.0.1:8000
#   docker run --rm -v "$PWD/zensical.toml:/site-src/zensical.toml:ro" \
#       -v "$PWD/docs:/site-src/docs:ro" eemt-docs:<VERSION> zensical build

FROM python:3.14.10-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

ARG EEMT_UID=1000
ARG EEMT_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements-docs.txt /tmp/requirements-docs.txt
RUN pip install --require-hashes --root-user-action=ignore -r /tmp/requirements-docs.txt \
    && rm /tmp/requirements-docs.txt \
    && pip check \
    && zensical --version

# /site-src is writable for any UID: the build output (site/) and cache (.cache/) are
# created next to the mounted, read-only zensical.toml and docs/.
RUN groupadd --gid "${EEMT_GID}" eemt \
    && useradd --uid "${EEMT_UID}" --gid eemt --home-dir /home/eemt --create-home --shell /bin/bash eemt \
    && mkdir -p /site-src \
    && chmod 1777 /site-src

WORKDIR /site-src
ENV HOME=/home/eemt

ARG EEMT_VERSION=unset
ARG EEMT_REVISION=unknown
# Commit date of EEMT_REVISION (docker/build.sh), not the build time.
ARG EEMT_CREATED=unknown
LABEL org.opencontainers.image.title="eemt-docs" \
      org.opencontainers.image.description="EEMT documentation server: Zensical; mount zensical.toml and docs/ read-only at /site-src" \
      org.opencontainers.image.version="${EEMT_VERSION}" \
      org.opencontainers.image.revision="${EEMT_REVISION}" \
      org.opencontainers.image.created="${EEMT_CREATED}" \
      org.opencontainers.image.source="https://github.com/tyson-swetnam/eemt" \
      org.opencontainers.image.url="https://tyson-swetnam.github.io/eemt/" \
      org.opencontainers.image.licenses="BSD-3-Clause" \
      org.opencontainers.image.base.name="docker.io/library/python:3.14.10-slim-trixie"

USER ${EEMT_UID}

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=5).status == 200 else 1)"]

# Bound to all interfaces inside the container; publish it on the host as
# 127.0.0.1:8000 (docker-compose.yml) unless the docs should be reachable from elsewhere.
CMD ["zensical", "serve", "--dev-addr", "0.0.0.0:8000"]
