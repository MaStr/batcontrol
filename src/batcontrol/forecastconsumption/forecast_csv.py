# %%
import datetime
import math
import logging
import pandas as pd
import numpy as np
import os
from .baseclass import ForecastConsumptionBaseclass


logger = logging.getLogger(__name__)
logger.info('Loading module')


class ForecastConsumptionCsv(ForecastConsumptionBaseclass):
    """Forecasts Consumption based on load profiles

        Loadprofile:
        csv file containing, month(1..12), weekday(0..6, 0-Monday), hour(0..24) and Energy in Wh

        Can create load profiles:
        required input: csv file containing
            - 'timestamp' Timestamp in ISO format
            - 'energy' consumption in one our periods measured in Ws - Yes, Wattseconds!

        forecasting will be done by considering month, weekday and hour
    """

    def __init__(
            self,
            loadprofile,
            timezone,
            annual_consumption=0,
            datafile=None,
            target_resolution: int = 60) -> None:
        # Initialize baseclass with 60-minute native resolution
        super().__init__(
            timezone,
            target_resolution=target_resolution,
            native_resolution=60)
        if not os.path.isfile(loadprofile):
            raise RuntimeError(
                "[ForecastCSV] Specified Load Profile file " +
                f"'{loadprofile}' not found"
            )

        self.path_to_load_profile = loadprofile
        self.annual_consumption = annual_consumption
        if datafile:
            self.create_loadprofile(datafile, self.path_to_load_profile)
        self.load_loadprofile()
        self.scaling_factor = self._compute_scaling_factor(self.dataframe)

    def _compute_scaling_factor(self, dataframe) -> float:
        """Log and return the scaling factor for the given load profile."""
        if self.annual_consumption > 0:
            annual_consumption_load_profile = dataframe['energy'].sum(
            ) * 8760 / 2016 / 1000
            scaling_factor = self.annual_consumption / annual_consumption_load_profile
            logger.info(
                "The hourly values from the load profile are scaled with a "
                "factor of %.2f to match the annual consumption of %d kWh",
                scaling_factor,
                self.annual_consumption
            )
            return scaling_factor
        annual_consumption_load_profile = dataframe['energy'].sum(
        ) * 8760 / 2016 / 1000
        logger.info(
            "The annual consumption of the applied load profile is %.2f kWh ",
            annual_consumption_load_profile)
        logger.info(
            "You can specify your estimated annual consumption in the config file "
            "under consumption_forecast:  annual_consumption ")
        return 1

    def refresh_data(self) -> None:
        """Re-read the load profile CSV from disk.

        The new profile only replaces the active one if it can be read and
        has the required columns; otherwise the old profile stays in use.
        """
        try:
            dataframe = pd.read_csv(self.path_to_load_profile)
            missing = {'month', 'weekday', 'hour', 'energy'} - set(dataframe.columns)
            if missing:
                raise ValueError(f"missing columns {sorted(missing)}")
            if dataframe.empty:
                raise ValueError("load profile is empty")
            scaling_factor = self._compute_scaling_factor(dataframe)
        except Exception as err:  # pylint: disable=broad-exception-caught
            logger.error(
                "[ForecastCSV] Reloading load profile '%s' failed, keeping "
                "previous profile: %s", self.path_to_load_profile, err)
            return
        # Swap both values together, readers hold the same lock in get_forecast
        with self._forecast_lock:
            self.dataframe = dataframe
            self.scaling_factor = scaling_factor
        logger.info("[ForecastCSV] Reloaded load profile '%s'",
                    self.path_to_load_profile)

    def _get_forecast_native(self, hours: int) -> dict[int, float]:
        """Get hour-aligned forecast at native (60-minute) resolution.

        Args:
            hours: Number of hours to forecast

        Returns:
            Dict mapping hour index to energy value (Wh per hour)
            Index 0 = start of current hour
        """
        t0 = datetime.datetime.now().astimezone(self.timezone)
        # Align to start of current hour
        t0 = t0.replace(minute=0, second=0, microsecond=0)
        df = self.dataframe
        prediction = {}

        for h in range(hours):
            delta_t = datetime.timedelta(hours=h)
            t1 = t0 + delta_t
            energy = df.loc[df['hour'] == t1.hour].loc[df['month'] ==
                                                       t1.month].loc[df['weekday'] == t1.weekday()]['energy'].median()
            if math.isnan(energy):
                energy = df['energy'].median()
            prediction[h] = energy * self.scaling_factor

        logger.debug(
            'Predicting consumption (hour-aligned): %s',
            np.array(list(prediction.values())).round(1)
        )
        return prediction

    def load_data_file(self, datafile):
        df = pd.read_csv(datafile)
        df['timestamp'] = df['timestamp'].map(
            lambda timestamp: pd.to_datetime(timestamp).astimezone(
                self.timezone))
        df['month'] = df['timestamp'].map(lambda timestamp: timestamp.month)
        df['weekday'] = df['timestamp'].map(
            lambda timestamp: timestamp.dayofweek)
        df['hour'] = df['timestamp'].map(lambda timestamp: timestamp.hour)
        # convert Ws to Wh and adjust sign
        df['energy'] = df['energy'] / 3600 * -1
        return df

    def create_loadprofile(self, datafile, path_to_profile='load_profile.csv'):
        df = self.load_data_file(datafile)
        a = []
        energy = 0
        for month in range(1, 13):
            for day in range(7):
                for hour in range(24):
                    energy = df.loc[df['hour'] == hour].loc[df['month'] ==
                                                            month].loc[df['weekday'] == day]['energy'].mean()  # pylint: disable=c0301
                    a.append([month, day, hour, energy])
        df_load_profile = pd.DataFrame(a)
        df_load_profile.to_csv(
            path_to_profile, header=['month', 'weekday', 'hour', 'energy'],
            index=None
        )

    def load_loadprofile(self):
        self.dataframe = pd.read_csv(self.path_to_load_profile)
