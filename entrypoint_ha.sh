#!/bin/sh
set -e

SHARED_FOLDER="/app/addon_config"
CONFIG_FOLDER="/app/config"
CONFIG_TEMPLATE_FOLDER="/app/config_template"

CONFIG_FILE="$CONFIG_FOLDER/batcontrol_config.yaml"
CONFIG_FILE_SHARED="$SHARED_FOLDER/batcontrol_config.yaml"
CONFIG_FILE_HA="/data/options.json"

LOAD_PROFILE="$CONFIG_FOLDER/load_profile.csv"
LOAD_PROFILE_DEFAULT="$CONFIG_TEMPLATE_FOLDER/load_profile_default.csv"
LOAD_PROFILE_SHARED="$SHARED_FOLDER/load_profile.csv"

LOG_FILE="/data/batcontrol.log"

# use custom config file, if available
if test -e $CONFIG_FILE_SHARED ; then
  echo "Using Config file found in addon config folder."
  ln -sf $CONFIG_FILE_SHARED $CONFIG_FILE
else
  echo "Config file batcontrol_config.yaml not found in addon config folder. Proceeding with data from HA Addon configuration"
  ln -sf $CONFIG_FILE_HA $CONFIG_FILE
fi

# use custom load profile, if available
if test -e $LOAD_PROFILE_SHARED ; then
  echo "Custom load_profile.csv found in addon config folder. Using custom load profile"
  ln -sf $LOAD_PROFILE_SHARED $LOAD_PROFILE
else
  echo "Custom load_profile.csv not found in addon config folder. Proceeding with default load profile"
  ln -sf $LOAD_PROFILE_DEFAULT $LOAD_PROFILE
fi

# Check if logfile exists. If not create an empty log file.
if test ! -e $LOG_FILE ; then
  echo "Creating log file at $LOG_FILE"
  touch $LOG_FILE
fi
# Create a symlink to /app/log/batcontrol.log
ln -sf $LOG_FILE /app/logs/batcontrol.log

# Start batcontrol.py
exec python -m batcontrol --data-dir /data/data "$@"
