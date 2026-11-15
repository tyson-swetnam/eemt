# SPDX-FileCopyrightText: 2026 Tyson Lee Swetnam
# SPDX-License-Identifier: BSD-3-Clause
#
# EEMT workflow image: GRASS GIS 8.5, GDAL 3.12, CCTools (Makeflow, Work Queue),
# Python 3.14 and the sol/ and eemt/ pipelines.
#
# Build from the repository root (the build context):
#   docker/build.sh                                   -> eemt:<docker/VERSION>
#   docker build -f docker/workflow/Dockerfile .      (same, without the tag)
#
# Stage "env":     micromamba installs docker/workflow/conda-lock.yml (every package
#                  pinned by URL and sha256) into /opt/eemt/env.
# Stage "runtime": plain Ubuntu, no apt. The environment is copied in and described by
#                  static ENV lines, so `docker exec`, `--entrypoint` and Kubernetes
#                  `command:` get the same PATH and GRASS/GDAL/PROJ settings as the
#                  entrypoint. A RUN step fails the build if that ENV disagrees with
#                  what GRASS and the conda activation scripts report.
# Runs under any `--user UID:GID`; see docker/workflow/README.md.

# --- Stage 1: hash-locked conda environment ---------------------------------
# mambaorg/micromamba 2.10.0 (ubuntu24.04 variant). Only the environment it builds is
# copied out; its own OS is never shipped.
FROM mambaorg/micromamba:2.10.0-ubuntu24.04@sha256:c3897a06f87c1c13f038188b3665a8c4162da62eac5109ae6a939d1e290dcefa AS env

USER root
COPY docker/workflow/conda-lock.yml /tmp/eemt/conda-lock.yml
COPY scripts/locks/assert-conda-env.sh /opt/eemt/share/eemt/assert-conda-env.sh

# The lock file name must end in "-lock.yml": micromamba reads any other name as a
# plain environment file and can create an empty environment without failing;
# assert-conda-env.sh fails the build unless exactly the locked name-version-build
# set is installed. Licence texts of every package (GPL components included)
# are copied out of the package cache before it is cleaned, so they ship with the
# binaries in /opt/eemt/share/licenses/<name>-<version>-<build>/.
RUN set -eu; \
    export CONDA_PKGS_DIRS=/tmp/eemt/pkgs; \
    micromamba create -y -p /opt/eemt/env -f /tmp/eemt/conda-lock.yml; \
    mkdir -p /opt/eemt/share/licenses; \
    find /tmp/eemt/pkgs -type d -path '*/info/licenses' > /tmp/eemt/licence-dirs; \
    while read -r d; do \
        dist="$(basename "$(dirname "$(dirname "$d")")")"; \
        cp -a "$d" "/opt/eemt/share/licenses/${dist}"; \
    done < /tmp/eemt/licence-dirs; \
    rm -rf /tmp/eemt/pkgs; \
    bash /opt/eemt/share/eemt/assert-conda-env.sh /tmp/eemt/conda-lock.yml /opt/eemt/env; \
    licensed="$(ls /opt/eemt/share/licenses | wc -l)"; \
    echo "packages with licence files: ${licensed}"; \
    test "${licensed}" -gt 0

# micromamba links packages in parallel and runs the gdk-pixbuf and gtk3 post-link
# scripts before glib-tools and libiconv are in place, so both fail during the
# create (GTK is pulled in by the GRASS GUI and unused here, but the caches should
# not be left half-built). Run them again now and fail if either still reports an error.
RUN set -eu; \
    export PREFIX=/opt/eemt/env; \
    rm -f "${PREFIX}/.messages.txt"; \
    bash "${PREFIX}/bin/.gdk-pixbuf-post-link.sh"; \
    bash "${PREFIX}/bin/.gtk3-post-link.sh"; \
    if grep -q ERROR "${PREFIX}/.messages.txt" 2>/dev/null; then cat "${PREFIX}/.messages.txt"; exit 1; fi; \
    rm -f "${PREFIX}/.messages.txt"

# What the conda activation scripts would export. The runtime stage compares its
# static ENV with this file.
RUN set -eu; \
    micromamba run -p /opt/eemt/env /bin/sh -c \
      'for v in GDAL_DATA GDAL_DRIVER_PATH PROJ_DATA; do printf "%s=%s\n" "$v" "$(printenv "$v")"; done' \
      > /opt/eemt/share/activation.env; \
    cat /opt/eemt/share/activation.env

# --- Stage 2: runtime --------------------------------------------------------
# ubuntu:26.04, pinned by digest.
FROM ubuntu:26.04@sha256:065aa8626841f16057b78b027b464cd59c4fac36c204c72dcbf48d66f5fa8a96 AS runtime

ARG EEMT_VERSION=unset
ARG EEMT_REVISION=unknown
# Commit date of EEMT_REVISION (docker/build.sh), so the label is reproducible and
# does not show the creation date inherited from the base image.
ARG EEMT_CREATED=unknown

LABEL org.opencontainers.image.title="eemt" \
      org.opencontainers.image.description="EEMT workflow runtime: GRASS GIS 8.5, GDAL 3.12, CCTools 7.17, Python 3.14, sol/ and eemt/ pipelines" \
      org.opencontainers.image.version="${EEMT_VERSION}" \
      org.opencontainers.image.revision="${EEMT_REVISION}" \
      org.opencontainers.image.created="${EEMT_CREATED}" \
      org.opencontainers.image.source="https://github.com/tyson-swetnam/eemt" \
      org.opencontainers.image.url="https://tyson-swetnam.github.io/eemt/" \
      org.opencontainers.image.licenses="BSD-3-Clause" \
      org.opencontainers.image.base.name="docker.io/library/ubuntu:26.04"

# Static environment. GISBASE and the GRASS Python path are what
# `grass --config path` and `grass --config python_path` print (checked below).
# The sol/ and eemt/ shell scripts call GRASS modules directly, hence $GISBASE/bin
# and $GISBASE/scripts on PATH. PROJ_NETWORK=OFF: grids come from the image only.
ENV EEMT_PREFIX=/opt/eemt/env \
    GISBASE=/opt/eemt/env/lib/grass85 \
    PATH=/opt/eemt/bin:/opt/eemt/env/bin:/opt/eemt/env/lib/grass85/bin:/opt/eemt/env/lib/grass85/scripts:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONPATH=/opt/eemt/env/lib/grass85/etc/python \
    GDAL_DATA=/opt/eemt/env/share/gdal \
    GDAL_DRIVER_PATH=/opt/eemt/env/lib/gdalplugins \
    PROJ_DATA=/opt/eemt/env/share/proj \
    PROJ_NETWORK=OFF \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/eemt \
    USER=eemt \
    LOGNAME=eemt

COPY --from=env /opt/eemt /opt/eemt

# Pipelines and entry points. Sample data is not copied (.dockerignore excludes
# every *.tif); mount a DEM at run time.
COPY sol/ /opt/eemt/sol/
COPY eemt/ /opt/eemt/eemt/
COPY docker/workflow/container-scripts/ /opt/eemt/bin/
COPY docker/workflow/entrypoint.sh docker/workflow/eemt-selftest /opt/eemt/bin/
COPY docker/workflow/conda-lock.yml docker/workflow/environment.yml docker/VERSION /opt/eemt/share/eemt/
COPY LICENSE /opt/eemt/share/doc/eemt/LICENSE
COPY LICENSES/ /opt/eemt/share/doc/eemt/LICENSES/

# - Replace the base image's "ubuntu" user (UID 1000) with "eemt" (UID/GID 1000).
# - HOME and /data are mode 1777 so that any `--user UID:GID`, with or without a
#   passwd entry, can write there; the entrypoint moves HOME to $TMPDIR when the
#   root filesystem is read-only.
# - Build-time assertions: the static ENV above equals what GRASS and the conda
#   activation scripts report, and the image version file matches the build arg.
RUN set -eu; \
    chmod 0755 /opt/eemt/bin/*; \
    userdel -r ubuntu 2>/dev/null || true; \
    groupadd -g 1000 eemt; \
    useradd -u 1000 -g 1000 -d /home/eemt -M -s /bin/bash eemt; \
    mkdir -p /home/eemt /data; \
    chmod 1777 /home/eemt /data; \
    test "$(grass --config path)" = "${GISBASE}"; \
    test "$(grass --config python_path)" = "${PYTHONPATH}"; \
    grep -qx "GDAL_DATA=${GDAL_DATA}" /opt/eemt/share/activation.env; \
    grep -qx "GDAL_DRIVER_PATH=${GDAL_DRIVER_PATH}" /opt/eemt/share/activation.env; \
    grep -qx "PROJ_DATA=${PROJ_DATA}" /opt/eemt/share/activation.env; \
    test -f "${PROJ_DATA}/proj.db"; \
    test -x "${GISBASE}/bin/r.sun"; \
    bash /opt/eemt/share/eemt/assert-conda-env.sh /opt/eemt/share/eemt/conda-lock.yml /opt/eemt/env; \
    if [ "${EEMT_VERSION}" != unset ]; then \
        test "$(tr -d '[:space:]' < /opt/eemt/share/eemt/VERSION)" = "${EEMT_VERSION}"; \
    fi; \
    if find / -xdev \( -name '.eemt-makeflow-password' -o -name '*.password' \) -print -quit | grep -q .; then \
        echo "password file found in image" >&2; exit 1; \
    fi

USER eemt
WORKDIR /data
ENTRYPOINT ["/opt/eemt/bin/entrypoint.sh"]
CMD ["/bin/bash"]
