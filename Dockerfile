# Stage 1: Build Stage
FROM python:3.13-alpine AS builder

# Install uv for fast, reliable Python packaging
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

# Copy only whats needed for dependencies first
COPY pyproject.toml LICENSE README.MD ./

# Copy the rest of the source files
COPY ./src ./src

# Build a wheel
RUN uv build --wheel --out-dir /wheels

# Stage 2: Build the final image
FROM python:3.13-alpine

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
COPY LICENSE entrypoint.sh ./
COPY config ./config_template

# Set the scripts as executable
RUN chmod +x entrypoint.sh

VOLUME ["/app/logs", "/app/config"]

CMD ["/bin/sh", "/app/entrypoint.sh"]
