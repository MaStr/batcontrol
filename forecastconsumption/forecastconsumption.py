#%%
import pandas as pd
import numpy as np
import datetime
import math
import logging
import pytz

logger = logging.getLogger("__main__")
logger.info(f'[FCConsumption] loading module ')

class ForecastConsumption(object):
    """Forecasts Consumption based on load profiles

        Loadprofile:
        csv file containing, month(1..12), weekday(0..6, 0-Monday), hour(0..24) and Energy in Wh

        Can create load profiles:
        required input: csv file containing
            - 'timestamp' Timestamp in ISO format
            - 'energy' consumption in one our periods measured in Ws - Yes, Wattseconds!

        forecasting will be done by considering month, weekday and hour
    """

    def __init__(self, loadprofile, timezone, annual_consumption=0 , datafile=None, ) -> None:
        self.path_to_load_profile=loadprofile
        if datafile:
            self.create_loadprofile(datafile,self.path_to_load_profile)
        self.load_loadprofile()
        if annual_consumption >0:
            self.scaling_factor = self.calculate_scaling_factor(annual_consumption)
            logger.info(f"[FC Cons] the hourly values from the load profile are scaled with a factor of {self.scaling_factor:0.2f} to match the annual consumption of {annual_consumption} kWh")
        else:
            self.scaling_factor=1
            annual_consumption_load_profile= self.dataframe['energy'].sum()*8760/2016/1000
            logger.info(f"[FC Cons] The annual consumption of the applied load profile is {annual_consumption_load_profile:0.1f} kWh ")
            logger.info(f"[FC Cons] You can specify your estimated annual consumption in the config file under consumption_forecast:  annual_consumption ")
        self.timezone=timezone

    def calculate_scaling_factor(self, annual_consumption):
        annual_consumption_load_profile= self.dataframe['energy'].sum()*8760/2016/1000
        logger.info(f"[FC Cons] The annual consumption of the applied load profile is {annual_consumption_load_profile} kWh ")
        scaling_factor = annual_consumption/annual_consumption_load_profile
        return scaling_factor

    def load_data_file(self, datafile):
        df = pd.read_csv(datafile)
        df['timestamp'] = df['timestamp'].map(
            lambda timestamp: pd.to_datetime(timestamp).astimezone(self.timezone))
        df['month'] = df['timestamp'].map(lambda timestamp: timestamp.month)
        df['weekday'] = df['timestamp'].map(
            lambda timestamp: timestamp.dayofweek)
        df['hour'] = df['timestamp'].map(lambda timestamp: timestamp.hour)
        # convert Ws to Wh and adjust sign
        df['energy'] = df['energy']/3600*-1
        return df

    def get_forecast(self, hours):
        t0 = datetime.datetime.now().astimezone(self.timezone)
        df = self.dataframe
        prediction = {}

        for h in range(hours):
            delta_t = datetime.timedelta(hours=h)
            t1 = t0+delta_t
            energy = df.loc[df['hour'] == t1.hour].loc[df['month'] ==
                                               t1.month].loc[df['weekday'] == t1.weekday()]['energy'].median()
            if math.isnan(energy):
                energy = df['energy'].median()
            prediction[h]=energy*self.scaling_factor
        logger.debug('[FC Cons] predicting consumption: %s', np.array(list(prediction.values())).round(1))
        return prediction

    def get_annual_value(self):
        self.dataframe['energy'].sum()*8760/2016/1000

    def create_loadprofile(self, datafile, path_to_profile='load_profile.csv'):
        df=self.load_data_file(datafile)
        a=[]
        energy=0
        for month in range(1,13):
            for day in range(7):
                for hour in range(24):
                    energy = df.loc[df['hour'] == hour].loc[df['month'] == month].loc[df['weekday'] == day]['energy'].mean()
                    a.append([month,day,hour,energy])
        df_load_profile=pd.DataFrame(a)
        df_load_profile.to_csv(path_to_profile,header=['month','weekday','hour','energy'], index=None)

    def load_loadprofile(self):
        self.dataframe=pd.read_csv(self.path_to_load_profile)
# %%
if __name__ == '__main__':
    tz=pytz.timezone('Europe/Berlin')
    fc=ForecastConsumption('../config/load_profile.csv',tz)
    print(fc.get_forecast(25))

# %%
