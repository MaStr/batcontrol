# Stage 1: Build Stage
FROM python:3.14-alpine AS builder

# Copy only whats needed for dependencies first
COPY pyproject.toml LICENSE README.MD ./

# Install build dependencies
RUN pip install setuptools>=66.0

# Copy the rest of the source files
COPY ./src ./src

# Build a wheel from the public Git repo
RUN pip wheel --no-cache-dir --no-deps --wheel-dir=/wheels .

# Stage 2: Build the final image
FROM python:3.14-alpine

ARG VERSION
ARG GIT_SHA

LABEL version="${VERSION}"
LABEL git-sha="${GIT_SHA}"
LABEL description="This is a Docker image for the BatControl project."
LABEL maintainer="matthias.strubel@aod-rpg.de"

# Copy the built wheel from the builder stage and install it
COPY --from=builder /wheels /wheels

# Update pip and install runtime dependencies
RUN pip install --no-cache-dir --extra-index-url https://piwheels.org/simple --prefer-binary /wheels/*.whl && rm -rf /wheels

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
