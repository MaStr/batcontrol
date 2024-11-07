#!/bin/bash

CONFIG_FILE="/app/config/batcontrol_config.yaml"

# Check if the config file is mounted
if [ ! -f "$CONFIG_FILE" ]; then
  echo "Config file not found: $CONFIG_FILE"
  exit 1
fi

# Print BATCONTROL_VERSION and BATCONTROL_GIT_SHA
echo "BATCONTROL_VERSION: $BATCONTROL_VERSION"
echo "BATCONTROL_GIT_SHA: $BATCONTROL_GIT_SHA"

# Start batcontrol.py
exec python /app/batcontrol.py