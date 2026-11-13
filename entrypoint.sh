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
  echo "Config file not found: $CONFIG_FILE"
  echo "Copying dummy config for first startup..."
  cp /app/config_template/batcontrol_config_dummy.yaml "$CONFIG_FILE"
  echo "IMPORTANT: Dummy configuration copied!"
  echo "          This uses a 'dummy' inverter for demonstration only."
  echo "          Please edit $CONFIG_FILE and:"
  echo "          1. Change inverter type from 'dummy' to your actual inverter (e.g., 'fronius_gen24')"
  echo "          2. Configure your inverter address, user, and password"
  echo "          3. Update PV installation details"
  echo "          4. Configure your electricity tariff"
  echo ""
  echo "       You can download the latest sample config from:"
  if [ "snapshot" = "$BATCONTROL_VERSION" ]; then
     echo "        https://raw.githubusercontent.com/MaStr/batcontrol/${BATCONTROL_GIT_SHA}/config/batcontrol_config_dummy.yaml"
  else
     echo "        https://raw.githubusercontent.com/MaStr/batcontrol/refs/tags/${BATCONTROL_VERSION}/config/batcontrol_config_dummy.yaml"
  fi
  echo ""
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

# Output the timezone
echo "Current local time is: $(date)"
echo "Configured timezone (env var TZ) is: $TZ"

# Start batcontrol.py
exec python -m batcontrol --data-dir /app/data "$@"
