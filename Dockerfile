# Stage 1: Dashboard
#
# A dedicated Node image rather than installing nodejs into the Python stage:
# the toolchain stays out of the build stage that produces the wheel, and the
# Node version is pinned here instead of inheriting whatever the base image's
# package repository happens to ship.
#
# --platform=$BUILDPLATFORM pins this stage to the machine doing the build, so
# a multi-arch build runs npm once, natively, instead of once per target
# architecture under QEMU emulation. The output is static files, which are
# architecture independent.
FROM --platform=$BUILDPLATFORM node:26-alpine AS frontend

# Same directory layout as the repository, so vite's outDir
# (../src/batcontrol/web/static) resolves the same way it does locally.
WORKDIR /build/frontend

# Dependencies first, so a source-only change reuses the npm layer.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# Also runs scripts/check-offline.mjs, which fails the build if the dashboard
# would load a font, stylesheet or script from the network: batcontrol runs in
# homes without internet egress and behind Home Assistant ingress.
RUN npm run build

# Stage 2: Build Stage
FROM python:3.14-alpine AS builder

# Install uv for fast, reliable Python packaging
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

WORKDIR /build

# Copy only whats needed for dependencies first
COPY pyproject.toml LICENSE README.md ./

# Copy the rest of the source files
COPY ./src ./src

# The built dashboard travels inside the wheel as package data. The Home
# Assistant stable add-on installs nothing but that wheel, so this copy is
# what keeps the add-on from shipping without a dashboard.
COPY --from=frontend /build/src/batcontrol/web/static ./src/batcontrol/web/static

# Build a wheel
RUN uv build --wheel --out-dir /wheels

# Stage 3: Build the final image
FROM python:3.14-alpine

ARG VERSION
ARG GIT_SHA

LABEL version="${VERSION}"
LABEL git-sha="${GIT_SHA}"
LABEL description="This is a Docker image for the BatControl project."
LABEL maintainer="matthias.strubel@aod-rpg.de"

# Install uv for fast, reliable Python packaging
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

# Copy the built wheel from the builder stage and install it
COPY --from=builder /wheels /wheels

# Install runtime dependencies using uv
# Prefer piwheels for compatible wheels and fall back to PyPI
RUN uv pip install --system --no-cache --index-url https://piwheels.org/simple --extra-index-url https://pypi.org/simple /wheels/*.whl && \
    rm -rf /wheels /usr/local/bin/uv

ENV BATCONTROL_VERSION=${VERSION}
ENV BATCONTROL_GIT_SHA=${GIT_SHA}
ENV BATCONTROL_RUNTIME_ENV="docker"
# Set default timezone to UTC, override with -e TZ=Europe/Berlin or similar
# when starting the container
# or set the timezone in docker-compose.yml in the environment section,
ENV TZ=UTC

# Create the app directory and copy the app files
RUN mkdir -p /app /app/logs /app/config
WORKDIR /app

COPY config/load_profile_default.csv ./config/load_profile.csv
COPY config/load_profile_default.csv ./default_load_profile.csv

# Copy all the other necessary runtime files
COPY LICENSE THIRD-PARTY-LICENSES.md entrypoint.sh ./
COPY config ./config_template

# Set the scripts as executable
RUN chmod +x entrypoint.sh

VOLUME ["/app/logs", "/app/config"]

# Read-only API, dashboard and MCP server. Only reachable when api.enabled is
# set in the configuration, and the bind address has to be 0.0.0.0 for a
# published port to work.
EXPOSE 8080

CMD ["/bin/sh", "/app/entrypoint.sh"]
