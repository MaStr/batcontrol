#!/bin/bash

CONFIG_FILE="/app/config/batcontrol_config.yaml"

# Check if the config file is mounted
if [ ! -f "$CONFIG_FILE" ]; then
  echo "ERROR: Config file not found: $CONFIG_FILE !"
  echo "       Please mount the config file to /app/config/batcontrol_config.yaml"
  echo "       You can download a sample config file from :"
  if [ "snapshot" == "$BATCONTROL_VERSION" ]; then
     echo "        https://raw.githubusercontent.com/muexxl/batcontrol/${BATCONTROL_GIT_SHA}/config/batcontrol_config_dummy.yaml"
  else
     echo "        https://raw.githubusercontent.com/muexxl/batcontrol/refs/tags/${BATCONTROL_VERSION}/config/batcontrol_config_dummy.yaml"
  fi
  exit 1
fi

# Print BATCONTROL_VERSION and BATCONTROL_GIT_SHA
echo "BATCONTROL_VERSION: $BATCONTROL_VERSION"
echo "BATCONTROL_GIT_SHA: $BATCONTROL_GIT_SHA"

# Start batcontrol.py
exec python /app/batcontrol.py