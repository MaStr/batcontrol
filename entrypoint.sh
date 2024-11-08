#!/bin/sh
set -e

CONFIG_FILE="/app/config/batcontrol_config.yaml"

if test ! -e "/app/config/.init" ; then
  echo "Initializing config file from template"
  # Copy files but don't overwrite
  cp -nv /app/config_template/* /app/config
  echo "$BATCONTROL_VERSION" >  /app/config/.init
fi

# Check if the config file is available
if test ! -f "$CONFIG_FILE" ; then
  echo "ERROR: Config file not found: $CONFIG_FILE !"
  echo "       Please mount the config file to /app/config/batcontrol_config.yaml"
  echo "       You can download a sample config file from :"
  if [[ "snapshot" == "$BATCONTROL_VERSION" ]]; then
     echo "        https://raw.githubusercontent.com/muexxl/batcontrol/${BATCONTROL_GIT_SHA}/config/batcontrol_config_dummy.yaml"
  else
     echo "        https://raw.githubusercontent.com/muexxl/batcontrol/refs/tags/${BATCONTROL_VERSION}/config/batcontrol_config_dummy.yaml"
  fi
  echo ""
  echo "       In the config folder a template is available to copy."
  exit 1
fi

# Print BATCONTROL_VERSION and BATCONTROL_GIT_SHA
echo "BATCONTROL_VERSION: $BATCONTROL_VERSION"
echo "BATCONTROL_GIT_SHA: $BATCONTROL_GIT_SHA"

# Workaround for non configurable logfile path
# Create a symlink to /app/log/batcontrol.log
if test ! -e "/app/logs/batcontrol.log" ; then
  touch /app/logs/batcontrol.log
  ln -s /app/logs/batcontrol.log /app/batcontrol.log
fi

# Start batcontrol.py
exec python /app/batcontrol.py
