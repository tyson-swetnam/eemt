# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
#
# SPDX-License-Identifier: BSD-3-Clause

# EEMT web interface image: eemt-web:<docker/VERSION>.
#
# FastAPI app (web-interface/) that launches workflow containers (eemt:<VERSION>)
# through the host Docker socket. Build from the repository root:
#
#   docker/build.sh web                  # -> eemt-web:<docker/VERSION>
#
# Every base is pinned by digest (scripts/locks/check.sh images); Python packages come
# from the hashed lock web-interface/requirements.txt (pip --require-hashes).

# Docker CLI (static Go binary, Apache-2.0); only the binary is copied.
FROM docker:29.9.3-cli@sha256:5a7af0dcb0e46c803efe9b45371dd21cb500e5b4453250394ac6f06d49b6e6f1 AS docker-cli

FROM python:3.14.10-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

# Non-root runtime user. EEMT_DOCKER_GID must equal the group of the host's
# /var/run/docker.sock (stat -c %g /var/run/docker.sock) for the image's own user to
# reach the daemon; docker-compose.yml also adds that group at run time (group_add),
# so a container started with another --user works too.
ARG EEMT_UID=1000
ARG EEMT_GID=1000
ARG EEMT_DOCKER_GID=999

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

# Python dependencies first (own layer, changes only with the lock).
COPY web-interface/requirements.txt /tmp/requirements.txt
RUN pip install --require-hashes --root-user-action=ignore -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && pip check

RUN groupadd --gid "${EEMT_GID}" eemt \
    && if getent group "${EEMT_DOCKER_GID}" >/dev/null; then \
           docker_group="$(getent group "${EEMT_DOCKER_GID}" | cut -d: -f1)"; \
       else \
           groupadd --gid "${EEMT_DOCKER_GID}" docker && docker_group=docker; \
       fi \
    && useradd --uid "${EEMT_UID}" --gid eemt --groups "${docker_group}" \
           --home-dir /home/eemt --create-home --shell /bin/bash eemt

# Only what the web app needs: the FastAPI app, its workflow manager, templates and
# static files, the cleanup tool, and the master/worker launchers (on PATH).
WORKDIR /app
COPY web-interface/app.py web-interface/cleanup_jobs.py /app/
COPY web-interface/containers/ /app/containers/
COPY web-interface/templates/ /app/templates/
COPY web-interface/static/ /app/static/
COPY docker/web-interface/startup.sh /app/startup.sh
COPY scripts/start-master.py scripts/start-worker.py /scripts/

# Runtime state directories: writable for any UID (compose may run the container with
# --user UID:GID and usually bind-mounts host directories over them). /app itself is
# sticky and world-writable because cleanup_jobs.py writes cleanup_jobs.log and
# cleanup_summary_*.json next to itself; the root-owned code files stay read-only.
RUN chmod 0755 /app/startup.sh /scripts/start-master.py /scripts/start-worker.py \
    && chmod 1777 /app \
    && mkdir -p /app/uploads /app/results /app/temp /app/cache /app/shared /app/logs \
    && chown "${EEMT_UID}:${EEMT_GID}" /app/uploads /app/results /app/temp /app/cache /app/shared /app/logs \
    && chmod 1777 /app/uploads /app/results /app/temp /app/cache /app/shared /app/logs \
    && python -c "import fastapi, starlette, docker, urllib3, uvicorn, jinja2, multipart, psutil, requests" \
    && docker --version \
    && state="$(find /app /scripts \( -name '*.db' -o -name '*.sqlite' -o -name '*.tif' -o -name '*.tiff' \
           -o -name '*.password' -o -name '.eemt-makeflow-password' -o -name '.env' \
           -o -name 'cleanup_summary_*.json' -o -name '*.log' -o -name '__pycache__' \) -print)" \
    && if [ -n "${state}" ]; then \
           echo "local state or credentials in the image (validation/findings.toml F-A-012):" >&2; \
           echo "${state}" >&2; exit 1; \
       fi

ENV PYTHONPATH=/app \
    PATH="/scripts:${PATH}" \
    HOME=/home/eemt \
    EEMT_SUCCESS_RETENTION_DAYS=7 \
    EEMT_FAILED_RETENTION_HOURS=12 \
    EEMT_CLEANUP_ENABLED=true

ARG EEMT_VERSION=unset
ARG EEMT_REVISION=unknown
# Commit date of EEMT_REVISION (docker/build.sh), not the build time, so labels are
# reproducible.
ARG EEMT_CREATED=unknown
LABEL org.opencontainers.image.title="eemt-web" \
      org.opencontainers.image.description="EEMT web interface: FastAPI job submission and monitoring; launches eemt workflow containers through the Docker socket" \
      org.opencontainers.image.version="${EEMT_VERSION}" \
      org.opencontainers.image.revision="${EEMT_REVISION}" \
      org.opencontainers.image.created="${EEMT_CREATED}" \
      org.opencontainers.image.source="https://github.com/tyson-swetnam/eemt" \
      org.opencontainers.image.url="https://tyson-swetnam.github.io/eemt/" \
      org.opencontainers.image.licenses="BSD-3-Clause" \
      org.opencontainers.image.base.name="docker.io/library/python:3.14.10-slim-trixie"

# Numeric UID only (no ":GID"): Docker then takes the primary and supplementary groups
# from /etc/group, so the user is in the docker group; "UID:GID" would drop it.
USER ${EEMT_UID}

# 5000: web interface; 9123: Work Queue manager (distributed mode).
EXPOSE 5000 9123

# No curl in the image: probe /health with the standard library.
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=5).status == 200 else 1)"]

CMD ["/app/startup.sh"]
