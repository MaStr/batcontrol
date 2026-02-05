"""
This module provides a class `FroniusWR` for handling Fronius GEN24 Inverters.
It includes methods for interacting with the inverter's API, managing battery
configurations, and controlling various inverter settings.

The Fronius Web-API is a bit quirky, which is reflected in the code.

The Web-Login form does send a first request without authentication, which
returns a nonce. This nonce is then used to create a digest for the login
request.

Parts of the information can be called without authentication, but some
settings require authentication. We tackle a 401 as a signal to login again
and retry the request.

Yes, the Webfronted does send the password on each authenticated request hashed
with MD5, nounce etc.

"""
import time
import os
import logging
import json
import hashlib
from dataclasses import dataclass
import requests
from packaging import version
from cachetools import TTLCache
from .baseclass import InverterBaseclass

logger = logging.getLogger(__name__)
logger.info('Loading module ')

logger_auth = logging.getLogger("batcontrol.inverter.fronius.auth")


def hash_utf8(x, algorithm="MD5"):
    """Hash a string or bytes object.

    Args:
        x: String or bytes to hash
        algorithm: Hash algorithm to use ("MD5" or "SHA256")
    """
    if isinstance(x, str):
        x = x.encode("utf-8")

    if algorithm.upper() == "SHA256":
        return hashlib.sha256(x).hexdigest()
    else:  # Default to MD5 for backward compatibility
        return hashlib.md5(x).hexdigest()


def strip_dict(original):
    """Strip all keys starting with '_' from a dictionary."""
    # return unmodified original if its not a dict
    if not isinstance(original, dict):
        return original
    stripped_copy = {}
    for key in original.keys():
        if not key.startswith('_'):
            stripped_copy[key] = original[key]
    return stripped_copy


TIMEOFUSE_CONFIG_FILENAME = 'config/timeofuse_config.json'
BATTERY_CONFIG_FILENAME = 'config/battery_config.json'


class MockResponse:
    """ Mock response object to return when no update is needed """

    def __init__(self):
        self.text = '{"writeSuccess": ["timeofuse"]}'
        self.status_code = 200


@dataclass
class FroniusApiConfig:
    """Configuration for Fronius API endpoints and behavior."""
    from_version: version.Version
    to_version: version.Version
    version_path: str
    powerflow_path: str
    storage_path: str
    config_battery_path: str
    config_powerunit_path: str
    config_solar_api_path: str
    config_timeofuse_path: str
    commands_login_path: str
    commands_logout_path: str
    auth_algorithm: str = "SHA256"  # Authentication algorithm: "MD5" or "SHA256"


# Alle Konfigurationen in einer Liste
API_CONFIGS = [
    FroniusApiConfig(
        from_version=version.parse("0.0.0"),
        to_version=version.parse("1.28.7-1"),
        version_path='/status/version',
        powerflow_path='/solar_api/v1/GetPowerFlowRealtimeData.fcgi',
        storage_path='/solar_api/v1/GetStorageRealtimeData.cgi',
        config_battery_path='/config/batteries',
        config_powerunit_path='/config/setup/powerunit',
        config_solar_api_path='/config/solar_api',
        config_timeofuse_path='/config/timeofuse',
        commands_login_path='/commands/Login',
        commands_logout_path='/commands/Logout',
        auth_algorithm="MD5",
    ),
    FroniusApiConfig(
        from_version=version.parse("1.28.7-1"),
        to_version=version.parse("1.36"),
        version_path='/status/version',
        powerflow_path='/solar_api/v1/GetPowerFlowRealtimeData.fcgi',
        storage_path='/solar_api/v1/GetStorageRealtimeData.cgi',
        config_battery_path='/config/batteries',
        config_powerunit_path='/config/powerunit',
        config_solar_api_path='/config/solar_api',
        config_timeofuse_path='/config/timeofuse',
        commands_login_path='/commands/Login',
        commands_logout_path='/commands/Logout',
        auth_algorithm="MD5",
    ),
    FroniusApiConfig(
        from_version=version.parse("1.36"),
        to_version=version.parse("1.38.6-1"),
        version_path='/api/status/version',
        powerflow_path='/solar_api/v1/GetPowerFlowRealtimeData.fcgi',
        storage_path='/solar_api/v1/GetStorageRealtimeData.cgi',
        config_battery_path='/api/config/batteries',
        config_powerunit_path='/api/config/powerunit',
        config_solar_api_path='/api/config/solar_api',
        config_timeofuse_path='/api/config/timeofuse',
        commands_login_path='/api/commands/Login',
        commands_logout_path='/api/commands/Logout',
        auth_algorithm="MD5",
    ),
    FroniusApiConfig(
        from_version=version.parse("1.38.6-1"),
        to_version=version.parse("9999.99.99"),
        version_path='/api/status/version',
        powerflow_path='/solar_api/v1/GetPowerFlowRealtimeData.fcgi',
        storage_path='/solar_api/v1/GetStorageRealtimeData.cgi',
        config_battery_path='/api/config/batteries',
        config_powerunit_path='/api/config/powerunit',
        config_solar_api_path='/api/config/solar_api',
        config_timeofuse_path='/api/config/timeofuse',
        commands_login_path='/api/commands/Login',
        commands_logout_path='/api/commands/Logout',
        auth_algorithm="SHA256",
    ),
]


def get_api_config(fw_version: version) -> FroniusApiConfig:
    """Get the API configuration for the given firmware version."""
    for config in API_CONFIGS:
        if config.from_version <= fw_version < config.to_version:
            return config
    raise RuntimeError(
        f"Keine API Konfiguration für Firmware-Version {fw_version}")


class FroniusWR(InverterBaseclass):
    """ Class for Handling Fronius GEN24 Inverters """

    def __init__(self, config: dict) -> None:
        super().__init__(config)

        # We are doing three login tests during first login.
        # As MD5 was the default on the old firmware, the latest
        # retries should be MD5.
        self.usable_password_hash_methods = [
            "SHA256",  # First try: SHA256
            "MD5",     # Second try: MD5
            "MD5"      # Third try: MD5 again (retry with same method)
        ]
        self._last_password_hash_method_index = -1
        self.password_hash = None

        self.subsequent_login = False
        self.ncvalue_num = 1
        self.cnonce = hashlib.md5(os.urandom(8)).hexdigest()
        self.login_attempts = 0
        self.address = config['address']
        self.capacity = -1
        self.max_grid_charge_rate = config['max_grid_charge_rate']
        self.max_pv_charge_rate = config['max_pv_charge_rate']
        self.nonce = 0
        self.user = config['user']
        self.password = config['password']
        # Fronius API IDs - configurable with defaults
        self.inverter_id = str(config.get('fronius_inverter_id', '1'))
        self.controller_id = str(config.get('fronius_controller_id', '0'))
        self.fronius_version = self.get_firmware_version()
        self.api_config = get_api_config(self.fronius_version)

        # Verify that the configured IDs are valid
        self._verify_fronius_ids()

        self.previous_battery_config = self.get_battery_config()
        self.previous_backup_power_config = None
        # default values
        self.max_soc = 100
        self.min_soc = 5
        # Energy Management (EM)
        #  0 - On  (Automatic , Default)
        #  1 - Off (Adjustable)
        self.em_mode = self.previous_battery_config['HYB_EM_MODE']
        # Power in W  on in em_mode = 0
        #   negative = Feed-In (to grid)
        #   positive = Get from grid
        self.em_power = self.previous_battery_config['HYB_EM_POWER']

        self.set_solar_api_active(True)

        # Initialize SOC cache with 30-second TTL (maxsize=1 since we only cache one SOC value)
        self._soc_cache = TTLCache(maxsize=1, ttl=30)

        # Initialize time of use cache with 15-minute TTL (maxsize=1 since we only cache one config)
        # 15 minutes , 10 Seconds = 910 seconds
        self._time_of_use_cache = TTLCache(maxsize=1, ttl=910)

        if not self.previous_battery_config:
            raise RuntimeError(
                f'Failed to load Battery config from Inverter at {self.address}')
        try:
            self.previous_backup_power_config = self.get_powerunit_config()
        except RuntimeError:
            logger.error(
                'Failed to load Power Unit config from Inverter'
            )

        if self.previous_backup_power_config:
            self.backup_power_mode = self.previous_backup_power_config[
                'backuppower']['DEVICE_MODE_BACKUPMODE_TYPE_U16']
        else:
            logger.error(
                "Setting backup power mode to 0 as a fallback."
            )
            self.backup_power_mode = 0
            self.previous_backup_power_config = None

        if self.backup_power_mode == 0:
            # in percent
            self.min_soc = self.previous_battery_config['BAT_M0_SOC_MIN']
        else:
            # in percent
            self.min_soc = max(
                self.previous_battery_config['BAT_M0_SOC_MIN'],
                self.previous_battery_config['HYB_BACKUP_RESERVED']
            )
        self.max_soc = self.previous_battery_config['BAT_M0_SOC_MAX']
        self.backup_time_of_use()  # save timesofuse
        self.set_allow_grid_charging(True)

    def get_firmware_version(self) -> version:
        """ Get the firmware version of the inverter."""
        response = None

        # This stays as a hardcoded path for now
        # since 1.36 /api/status/version
        path = '/api/status/version'

        # Try to get the version from the new path
        try:
            response = self.send_request(
                path, method='GET', payload={}, auth=False)
        except RuntimeError:
            # If it fails, try the old path
            path = '/status/version'
            response = self.send_request(
                path, method='GET', payload={}, auth=False)

        if not response:
            raise RuntimeError('Failed to retrieve firmware version')
        version_dict = json.loads(response.text)
        version_string = version_dict["swrevisions"]["GEN24"]
        logger.info('Fronius firmware version: %s', version_string)
        return version.parse(version_string)

    def get_SOC(self):
        """ Get the state of charge (SOC) of the battery with 30-second caching."""
        # Check if we have a cached value
        cache_key = "soc"
        if cache_key in self._soc_cache:
            return self._soc_cache[cache_key]

        # Fetch fresh SOC value from inverter
        logger.debug("Fetching fresh SOC value from inverter")
        path = self.api_config.powerflow_path
        response = self.send_request(path)
        if not response:
            logger.error(
                'Failed to get SOC. Returning default value of 99.0'
            )
            return 99.0
        result = json.loads(response.text)
        soc = result['Body']['Data']['Inverters'][self.inverter_id]['SOC']

        # Cache the result
        self._soc_cache[cache_key] = soc
        logger.debug("Cached SOC value: %s", soc)
        return soc

    def get_battery_config(self):
        """ Get battery configuration from inverter and keep a backup."""
        path = self.api_config.config_battery_path
        response = self.send_request(path, auth=True)
        if not response:
            logger.error(
                'Failed to get battery configuration. Returning empty dict'
            )
            return {}

        result = json.loads(response.text)
        # only write file if it does not exist
        if not os.path.exists(BATTERY_CONFIG_FILENAME):
            with open(BATTERY_CONFIG_FILENAME, 'w', encoding='utf-8') as f:
                f.write(response.text)
        else:
            logger.warning(
                'Battery config file already exists. Not writing to %s',
                BATTERY_CONFIG_FILENAME
            )

        return result

    def get_powerunit_config(self):
        """ Get additional PowerUnit configuration for backup power.
        Returns: dict with backup power configuration
        """
        path = self.api_config.config_powerunit_path
        response = self.send_request(path, auth=True)
        if not response:
            logger.error(
                'Failed to get power unit configuration. Returning empty dict'
            )
            return {}
        result = json.loads(response.text)
        return result

    def restore_battery_config(self):
        """ Restore the previous battery config from a backup file."""
        settings_to_restore = [
            'BAT_M0_SOC_MAX',
            'BAT_M0_SOC_MIN',
            'BAT_M0_SOC_MODE',
            'HYB_BM_CHARGEFROMAC',
            'HYB_EM_MODE',
            'HYB_EM_POWER',
            'HYB_EVU_CHARGEFROMGRID'
        ]
        settings = {}
        for key in settings_to_restore:
            if key in self.previous_battery_config.keys():
                settings[key] = self.previous_battery_config[key]
            else:
                raise RuntimeError(
                    f"Unable to restore settings. Parameter {key} is missing"
                )
        path = self.api_config.config_battery_path
        payload = json.dumps(settings)
        logger.info(
            'Restoring previous battery configuration: %s ',
            payload
        )
        response = self.send_request(
            path, method='POST', payload=payload, auth=True)
        if not response:
            raise RuntimeError('failed to restore battery config')

        response_dict = json.loads(response.text)
        expected_write_successes = settings_to_restore
        for expected_write_success in expected_write_successes:
            if not expected_write_success in response_dict['writeSuccess']:
                raise RuntimeError(f'failed to set {expected_write_success}')
        # Remove after successful restore
        try:
            os.remove(BATTERY_CONFIG_FILENAME)
        except OSError:
            logger.error(
                'Could not remove battery config file %s', BATTERY_CONFIG_FILENAME)
        return response

    def set_allow_grid_charging(self, value: bool):
        """ Switches grid charging on (true) or off."""
        if value:
            payload = '{"HYB_EVU_CHARGEFROMGRID": true}'
        else:
            payload = '{"HYB_EVU_CHARGEFROMGRID": false}'
        path = self.api_config.config_battery_path
        response = self.send_request(
            path, method='POST', payload=payload, auth=True)
        response_dict = json.loads(response.text)
        expected_write_successes = ['HYB_EVU_CHARGEFROMGRID']
        for expected_write_success in expected_write_successes:
            if not expected_write_success in response_dict['writeSuccess']:
                raise RuntimeError(f'failed to set {expected_write_success}')
        return response

    def set_solar_api_active(self, value: bool):
        """ Switches Solar.API on (true) or off. Solar.API is required to get SOC values."""
        if value:
            payload = '{"SolarAPIv1Enabled": true}'
        else:
            payload = '{"SolarAPIv1Enabled": false}'
        path = self.api_config.config_solar_api_path
        response = self.send_request(
            path, method='POST', payload=payload, auth=True)
        response_dict = json.loads(response.text)
        expected_write_successes = ['SolarAPIv1Enabled']
        for expected_write_success in expected_write_successes:
            if not expected_write_success in response_dict['writeSuccess']:
                raise RuntimeError(f'failed to set {expected_write_success}')
        return response

    def set_wr_parameters(self, minsoc, maxsoc, allow_grid_charging, grid_power):
        """set power at grid-connection point negative values for Feed-In"""
        path = self.api_config.config_battery_path
        if not isinstance(allow_grid_charging, bool):
            raise RuntimeError(
                f'Expected type: bool actual type: {type(allow_grid_charging)}')

        grid_power = int(grid_power)
        minsoc = int(minsoc)
        maxsoc = int(maxsoc)

        if not 0 <= grid_power <= self.max_grid_charge_rate:
            raise RuntimeError(f'gridpower out of allowed limits {grid_power}')

        if minsoc > maxsoc:
            raise RuntimeError('Min SOC needs to be higher than Max SOC')

        if minsoc < self.min_soc:
            raise RuntimeError(f'Min SOC not allowed below {self.min_soc}')

        if maxsoc > self.max_soc:
            raise RuntimeError(f'Max SOC not allowed above {self.max_soc}')

        parameters = {'HYB_EVU_CHARGEFROMGRID': allow_grid_charging,
                      'HYB_EM_POWER': grid_power,
                      'HYB_EM_MODE': 1,
                      'BAT_M0_SOC_MIN': minsoc,
                      'BAT_M0_SOC_MAX': maxsoc,
                      'BAT_M0_SOC_MODE': 'manual'
                      }

        payload = json.dumps(parameters)
        logger.info('Setting battery parameters: %s', payload)

        response = self.send_request(
            path, method='POST', payload=payload, auth=True)
        if not response:
            logger.error(
                'Failed to set parameters. No response from server'
            )
            return response
        response_dict = json.loads(response.text)
        for expected_write_success in parameters.keys():
            if not expected_write_success in response_dict['writeSuccess']:
                raise RuntimeError(f'failed to set {expected_write_success}')
        return response

    def backup_time_of_use(self):
        """ Get time of use configuration from inverter and keep a backup."""
        result = self.get_time_of_use()
        # only write file if it does not exist
        if not os.path.exists(TIMEOFUSE_CONFIG_FILENAME):
            with open(TIMEOFUSE_CONFIG_FILENAME, 'w', encoding='utf-8') as f:
                f.write(json.dumps(result))
        else:
            logger.warning(
                'Time of use config file already exists. Not writing to %s',
                TIMEOFUSE_CONFIG_FILENAME
            )

        return result

    def get_time_of_use(self):
        """ Get time of use configuration from inverter with 15-minute caching."""
        # Check if we have a cached value
        cache_key = "time_of_use"
        if cache_key in self._time_of_use_cache:
            logger.debug("Returning cached time of use configuration")
            return self._time_of_use_cache[cache_key]

        # Fetch fresh time of use configuration from inverter
        logger.debug("Fetching fresh time of use configuration from inverter")
        path = self.api_config.config_timeofuse_path
        response = self.send_request(path, auth=True)
        if not response:
            return None

        result = json.loads(response.text)['timeofuse']

        # Cache the result
        self._time_of_use_cache[cache_key] = result
        logger.debug("Cached time of use configuration")

        return result

    def set_mode_avoid_discharge(self):
        """ Set the inverter to avoid discharging the battery."""
        timeofuselist = [{'Active': True,
                          'Power': int(0),
                          'ScheduleType': 'DISCHARGE_MAX',
                          "TimeTable": {"Start": "00:00", "End": "23:59"},
                          "Weekdays":
                          {"Mon": True,
                           "Tue": True,
                           "Wed": True,
                           "Thu": True,
                           "Fri": True,
                           "Sat": True,
                           "Sun": True}
                          }]
        return self.set_time_of_use(timeofuselist)

    def set_mode_allow_discharge(self):
        """ Set the inverter to discharge the battery."""
        timeofuselist = []
        if self.max_pv_charge_rate > 0:
            timeofuselist = [{'Active': True,
                              'Power': int(self.max_pv_charge_rate),
                              'ScheduleType': 'CHARGE_MAX',
                              "TimeTable": {"Start": "00:00", "End": "23:59"},
                              "Weekdays":
                              {"Mon": True,
                               "Tue": True,
                               "Wed": True,
                               "Thu": True,
                               "Fri": True,
                               "Sat": True,
                               "Sun": True}
                              }]
        response = self.set_time_of_use(timeofuselist)

        return response

    def set_mode_limit_battery_charge(self, limit_charge_rate: int):
        """ Limit PV charging rate while allowing battery discharge

        Args:
            limit_charge_rate: Maximum charge rate in W (0 = no charging)
        """
        if limit_charge_rate < 0:
            raise ValueError(f"limit_charge_rate must be >= 0, got {limit_charge_rate}")

        # Always set TimeOfUse rule for mode 8 (even if 0 = no charging)
        timeofuselist = [{'Active': True,
                          'Power': int(limit_charge_rate),
                          'ScheduleType': 'CHARGE_MAX',
                          "TimeTable": {"Start": "00:00", "End": "23:59"},
                          "Weekdays":
                          {"Mon": True,
                           "Tue": True,
                           "Wed": True,
                           "Thu": True,
                           "Fri": True,
                           "Sat": True,
                           "Sun": True}
                          }]
        response = self.set_time_of_use(timeofuselist)
        return response

    def set_mode_force_charge(self, chargerate=500):
        """ Set the inverter to charge the battery with a specific power from GRID."""
        # activate timeofuse rules
        chargerate = min(chargerate, self.max_grid_charge_rate)
        timeofuselist = [{'Active': True,
                          'Power': int(chargerate),
                          'ScheduleType': 'CHARGE_MIN',
                          "TimeTable": {"Start": "00:00", "End": "23:59"},
                          "Weekdays":
                          {"Mon": True,
                           "Tue": True,
                           "Wed": True,
                           "Thu": True,
                           "Fri": True,
                           "Sat": True,
                           "Sun": True}
                          }]
        return self.set_time_of_use(timeofuselist)

    def restore_time_of_use_config(self):
        """ Restore the previous time of use config from a backup file."""
        try:
            with open(TIMEOFUSE_CONFIG_FILENAME, 'r', encoding="utf-8") as f:
                time_of_use_config_json = f.read()
        except OSError:
            logger.error('Could not restore timeofuse config')
            return

        try:
            time_of_use_config = json.loads(time_of_use_config_json)
        except:  # pylint: disable=bare-except
            logger.error(
                'Could not parse timeofuse config from %s',
                TIMEOFUSE_CONFIG_FILENAME
            )
            return

        stripped_time_of_use_config = []
        for listitem in time_of_use_config:
            new_item = {}
            new_item['Active'] = listitem['Active']
            new_item['Power'] = listitem['Power']
            new_item['ScheduleType'] = listitem['ScheduleType']
            new_item['TimeTable'] = {
                'Start': listitem['TimeTable']['Start'],
                'End': listitem['TimeTable']['End']
            }
            weekdays = {}
            for day in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']:
                weekdays[day] = listitem['Weekdays'][day]
            new_item['Weekdays'] = weekdays
            stripped_time_of_use_config.append(new_item)

        self.set_time_of_use(stripped_time_of_use_config)
        # After restoring the time of use config, delete the backup
        try:
            os.remove(TIMEOFUSE_CONFIG_FILENAME)
        except OSError:
            logger.error(
                'Could not remove timeofuse config file %s', TIMEOFUSE_CONFIG_FILENAME
            )

    def _compare_timeofuse_essentials(self, current_timeofuse, new_timeofuse):
        """Compare only ScheduleType and Power values of timeofuse configurations."""
        if len(current_timeofuse) != len(new_timeofuse):
            return False

        for i, (current_item, new_item) in enumerate(zip(current_timeofuse, new_timeofuse)):
            # Compare only ScheduleType and Power values
            if (current_item.get('ScheduleType') != new_item.get('ScheduleType') or
                    current_item.get('Power') != new_item.get('Power')):
                logger.debug("Time of use item %d differs in essential values: "
                             "ScheduleType current=%s vs new=%s, "
                             "Power current=%s vs new=%s",
                             i, current_item.get(
                                 'ScheduleType'), new_item.get('ScheduleType'),
                             current_item.get('Power'), new_item.get('Power'))
                return False

        return True

    def set_time_of_use(self, timeofuselist):
        """ Set the planned battery charge/discharge schedule."""
        # Get current time of use configuration to check if update is needed
        current_timeofuse = self.get_time_of_use()

        # Compare only ScheduleType and Power values to avoid unnecessary updates
        if current_timeofuse is not None and \
           self._compare_timeofuse_essentials(current_timeofuse, timeofuselist):
            logger.debug("Time of use configuration (ScheduleType and Power) is"
                         " already identical, skipping update")
            # Return a mock response object to maintain compatibility
            return MockResponse()

        config = {
            'timeofuse': timeofuselist
        }
        payload = json.dumps(config)
        path = self.api_config.config_timeofuse_path
        logger.info("Updating time of use configuration")
        response = self.send_request(
            path, method='POST', payload=payload, auth=True
        )
        if not response:
            raise RuntimeError('Failed to set time of use configuration')
        response_dict = json.loads(response.text)
        expected_write_successes = ['timeofuse']
        for expected_write_success in expected_write_successes:
            if not expected_write_success in response_dict['writeSuccess']:
                raise RuntimeError(f'failed to set {expected_write_success}')

        # Invalidate the cache after successfully updating the configuration
        cache_key = "time_of_use"
        if cache_key in self._time_of_use_cache:
            del self._time_of_use_cache[cache_key]
            logger.debug("Invalidated time of use cache after update")

        return response

    def get_capacity(self):
        """ Get the full and raw capacity of the battery in Wh."""
        if self.capacity >= 0:
            return self.capacity

        path = self.api_config.storage_path
        response = self.send_request(path)
        if not response:
            logger.warning(
                'Capacity request failed. Returning default value'
            )
            return 1000
        result = json.loads(response.text)
        capacity = result['Body']['Data'][self.controller_id]['Controller']['DesignedCapacity']
        self.capacity = capacity
        return capacity

    def _verify_fronius_ids(self):
        """
        Verify that the configured inverter_id and controller_id are valid.

        This method makes test calls to the Fronius API to ensure the configured
        IDs exist in the actual API responses. If verification fails, it logs
        the complete JSON response to help with debugging.
        """
        # Verify inverter_id by checking powerflow data
        try:
            logger.info('Verifying Fronius inverter_id: %s', self.inverter_id)
            path = self.api_config.powerflow_path
            response = self.send_request(path)
            if response:
                result = json.loads(response.text)
                inverters = result.get('Body', {}).get(
                    'Data', {}).get('Inverters', {})

                if self.inverter_id not in inverters:
                    logger.error(
                        'Configured inverter_id "%s" not found in Fronius API response.',
                        self.inverter_id
                    )
                    logger.error(
                        'Available inverter IDs: %s',
                        list(inverters.keys())
                    )
                    logger.error(
                        'Complete Inverters JSON response:\n%s',
                        json.dumps(inverters, indent=2)
                    )
                    raise RuntimeError(
                        f'Invalid fronius_inverter_id "{self.inverter_id}". '
                        f'Available IDs: {list(inverters.keys())}'
                    )
                logger.info(
                    'Inverter ID "%s" verified successfully', self.inverter_id)
        except (KeyError, json.JSONDecodeError) as e:
            logger.error(
                'Failed to verify inverter_id due to unexpected response format: %s',
                e
            )
            if response:
                logger.error('Complete API response:\n%s', response.text)
            raise RuntimeError(
                f'Failed to verify fronius_inverter_id "{self.inverter_id}": {e}'
            )

        # Verify controller_id by checking storage data
        try:
            logger.info('Verifying Fronius controller_id: %s',
                        self.controller_id)
            path = self.api_config.storage_path
            response = self.send_request(path)
            if response:
                result = json.loads(response.text)
                data = result.get('Body', {}).get('Data', {})

                if self.controller_id not in data:
                    logger.error(
                        'Configured controller_id "%s" not found in Fronius API response.',
                        self.controller_id
                    )
                    logger.error(
                        'Available controller IDs: %s',
                        list(data.keys())
                    )
                    logger.error(
                        'Complete Storage Data JSON response:\n%s',
                        json.dumps(data, indent=2)
                    )
                    raise RuntimeError(
                        f'Invalid fronius_controller_id "{self.controller_id}". '
                        f'Available IDs: {list(data.keys())}'
                    )

                # Also verify that the Controller key exists within the data
                controller_data = data.get(self.controller_id, {})
                if 'Controller' not in controller_data:
                    logger.error(
                        'Controller data not found for controller_id "%s"',
                        self.controller_id
                    )
                    logger.error(
                        'Complete data for controller_id "%s":\n%s',
                        self.controller_id,
                        json.dumps(controller_data, indent=2)
                    )
                    raise RuntimeError(
                        f'No Controller data found for fronius_controller_id "{self.controller_id}"'
                    )
                logger.info(
                    'Controller ID "%s" verified successfully', self.controller_id)
        except (KeyError, json.JSONDecodeError) as e:
            logger.error(
                'Failed to verify controller_id due to unexpected response format: %s',
                e
            )
            if response:
                logger.error('Complete API response:\n%s', response.text)
            raise RuntimeError(
                f'Failed to verify fronius_controller_id "{self.controller_id}": {e}'
            )

    def send_request(self, path, method='GET', payload="", params=None, headers=None, auth=False):
        """Send a HTTP REST request to the inverter.

            auth = This request needs to be run with authentication.
            is_login = This request is a login request. Do not retry on 401.
        """
        logger.debug("Sending request to %s", path)
        if not headers:
            headers = {}
        for i in range(3):
            # Try tp send the request, if it fails, try to login and resend
            response = self.__send_one_http_request(
                path, method, payload, params, headers, auth)
            if response.status_code == 200:
                if auth:
                    self.__retrieve_auth_from_response(response)
                return response
            # 401 - unauthorized , relogin
            # 403 - is forbidden, what happens at 01.00 in the night
            if response.status_code in (401, 403):
                self.__retrieve_auth_from_response(response)
                self.login()
            else:
                raise RuntimeError(
                    f"[Inverter] Request {i} failed with {response.status_code}-"
                    f"{response.reason}. \n"
                    f"\t path:{path}, \n\tparams:{params} \n\theaders {headers} \n"
                    f"\tnonce {self.nonce} \n"
                    f"\tpayload {payload}"
                )
        return None

    def __send_one_http_request(self, path, method='GET', payload="",
                                params=None, headers=None, auth=False):
        """ Send one HTTP Request to the backend.
            This method does not handle application errors, only connection errors.
        """
        if not headers:
            headers = {}
        url = 'http://' + self.address + path
        fullpath = path
        if params:
            fullpath += '?' + \
                "&".join(
                    [f'{k+"="+str(params[k])}' for k in params.keys()])
        if auth:
            headers['Authorization'] = self.get_auth_header(
                method=method, path=fullpath)
            logger_auth.debug("Requesting %s , header: %s",
                              fullpath, headers)

        for i in range(3):
            # 3 retries if connection can't be established
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    params=params,
                    headers=headers,
                    data=payload,
                    timeout=30
                )
                return response
            except requests.exceptions.ConnectionError as err:
                logger.error(
                    "Connection to Inverter failed on %s. (%d) "
                    "Retrying in 60 seconds, Error %s",
                    self.address,
                    i,
                    err
                )
                time.sleep(60)

        logger.error('Request failed without response.')
        raise RuntimeError(
            f"\turl:{url}, \n\tparams:{params} \n\theaders {headers} \n"
            f"\tnonce {self.nonce} \n"
            f"\tpayload {payload}"
        )

    def login(self):
        """Login to Fronius API"""
        logger_auth.debug("Logging in")
        path = self.api_config.commands_login_path
        self.cnonce = hashlib.md5(os.urandom(8)).hexdigest()
        self.ncvalue_num = 1
        self.login_attempts = 0
        for i in range(3):
            self.login_attempts += 1
            response = self.__send_one_http_request(path, auth=True)
            if response.status_code == 200:
                if not self.subsequent_login:
                    self.__store_latest_password_hash_method()
                self.subsequent_login = True
                logger_auth.info('Login successful %s', response)
                logger_auth.debug("Response: %s", response.headers)
                self.__retrieve_auth_from_response(response)
                self.login_attempts = 0
                return
            elif response.status_code == 401:
                self.__retrieve_auth_from_response(response)

            logger_auth.error(
                'Login -%d- failed, Response: %s', i, response)
            logger_auth.error('Response: %s ; %s', response.headers, response)
            if self.subsequent_login:
                logger_auth.info(
                    "Retrying login in 10 seconds")
                time.sleep(10)
        if self.login_attempts >= 3:
            logger_auth.info(
                'Login failed 3 times .. aborting'
            )
            raise RuntimeError(
                'Login failed repeatedly .. wrong credentials?'
            )

    def logout(self):
        """Logout from Fronius API"""
        path = self.api_config.commands_logout_path
        response = self.send_request(path, auth=True)
        if not response:
            logger_auth.warning('Logout failed. No response from server')
        if response.status_code == 200:
            logger_auth.info('Logout successful')
        else:
            logger_auth.info('Logout failed')
        return response

    def __retrieve_auth_from_response(self, response):
        """Get & store the authentication parts from response auth header.
            - nc
            - cnonce
            - nonce
        """
        auth_dict = self.__split_response_auth_header(response)
        if auth_dict.get('nc'):
            self.ncvalue_num = int(auth_dict['nc']) + 1
        else:
            self.ncvalue_num = 1
        if auth_dict.get('cnonce'):
            self.cnonce = auth_dict['cnonce']
        if auth_dict.get('nonce'):
            self.nonce = auth_dict['nonce']

        logger_auth.debug("nc: %s, cnonce: %s, nonce: %s",
                          self.ncvalue_num,
                          self.cnonce,
                          self.nonce
                          )

    def __split_response_auth_header(self, response):
        """ Split the response header into a dictionary."""
        auth_dict = {}
        # stupid API bug: nonce headers with different capitalization at different end points
        if 'X-WWW-Authenticate' in response.headers:
            auth_string = response.headers['X-WWW-Authenticate']
        elif 'X-Www-Authenticate' in response.headers:
            auth_string = response.headers['X-Www-Authenticate']
        elif 'Authentication-Info' in response.headers:
            auth_string = response.headers['Authentication-Info']
        else:
            # Return an empty dict to work with Fronius below 1.35.4-1
            logger_auth.debug(
                'No authentication header found in response')
            return auth_dict

        # Remove quotes and split by comma
        auth_list = auth_string.replace('"', '').split(',')
        logger_auth.debug("Authentication header: %s", auth_list)
        auth_dict = {}
        for item in auth_list:
            # Strip whitespace from each item and check if it contains '='
            item = item.strip()
            if '=' in item:
                key, value = item.split("=", 1)  # Split only on first '='
                key = key.strip()
                value = value.strip()
                auth_dict[key] = value
                logger_auth.debug(
                    "Authentication header key-value pair - %s: %s", key, value)
        return auth_dict

    def get_auth_header(self, method, path) -> str:
        """Create the Authorization header for the request."""
        nonce = self.nonce
        realm = 'Webinterface area'
        ncvalue = f"{self.ncvalue_num:08d}"
        cnonce = self.cnonce
        user = self.user
        password = self.password
        algorithm = self.api_config.auth_algorithm
        password_algorithm = algorithm

        password_algorithm = self.__get_password_hash_method()

        if len(self.user) < 4:
            raise RuntimeError("User needed for Authorization")
        if len(self.password) < 4:
            raise RuntimeError("Password needed for Authorization")

        a1 = f"{user}:{realm}:{password}"
        a2 = f"{method}:{path}"
        ha1 = hash_utf8(a1, password_algorithm)
        ha2 = hash_utf8(a2, algorithm)
        noncebit = f"{nonce}:{ncvalue}:{cnonce}:auth:{ha2}"
        respdig = hash_utf8(f"{ha1}:{noncebit}", algorithm)
        auth_header = f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{path}", '
        auth_header += f'algorithm="{algorithm}", qop=auth, nc={ncvalue}, cnonce="{cnonce}", '
        auth_header += f'response="{respdig}"'
        return auth_header

    def __get_password_hash_method(self) -> str:
        """ Figure out the password hash method during first login."""
        # If we already found a working method, use it
        if self.password_hash is not None:
            return self.password_hash

        # Index is initialized to -1. Increment to get the next method.
        password_algorithm = ""
        if self.api_config.auth_algorithm == "SHA256":
            self._last_password_hash_method_index += 1
            if self._last_password_hash_method_index >= len(self.usable_password_hash_methods):
                self._last_password_hash_method_index = 0
            password_algorithm = self.usable_password_hash_methods[
                self._last_password_hash_method_index
            ]
            logger_auth.debug(
                "Trying password hash method %s", password_algorithm)
        else:
            # Fallback to MD5 only for older firmwares
            password_algorithm = "MD5"
            # Set password_hash immediately for MD5 since there's only one option
            # Setting this here prevents __store_latest_password_hash_method from changing it later
            self.password_hash = password_algorithm

        return password_algorithm

    def __store_latest_password_hash_method(self):
        """ Save the password hash method to use after a successful login."""
        if self.password_hash is not None:
            # We already have a working method, do not change it
            return
        self.password_hash = self.usable_password_hash_methods[
            self._last_password_hash_method_index
        ]
        logger_auth.debug("Password hash method set to %s",
                          self.password_hash)

    def __set_em(self, mode=None, power=None):
        """ Change Energy Management """
        settings = {}
        settings = {
            'HYB_EM_MODE': self.em_mode,
            'HYB_EM_POWER': self.em_power
        }

        if mode is not None:
            settings['HYB_EM_MODE'] = mode
        if power is not None:
            settings['HYB_EM_POWER'] = power

        path = self.api_config.config_battery_path
        payload = json.dumps(settings)
        logger.info(
            'Setting EM mode %s , power %s',
            mode,
            power
        )
        response = self.send_request(
            path, method='POST', payload=payload, auth=True)
        if not response:
            raise RuntimeError('Failed to set EM')

    def set_em_power(self, power):
        """ Change Energy Manangement Power
            positive = get from grid
            negative = feed to grid
        """
        self.__set_em(power=power)
        self.em_power = power
        if self.mqtt_api:
            self.mqtt_api.generic_publish(
                self.get_mqtt_inverter_topic() + 'em_power', power)

    def set_em_mode(self, mode):
        """ Change Energy Manangement mode."""
        self.__set_em(mode=mode)
        self.em_mode = mode
        if self.mqtt_api:
            self.mqtt_api.generic_publish(
                self.get_mqtt_inverter_topic() + 'em_mode', mode)

    def shutdown(self):
        """Change back batcontrol changes."""
        logger.info('Reverting batcontrol created config changes')
        self.restore_battery_config()
        self.restore_time_of_use_config()
        self.logout()

    def activate_mqtt(self, api_mqtt_api):
        """
        Activates MQTT for the inverter.

        This function starts the API functions and publishes all internal values via MQTT.
        The MQTT topic is: base_topic + '/inverters/0/'

        Parameters that can be set via MQTT:
        - max_grid_charge_rate (int): Maximum power in W that can be
                                          used to load the battery from the grid.
        - max_pv_charge_rate (int): Maximum power in W that can be
                                          used to load the battery from the PV.

        Args:
            api_mqtt_api: The MQTT API instance to be used for registering callbacks.

        """
        self.mqtt_api = api_mqtt_api
        # /set is appended to the topic
        self.mqtt_api.register_set_callback(self.get_mqtt_inverter_topic(
        ) + 'max_grid_charge_rate', self.api_set_max_grid_charge_rate, int)
        self.mqtt_api.register_set_callback(self.get_mqtt_inverter_topic(
        ) + 'max_pv_charge_rate', self.api_set_max_pv_charge_rate, int)
        self.mqtt_api.register_set_callback(self.get_mqtt_inverter_topic(
        ) + 'em_mode', self.api_set_em_mode, int)
        self.mqtt_api.register_set_callback(self.get_mqtt_inverter_topic(
        ) + 'em_power', self.api_set_em_power, int)

        self.publish_inverter_discovery_messages()

    def refresh_api_values(self):
        """ Publishes all values to mqtt."""
        if self.mqtt_api:
            # Call parent to publish standard metrics
            super().refresh_api_values()
            # Publish Fronius-specific values
            self.mqtt_api.generic_publish(self.get_mqtt_inverter_topic(
            ) + 'max_grid_charge_rate', self.max_grid_charge_rate)
            self.mqtt_api.generic_publish(self.get_mqtt_inverter_topic(
            ) + 'max_pv_charge_rate', self.max_pv_charge_rate)
            self.mqtt_api.generic_publish(
                self.get_mqtt_inverter_topic() + 'min_soc', self.min_soc)
            self.mqtt_api.generic_publish(
                self.get_mqtt_inverter_topic() + 'max_soc', self.max_soc)
            self.mqtt_api.generic_publish(
                self.get_mqtt_inverter_topic() + 'capacity', self.get_capacity())
            self.mqtt_api.generic_publish(
                self.get_mqtt_inverter_topic() + 'em_mode', self.em_mode)
            self.mqtt_api.generic_publish(
                self.get_mqtt_inverter_topic() + 'em_power', self.em_power)

    def publish_inverter_discovery_messages(self):
        """Publish Home Assistant MQTT Auto Discovery messages for Fronius-specific sensors"""
        # First publish common inverter sensors
        super().publish_inverter_discovery_messages()

        # Then publish Fronius-specific configuration sensors
        if self.mqtt_api:
            topic = self.get_mqtt_inverter_topic()
            base_topic = self.mqtt_api.base_topic

            # Fronius-specific configuration
            self.mqtt_api.publish_mqtt_discovery_message(
                f"Inverter {self.inverter_num} Max Grid Charge Rate",
                f"batcontrol_inverter_{self.inverter_num}_max_grid_charge_rate",
                "number", "power", "W",
                base_topic + "/" + topic + "max_grid_charge_rate",
                base_topic + "/" + topic + "max_grid_charge_rate/set",
                entity_category="config",
                min_value=0, max_value=10000, initial_value=10000)

            self.mqtt_api.publish_mqtt_discovery_message(
                f"Inverter {self.inverter_num} Max PV Charge Rate",
                f"batcontrol_inverter_{self.inverter_num}_max_pv_charge_rate",
                "number", "power", "W",
                base_topic + "/" + topic + "max_pv_charge_rate",
                base_topic + "/" + topic + "max_pv_charge_rate/set",
                entity_category="config",
                min_value=0, max_value=10000, initial_value=10000)

            self.mqtt_api.publish_mqtt_discovery_message(
                f"Inverter {self.inverter_num} EM Mode",
                f"batcontrol_inverter_{self.inverter_num}_em_mode",
                "number", None, None,
                base_topic + "/" + topic + "em_mode",
                base_topic + "/" + topic + "em_mode/set",
                entity_category="config",
                min_value=0, max_value=2, initial_value=0)

            self.mqtt_api.publish_mqtt_discovery_message(
                f"Inverter {self.inverter_num} EM Power",
                f"batcontrol_inverter_{self.inverter_num}_em_power",
                "number", "power", "W",
                base_topic + "/" + topic + "em_power",
                base_topic + "/" + topic + "em_power/set",
                entity_category="config",
                min_value=-10000, max_value=10000, initial_value=0)

    def api_set_max_grid_charge_rate(self, max_grid_charge_rate: int):
        """ Set the maximum power in W that can be used to load the battery from the grid."""
        if max_grid_charge_rate < 0:
            logger.warning(
                'API: Invalid max_grid_charge_rate %sW',
                max_grid_charge_rate
            )
            return
        logger.info(
            'API: Setting max_grid_charge_rate: %.1fW',
            max_grid_charge_rate
        )
        self.max_grid_charge_rate = max_grid_charge_rate

    def api_set_max_pv_charge_rate(self, max_pv_charge_rate: int):
        """ Set the maximum power in W that can be used to load the battery from the PV."""
        if max_pv_charge_rate < 0:
            logger.warning(
                'API: Invalid max_pv_charge_rate %s',
                max_pv_charge_rate
            )
            return
        logger.info(
            'API: Setting max_pv_charge_rate: %.1fW',
            max_pv_charge_rate
        )
        self.max_pv_charge_rate = max_pv_charge_rate

    def api_set_em_mode(self, em_mode: int):
        """ Set the Energy Management Mode."""
        if not isinstance(em_mode, int):
            logger.warning(
                'API: Invalid type em_mode %s',
                em_mode
            )
            return
        if em_mode < 0 or em_mode > 2:
            logger.warning(
                'API: Invalid em_mode %s',
                em_mode
            )
            return
        logger.info(
            'API: Setting em_mode: %s',
            em_mode
        )
        self.set_em_mode(em_mode)

    def api_set_em_power(self, em_power: int):
        """ Change EnergeManagement Offset
            positive = get from grid
            negative = feed to grid
        """
        if not isinstance(em_power, int):
            logger.warning(
                'API: Invalid type em_power %s',
                em_power
            )
            return
        logger.info(
            'API: Setting em_power: %s',
            em_power
        )
        self.set_em_power(em_power)
