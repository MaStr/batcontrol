#! /usr/bin/env python
# %%
import sys
import datetime
import time
import os
import logging
import yaml
import pytz
import numpy as np

from forecastconsumption import forecastconsumption
from forecastsolar import forecastsolar
from dynamictariff import dynamictariff
from inverter import inverter
from logfilelimiter import logfilelimiter

LOGFILE = "batcontrol.log"
CONFIGFILE = "config/batcontrol_config.yaml"
VALID_UTILITIES = ['tibber','awattar_at','awattar_de','evcc']
VALID_INVERTERS = ['fronius_gen24' , 'testdriver']
ERROR_IGNORE_TIME = 600
TIME_BETWEEN_EVALUATIONS = 120
TIME_BETWEEN_UTILITY_API_CALLS=900 #15 Minutes

MODE_ALLOW_DISCHARGING = 10
MODE_AVOID_DISCHARGING = 0
MODE_FORCE_CHARGING = -1

loglevel = logging.DEBUG
logger = logging.getLogger(__name__)
formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                              "%Y-%m-%d %H:%M:%S")

filehandler = logging.FileHandler(LOGFILE)
filehandler.setFormatter(formatter)
logger.addHandler(filehandler)

streamhandler = logging.StreamHandler(sys.stdout)
streamhandler.setFormatter(formatter)

logger.addHandler(streamhandler)

logger.setLevel(loglevel)

logger.info(f'[Main] Starting Batcontrol ')


class Batcontrol(object):
    def __init__(self, configfile, is_simulation=False):
        # For API
        self.last_mode = None
        self.last_charge_rate = 0
        self.last_prices = None
        self.last_consumption = None
        self.last_production = None
        self.last_net_consumption = None

        self.last_stored_energy = -1
        self.last_reserved_energy = -1
        self.last_max_capacity = -1

        self.discharge_limit = 0

        self.fetched_stored_energy = False
        self.fetched_reserved_energy = False
        self.fetched_max_capacity = False

        self.last_run_time = 0

        self.load_config(configfile)
        config = self.config

        if config['max_logfile_size'] > 0:
            self.logfilelimiter =logfilelimiter.LogFileLimiter(LOGFILE,config['max_logfile_size'])


        timezone = pytz.timezone(config['timezone'])
        self.timezone = timezone

        try:
            tz=os.environ['TZ']
            logger.info(f"[Batcontrol] host system time zone is {tz}")
        except KeyError:
            logger.info(
                "[Batcontrol] host system time zone was not set. Setting to %s",
                config['timezone']
            )
            os.environ['TZ'] = config['timezone']
        time.tzset()



        self.is_simulation = is_simulation

        apikey = config['utility']['apikey']
        provider = config['utility']['type']
        self.dynamic_tariff = dynamictariff.DynamicTariff(
            config['utility'],
            timezone,
            TIME_BETWEEN_UTILITY_API_CALLS
        )

        self.inverter = inverter.Inverter(config['inverter'])

        self.pvsettings = config['pvinstallations']
        self.fc_solar = forecastsolar.ForecastSolar(self.pvsettings, timezone)

        self.load_profile = config['consumption_forecast']['load_profile']
        try:
            annual_consumption = config['consumption_forecast']['annual_consumption']
        except KeyError:
            # default setting
            annual_consumption = 0

        self.fc_consumption = forecastconsumption.ForecastConsumption(
            self.load_profile, timezone, annual_consumption)

        self.batconfig = config['battery_control']
        self.time_at_forecast_error=-1

        self.always_allow_discharge_limit = self.batconfig['always_allow_discharge_limit']
        self.max_charging_from_grid_limit = self.batconfig['max_charging_from_grid_limit']
        self.min_price_difference = self.batconfig['min_price_difference']

        self.api_overwrite = False

        self.mqtt_api = None
        if 'mqtt' in config.keys():
            if config['mqtt']['enabled'] == True:
                logger.info(f'[Main] MQTT Connection enabled ')
                import mqtt_api
                self.mqtt_api = mqtt_api.MQTT_API(config['mqtt'])
                self.mqtt_api.wait_ready()
                # Register for callbacks
                self.mqtt_api.register_set_callback(
                    'mode',
                    self.api_set_mode,
                    int
                )
                self.mqtt_api.register_set_callback(
                    'charge_rate',
                    self.api_set_charge_rate,
                    int
                )
                self.mqtt_api.register_set_callback(
                    'always_allow_discharge_limit',
                    self.api_set_always_allow_discharge_limit,
                    float
                )
                self.mqtt_api.register_set_callback(
                    'max_charging_from_grid_limit',
                    self.api_set_max_charging_from_grid_limit,
                    float
                )
                self.mqtt_api.register_set_callback(
                    'min_price_difference',
                    self.api_set_min_price_difference,
                    float
                )
                # Inverter Callbacks
                self.inverter.activate_mqtt(self.mqtt_api)
                logger.info(f'[Main] MQTT Connection ready ')



    def __del__(self):
        try:
            del self.inverter
        except:
            pass

    def load_config(self, configfile):

        if not os.path.isfile(configfile):
            raise RuntimeError(f'Configfile {configfile} not found')

        with open(configfile, 'r') as f:
            config_str = f.read()

        config = yaml.safe_load(config_str)

        if config['utility']['type'] in VALID_UTILITIES:
            pass
        else:
            raise RuntimeError('Unkonwn Utility')

        if config['utility']['type'] =='tibber':
            if 'apikey' in config['utility'].keys():
                pass
            else:
                raise RuntimeError(
                    '[BatCtrl] Utility Tibber requires an apikey. '
                    'Please provide the apikey in your configuration file'
                )
        elif config['utility']['type'] in ['evcc']:
            if 'url' in config['utility'].keys():
                pass
            else:
                raise RuntimeError(
                    '[BatCtrl] Utility EVCC requires an URL. '
                    'Please provide the URL in your configuration file'
                )
        else:
            config['utility']['apikey']=None

        if config['inverter']['type'] in VALID_INVERTERS:
            pass
        else:
            raise RuntimeError('Unkown inverter')

        if config['pvinstallations']:
            pass
        else:
            raise RuntimeError('No PV Installation found')

        try:
            config['consumption_forecast']['load_profile'] = 'config/' + \
                config['consumption_forecast']['load_profile']
        except:
            logger.info(
                "[Config] No load profile provided. "
                "Proceeding with default profile from default_load_profile.csv"
            )
            config['consumption_forecast']['load_profile'] = 'default_load_profile.csv'

        if not os.path.isfile(config['consumption_forecast']['load_profile']):
            raise RuntimeError(
                "[Config] Specified Load Profile file " +
                f"'{config['consumption_forecast']['load_profile']}' not found"
            )

        try:
            tzstring = config['timezone']
        except KeyError:
            raise RuntimeError(
                f"Config Entry in general: timezone {config['timezone']} " +
                 "not valid. Try e.g. 'Europe/Berlin'"
            )
        try:
            loglevel=config['loglevel']
        except KeyError:
            loglevel='info'

        if loglevel=='debug':
            logger.setLevel(logging.DEBUG)
        elif loglevel =='warning':
            logger.setLevel(logging.WARNING)
        elif loglevel =='error':
            logger.setLevel(logging.ERROR)
        elif loglevel =='info':
            logger.setLevel(logging.INFO)
        else :
            logger.setLevel(logging.INFO)
            logger.info(
                '[BATCtrl] Provided loglevel "%s" not valid. Defaulting to loglevel "info"',
                loglevel
            )

        if 'max_logfile_size' in config.keys():
            if type(config['max_logfile_size']) == int:
                pass
            else:
                raise RuntimeError(
                    f"Config Entry in general: max_logfile_size {config['max_logfile_size']}" +
                    " not valid. Only integer values allowed"
                )
        #default to unlimited filesize
        else :
            config['max_logfile_size']=-1

        self.config = config

    def reset_forecast_error(self):
        self.time_at_forecast_error=-1

    def handle_forecast_error(self):
        now=time.time()

        #set time_at_forecast_error if it is at the default value of -1
        if self.time_at_forecast_error == -1:
            self.time_at_forecast_error=now

        # get time delta since error
        time_passed= now-self.time_at_forecast_error

        if time_passed < ERROR_IGNORE_TIME :
            #keep current mode
            logger.info("[BatCtrl] An API Error occured %0.fs ago. "
                        "Keeping inverter mode unchanged.", time_passed)
        else:
            #set default mode
            logger.warning(
                  "[BatCtrl] An API Error occured %0.fs ago. "
                  "Setting inverter to default mode (Allow Discharging)",
                           time_passed)
            self.inverter.set_mode_allow_discharge()

    def run(self):
        # Reset some values
        self.reset_run_data()
        # for API
        self.refresh_static_values()
        self.set_discharge_limit ( self.get_max_capacity() * self.always_allow_discharge_limit )
        self.last_run_time=time.time()

        #prune log file if file is too large
        if self.config['max_logfile_size'] > 0:
            self.logfilelimiter.run()

        #get forecasts
        try:
            price_dict = self.dynamic_tariff.get_prices()
            production_forecast = self.fc_solar.get_forecast()
            # harmonize forecast horizon
            fc_period = min(max(price_dict.keys()), max(production_forecast.keys()))
            consumption_forecast = self.fc_consumption.get_forecast(fc_period+1)
        except Exception as e:
            logger.warning(
                '[BatCtrl] Following Exception occurred when trying to get forecasts: \n\t%s',
                exc_info=e
            )
            self.handle_forecast_error()
            return

        self.reset_forecast_error()

        #initialize arrays
        net_consumption = np.zeros(fc_period+1)
        production = np.zeros(fc_period+1)
        consumption = np.zeros(fc_period+1)
        prices = np.zeros(fc_period+1)

        for h in range(fc_period+1):
            production[h] = production_forecast[h]
            consumption[h] = consumption_forecast[h]
            prices[h] = price_dict[h]

        net_consumption = consumption-production
        logger.debug('[BatCTRL] Production FCST: %s', np.ndarray.round(production,1))
        logger.debug('[BatCTRL] Consumption FCST: %s', np.ndarray.round(consumption,1))
        logger.debug('[BatCTRL] Net Consumption FCST: %s', np.ndarray.round(net_consumption,1))
        logger.debug('[BatCTRL] Prices: %s', np.ndarray.round(prices,3))
        # negative = charging or feed in
        # positive = dis-charging or grid consumption

        # Store data for API
        self.save_run_data(production, consumption, net_consumption, prices)

        # stop here if api_overwrite is set and reset it
        if self.api_overwrite:
            logger.debug(
                '[BatCTRL] API Overwrite active. Skipping control logic. '
                'Next evaluation in %.0f seconds',
                TIME_BETWEEN_EVALUATIONS
            )
            self.api_overwrite = False
            return

        # correction for time that has already passed since the start of the current hour
        net_consumption[0] *= 1 - \
            datetime.datetime.now().astimezone(self.timezone).minute/60

        self.set_wr_parameters(net_consumption, price_dict)

        # %%
    def set_wr_parameters(self, net_consumption: np.ndarray, prices: dict):
        # ensure availability of data
        max_hour = min(len(net_consumption), len(prices))

        # current price as reference
        current_price = prices[0]
        mode = ""
        value = 0

        if self.is_discharge_allowed(net_consumption, prices):
            self.allow_discharging()
        else:  # discharge not allowed
            charging_limit = self.max_charging_from_grid_limit
            required_recharge_energy = self.get_required_required_recharge_energy(
                                                net_consumption[:max_hour],
                                                prices
                                            )
            is_charging_possible = self.get_SOC() < (self.get_max_capacity() * charging_limit)

            logger.debug('[BatCTRL] Discharging is NOT allowed')
            logger.debug(f'[BatCTRL] Charging allowed: {is_charging_possible}')
            logger.debug(
                f'[BatCTRL] Additional Energy required: {required_recharge_energy:0.1f} Wh')
            # charge if battery capacity available and more stored energy is required
            if is_charging_possible and required_recharge_energy > 0:
                remaining_time = (
                    60-datetime.datetime.now().astimezone(self.timezone).minute)/60
                charge_rate = required_recharge_energy/remaining_time
                self.force_charge(charge_rate)

            else:  # keep current charge level. recharge if solar surplus available
                self.avoid_discharging()
        return

    # %%
    def get_required_required_recharge_energy(self, net_consumption: list, prices: dict):
        current_price = prices[0]
        max_hour = len(net_consumption)
        consumption = np.array(net_consumption)
        consumption[consumption < 0] = 0

        production = -np.array(net_consumption)
        production[production < 0] = 0
        min_price_difference = self.min_price_difference

        # evaluation period until price is first time lower then current price
        for h in range(1, max_hour):
            future_price = prices[h]
            if future_price <= current_price:
                max_hour = h
                break

        # get high price hours
        high_price_hours = []
        for h in range(max_hour):
            future_price = prices[h]
            if future_price > current_price+min_price_difference:
                high_price_hours.append(h)

        # start with latest hour
        high_price_hours.sort()
        high_price_hours.reverse()
        required_energy = 0
        for high_price_hour in high_price_hours:
            energy_to_shift = consumption[high_price_hour]

            # correct energy to shift with potential production
            # start with latest hour
            for hour in list(range(high_price_hour))[::-1]:
                if production[hour] == 0:
                    continue
                if production[hour] >= energy_to_shift:
                    production[hour] -= energy_to_shift
                    energy_to_shift = 0
                else:
                    energy_to_shift -= production[hour]
                    production[hour]
            # add_remaining energy to shift to recharge amount
            required_energy += energy_to_shift

        recharge_energy =  required_energy-self.get_stored_energy()
        free_capacity = self.get_free_capacity()

        if recharge_energy > free_capacity:
            recharge_energy=free_capacity
        if recharge_energy <0:
            recharge_energy =0

        return recharge_energy

# %%

    def is_discharge_allowed(self, net_consumption: np.ndarray, prices: dict):
        # always allow discharging when battery is >90% maxsoc
        discharge_limit = self.get_max_capacity() * self.always_allow_discharge_limit
        stored_energy = self.get_stored_energy()
        if stored_energy > discharge_limit:
            logger.debug(
                f'[BatCTRL] Battery with {stored_energy} above discharge limit {discharge_limit}')
            return True

        current_price = prices[0]
        min_price_difference = self.min_price_difference
        max_hour = len(net_consumption)
        # relevant time range : until next recharge possibility
        for h in range(1, max_hour):
            future_price = prices[h]
            if future_price <= current_price-min_price_difference:
                max_hour = h
                break
        dt = datetime.timedelta(hours=max_hour-1)
        t0 = datetime.datetime.now()
        t1 = t0+dt
        last_hour = t1.astimezone(self.timezone).strftime("%H:59")
        logger.debug(
            f'[BatCTRL] Evaluating next {max_hour} hours until {last_hour}')
        # distribute remaining energy
        consumption = np.array(net_consumption)
        consumption[consumption < 0] = 0

        production = -np.array(net_consumption)
        production[production < 0] = 0

        # get hours with higher price
        higher_price_hours = []
        for h in range(max_hour):
            future_price = prices[h]
            # !!! different formula compared to detect relevant hours
            if future_price > current_price:
                higher_price_hours.append(h)

        higher_price_hours.sort()
        higher_price_hours.reverse()

        reserved_storage = 0
        for higher_price_hour in higher_price_hours:
            if consumption[higher_price_hour] == 0:
                continue
            required_energy = consumption[higher_price_hour]

            # correct reserved_storage with potential production
            # start with latest hour
            for hour in list(range(higher_price_hour))[::-1]:
                if production[hour] == 0:
                    continue
                if production[hour] >= required_energy:
                    production[hour] -= required_energy
                    required_energy = 0
                    break
                else:
                    required_energy -= production[hour]
                    production[hour] = 0
            # add_remaining required_energy to reserved_storage
            reserved_storage += required_energy

        logger.debug(
            "[BatCTRL] Reserved Energy: %0.1f Wh. Available in Battery: %0.1f Wh",
            reserved_storage,
            stored_energy
        )

        # for API
        self.set_reserved_energy(reserved_storage)
        self.set_stored_energy(stored_energy)

        if (stored_energy > reserved_storage):
            # allow discharging
            return True
        else:
            # forbid discharging
            return False

    def _set_charge_rate(self, charge_rate:int):
        self.last_charge_rate = charge_rate
        if self.mqtt_api is not None:
            self.mqtt_api.publish_charge_rate(charge_rate)

    def _set_mode(self, mode):
        self.last_mode = mode
        if self.mqtt_api is not None:
            self.mqtt_api.publish_mode(mode)
        # leaving force charge mode, reset charge rate
        if self.last_charge_rate > 0 and mode != MODE_FORCE_CHARGING:
            self._set_charge_rate(0)

    def allow_discharging(self):
        logger.debug(f'[BatCTRL] Mode: Allow Discharging')
        self.inverter.set_mode_allow_discharge()
        self._set_mode(MODE_ALLOW_DISCHARGING)
        return

    def avoid_discharging(self):
        logger.debug(f'[BatCTRL] Mode: Avoid Discharging')
        self.inverter.set_mode_avoid_discharge()
        self._set_mode(MODE_AVOID_DISCHARGING)
        return

    def force_charge(self, charge_rate=500):
        charge_rate = int(min(charge_rate, self.inverter.max_grid_charge_rate))
        logger.debug(f'[BatCTRL] Mode: grid charging. Charge rate : {charge_rate} W')
        self.inverter.set_mode_force_charge(charge_rate)
        self._set_mode(MODE_FORCE_CHARGING)
        self._set_charge_rate(charge_rate)
        return

    def save_run_data(self, production, consumption, net_consumption, prices):
        self.last_production = production
        self.last_consumption = consumption
        self.last_net_consumption = net_consumption
        self.last_prices = prices
        if self.mqtt_api is not None:
            self.mqtt_api.publish_production(production, self.last_run_time)
            self.mqtt_api.publish_consumption(consumption, self.last_run_time)
            self.mqtt_api.publish_net_consumption(net_consumption, self.last_run_time)
            self.mqtt_api.publish_prices(prices, self.last_run_time)
        return

    def reset_run_data(self):
        self.fetched_soc = False
        self.fetched_max_capacity = False
        self.fetched_stored_energy = False
        self.fetched_reserved_energy = False

    def get_SOC(self):
        if not self.fetched_soc:
            self.last_SOC = self.inverter.get_SOC()
            #self.last_SOC = self.get_stored_energy() / self.get_max_capacity() * 100
            self.fetched_soc = True
        return self.last_SOC

    def get_max_capacity(self):
        if not self.fetched_max_capacity:
            self.last_max_capacity = self.inverter.get_max_capacity()
            self.fetched_max_capacity = True
            if self.mqtt_api is not None:
                self.mqtt_api.publish_max_energy_capacity(self.last_max_capacity)
        return self.last_max_capacity

    def get_stored_energy(self):
        if not self.fetched_stored_energy:
            self.last_stored_energy = self.inverter.get_stored_energy()
            self.fetched_stored_energy = True
        return self.last_stored_energy

    def get_free_capacity(self):
        self.last_free_capacity = self.inverter.get_free_capacity()
        return self.last_free_capacity

    def set_reserved_energy(self, reserved_energy):
        self.last_reserved_energy = reserved_energy
        if self.mqtt_api is not None:
            self.mqtt_api.publish_reserved_energy_capacity(reserved_energy)
        return

    def get_reserved_energy(self):
        return self.last_reserved_energy

    def set_stored_energy(self, stored_energy):
        self.last_stored_energy = stored_energy
        if self.mqtt_api is not None:
            self.mqtt_api.publish_stored_energy_capacity(stored_energy)
        return

    def set_discharge_limit(self, discharge_limit):
        self.discharge_limit = discharge_limit
        if self.mqtt_api is not None:
            self.mqtt_api.publish_always_allow_discharge_limit_capacity(discharge_limit)
        return

    def refresh_static_values(self):
        if self.mqtt_api is not None:
            self.mqtt_api.publish_SOC(self.get_SOC())
            self.mqtt_api.publish_stored_energy_capacity(self.get_stored_energy())
            #
            self.mqtt_api.publish_always_allow_discharge_limit(self.always_allow_discharge_limit)
            self.mqtt_api.publish_max_charging_from_grid_limit(self.max_charging_from_grid_limit)
            #
            self.mqtt_api.publish_min_price_difference(self.min_price_difference)
            #
            self.mqtt_api.publish_evaluation_intervall(TIME_BETWEEN_EVALUATIONS)
            self.mqtt_api.publish_last_evaluation_time(self.last_run_time)
            # Trigger Inverter
            self.inverter.refresh_api_values()

    def api_set_mode(self, mode:int):
        # Check if mode is valid
        if mode not in [MODE_FORCE_CHARGING, MODE_AVOID_DISCHARGING, MODE_ALLOW_DISCHARGING]:
            logger.warning(f'[BatCtrl] API: Invalid mode {mode}')
            return

        logger.info(f'[BatCtrl] API: Setting mode to {mode}')
        self.api_overwrite = True

        if mode != self.last_mode:
            if mode == MODE_FORCE_CHARGING:
                self.force_charge()
            elif mode == MODE_AVOID_DISCHARGING:
                self.avoid_discharging()
            elif mode == MODE_ALLOW_DISCHARGING:
                self.allow_discharging()
        return

    def api_set_charge_rate(self, charge_rate:int):
        if charge_rate < 0:
            logger.warning(f'[BatCtrl] API: Invalid charge rate {charge_rate}')
            return
        logger.info(f'[BatCtrl] API: Setting charge rate to {charge_rate}')
        self.api_overwrite = True
        if charge_rate != self.last_charge_rate:
            self.force_charge(charge_rate)

        return

    def api_set_always_allow_discharge_limit(self, limit:float):
        if limit < 0 or limit > 1:
            logger.warning(f'[BatCtrl] API: Invalid always allow discharge limit {limit}')
            return
        logger.info(f'[BatCtrl] API: Setting always allow discharge limit to {limit}')
        self.always_allow_discharge_limit = limit
        return

    def api_set_max_charging_from_grid_limit(self, limit:float):
        if limit < 0 or limit > 1:
            logger.warning(f'[BatCtrl] API: Invalid max charging from grid limit {limit}')
            return
        logger.info(f'[BatCtrl] API: Setting max charging from grid limit to {limit}')
        self.max_charging_from_grid_limit = limit
        return

    def api_set_min_price_difference(self, min_price_difference:float):
        if min_price_difference < 0:
            logger.warning(f'[BatCtrl] API: Invalid min price difference {min_price_difference}')
            return
        logger.info(f'[BatCtrl] API: Setting min price difference to {min_price_difference}')
        self.min_price_difference = min_price_difference
        return


if __name__ == '__main__':
    bc = Batcontrol(CONFIGFILE)
    try:
        while (1):
            bc.run()
            time.sleep(TIME_BETWEEN_EVALUATIONS)
    finally:
        del bc
