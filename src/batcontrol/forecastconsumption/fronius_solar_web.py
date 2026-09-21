"""
Class to create load profile from SolarWeb Exports
"""
import logging
import os

import numpy as np
import pandas as pd
import pytz

logger = logging.getLogger("__main__")
logger.info('[SolarWeb Export Processor] loading module')

class SolarWebExportProcessor:
    """
    A class to process Fronius Solar Web Export data into a batcontrol load profile.

    Source File:
    Excel file containing at a minimum the SolarWeb detailed (i.e. 5 minute resolution) export of:
        - "Energie Bilanz / Verbrauch" ergo consumption

    Additionally, the following columns can be included:
        - "Wattpilot / Energie Wattpilot" ergo consumption from Fronius Wattpilot

    If these additional columns are included then the load from these "smart" consumers will be
    subtracted from the load to get a "base load" under the assumption that these will only run
    in the cheapest hours anyway.

    The load profile will output month, weekday, hour and energy in Wh

    Any gaps in the timeseries will be filled with the weekday average across the existing dataset
    unless fill_empty_with_average is set to False.

    Key Features:
    - Loads data from a SolarWeb exported Excel file.
    - Processes Wattpilot columns to calculate wallbox load.
    - Subtracts wallbox loads to get a base load and optionally smooths the ramp ups and downs.
    - Resamples data to hourly intervals.
    - Aggregates hourly data to month, weekday, hour as needed for load profile.
    - Exports processed data to a CSV file.

    Attributes:
        file_path (str): Path to the input Excel filed
        output_path (str): Path to save the output CSV file
        timezone (str): Timezone for the data (default: 'Europe/Berlin')
        fill_empty_with_average (bool): Whether to fill missing data with averages (default: True)
        smooth_base_load (bool): smooth the wallbox ramps in  calculated base load (default: True)
        smoothing_threshold (int): Threshold to detect switched on/off EV charging (default: 1200 W)
        smoothing_window_size (int): Window size for smoothing around EV charging (default: 2)
        resample_freq (str): Frequency for resampling data (default: '60min')
        df (pd.DataFrame): The main DataFrame holding the processed data
    """

    # pylint: disable=too-many-instance-attributes, too-many-arguments
    # Nine are ok here

    def __init__(self, file_path, *, output_path='/config/generated_load_profile.csv',
                 timezone='Europe/Berlin',
                 fill_empty_with_average=True,
                 smooth_base_load=True, smoothing_threshold=1200,smoothing_window_size=2,
                 resample_freq='60min'):
        """
        Initialize the SolarWebExportProcessor.

        :param file_path: Path to the Excel file containing the data.
        :param output_path: Path to output CSV file (default: '/config/generated_load_profile.csv').
        :param timezone: Timezone for the data (default: 'Europe/Berlin').
        :param fill_empty_with_average: Whether to fill missing data with averages (default: True).
        :param smooth_base_load: Whether to smooth the base load (default: True).
        :param smoothing_threshold: Threshold to detect switched on/off EV charge (default: 1200 W).
        :param smoothing_window_size: Window size for smoothing around sudden changes (default: 2).
        :param resample_freq: Frequency for resampling data (default: '60min').
        """
        self.file_path = file_path
        self.output_path = output_path
        self.timezone = pytz.timezone(timezone)
        self.fill_empty_with_average = fill_empty_with_average
        self.smooth_base_load = smooth_base_load
        self.smoothing_threshold = smoothing_threshold
        self.smoothing_window_size = smoothing_window_size
        self.resample_freq = resample_freq
        self.df = None

    def load_data(self):
        """Load data from the Excel file and preprocess it."""
        # Check if the input file exists
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"The input file '{self.file_path}' does not exist.")

        # Read excel into pandas dataframe
        self.df = pd.read_excel(self.file_path, header=[0, 1], index_col=0, parse_dates=True,
                                date_format='%d.%m.%Y %H:%M')

        # Check if the data has at least 1-hour resolution
        time_diff = self.df.index.to_series().diff().min()
        if time_diff > pd.Timedelta(hours=1):
            raise ValueError(
                f"The data resolution is larger than 1 hour. "
                f"Minimum time difference found: {time_diff}.")

        # Convert float64 columns to float32 for file/memory size
        float64_cols = self.df.select_dtypes(include='float64').columns
        self.df[float64_cols] = self.df[float64_cols].astype('float32')

    def process_wattpilot_columns(self):
        """Process Wattpilot columns to calculate Load_Wallbox."""
        # Step 1: Identify columns containing "Energie Wattpilot"
        level_0_columns = self.df.columns.get_level_values(0)
        wattpilot_columns = level_0_columns[level_0_columns.str.contains('Energie Wattpilot')]

        # Step 2: Check if any matching columns exist
        if not wattpilot_columns.empty:
            # Create a new column "Load_Wallbox" with the row sum of these columns
            # This also replaces all NaN with 0
            self.df[('Load_Wallbox', '[Wh]')] = self.df[wattpilot_columns].sum(axis=1)
        else:
            # If no matching columns exist, create a "Load_Wallbox" column with zeros
            self.df[('Load_Wallbox', '[Wh]')] = 0

    def calculate_base_load(self):
        """Calculate base load and optionally smooth it."""

        # Check if the required column ('Verbrauch', '[Wh]') exists
        if ('Verbrauch', '[Wh]') not in self.df.columns:
            raise KeyError(
                "The required column ('Verbrauch', '[Wh]') does not exist in the input data."
            )

        # Calculate a base load by removing the wallbox loads
        self.df[('base_load', '[Wh]')] = (
                self.df['Verbrauch', '[Wh]'] - self.df['Load_Wallbox', '[Wh]']
        )

        # Smoothing of data where Wallbox starts or ends charging due to artifacts (if enabled)
        if self.smooth_base_load:
            # Step 1: Calculate the difference between consecutive values
            self.df[('WB_diff', '[Wh]')] = self.df['Load_Wallbox', '[Wh]'].diff().abs()

            # Step 2: Define a threshold for detecting sudden changes (e.g., a large jump)
            sudden_change_idx = self.df[
                self.df[('WB_diff', '[Wh]')] > self.smoothing_threshold / 12
                ].index  # We're at 5 min intervals thus / 12

            # Step 3: Create a new smoothed base load curve
            self.df[('base_load_smoothed', '[Wh]')] = self.df[('base_load', '[Wh]')]

            # Smooth only around the points with sudden changes
            # (e.g., within a window of +/- smoothing_window_size)
            for idx in sudden_change_idx:
                int_idx = self.df.index.get_loc(idx)
                # Get the window around the sudden change index (ensuring we can't go out of bounds)
                start_idx = max(1, int_idx - self.smoothing_window_size)
                end_idx = min(len(self.df) - 1, int_idx + self.smoothing_window_size)

                # Calculate averages before and after ramp
                avg_before = self.df[('base_load_smoothed', '[Wh]')].iloc[
                             start_idx - 1 : int_idx - 1
                             ].mean()
                avg_after = self.df[('base_load_smoothed', '[Wh]')].iloc[
                            int_idx + 1 : end_idx + 1
                            ].mean()

                # Use averages to replace at detected ramps
                self.df[('base_load_smoothed', '[Wh]')].iat[int_idx - 1] = avg_before  # ramp downs
                self.df[('base_load_smoothed', '[Wh]')].iat[int_idx] = avg_after  # ramp ups
        else:
            # If smoothing is disabled, use the unsmoothed base load
            self.df[('base_load_smoothed', '[Wh]')] = self.df[('base_load', '[Wh]')]

    def resample_and_add_temporal_columns(self):
        """Resample data to hourly intervals and add temporal columns."""
        # Resampling to hourly data
        def custom_agg(column):
            if column.name[1] == '[Wh]':  # Check the second level of the column header
                result = column.sum()  # Apply sum to 'Wh'
            else:
                result = np.float32(column.mean())  # Apply mean to all others & back to float32
            return result

        # Resample dataframe to hourly data
        self.df = self.df.resample(self.resample_freq).apply(custom_agg)

        # Drop column multi index
        self.df.columns = self.df.columns.droplevel(1)

        # Add month, weekday, and hour columns
        self.df['month'] = self.df.index.month
        self.df['weekday'] = self.df.index.weekday  # Monday=0, Sunday=6
        self.df['hour'] = self.df.index.hour

    def process_and_export_data(self):
        """Process data and export to CSV."""

        # Define aggregation function
        def calculate_energy(group):
            """Calculate confidence intervals for a group."""
            mean = group.mean()
            return pd.Series({
                'energy': mean,
            })

        # Group by month, weekday, and hour, and calculate the mean energy consumption
        grouped = self.df.groupby(['month', 'weekday', 'hour']
                                  )['base_load_smoothed'].apply(calculate_energy).unstack()

        # Check if the grouped result is missing rows
        expected_rows = 12 * 7 * 24  # 12 months, 7 weekdays, 24 hours
        if len(grouped) < expected_rows and self.fill_empty_with_average:
            logger.info("Data is missing rows. Filling missing values with averages...")

            # Create a complete multi-index for all combinations of month, weekday, and hour
            full_index = pd.MultiIndex.from_product(
                [range(1, 13), range(7), range(24)],  # All months, weekdays, and hours
                names=['month', 'weekday', 'hour']
            )

            # Reindex the grouped result to include all combinations
            grouped_full = grouped.reindex(full_index)

            # Calculate the average for each weekday and hour
            weekday_hour_avg = grouped_full.groupby(['weekday', 'hour']).mean()

            # Fill missing values in the grouped result with the weekday and hour average
            for (weekday, hour), avg_value in weekday_hour_avg.iterrows():
                grouped_full.loc[(slice(None), weekday, hour), :] = grouped_full.loc[
                                                                    (slice(None), weekday, hour), :
                                                                    ].fillna(avg_value)

            # Reset the index for better CSV formatting (optional)
            grouped_filled = grouped_full.reset_index()

            # Write the result to a CSV file
            grouped_filled.to_csv(self.output_path, index=False)
            logger.info("Missing values filled and saved to '%s'.", self.output_path)
        else:
            logger.info("Data is complete. No missing rows to fill.")
            # Export the original grouped data to CSV
            grouped.reset_index().to_csv(self.output_path, index=False)
            logger.info("Data saved to '%s'.", self.output_path)

    def run(self):
        """Run the entire processing pipeline."""
        try:
            self.load_data()
            self.process_wattpilot_columns()
            self.calculate_base_load()
            self.resample_and_add_temporal_columns()
            self.process_and_export_data()
        except (FileNotFoundError, KeyError, ValueError) as e:
            logger.error("An error occurred: %s", e)
        except Exception as e: #pylint: disable=broad-exception-caught
            logger.error("An unexpected error occurred: %s", e)

# Example usage
if __name__ == "__main__":
    # Initialize the processor with file path, timezone, and smoothing options
    processor = SolarWebExportProcessor(
        file_path='../config/PartialSolarWebExport.xlsx',
        output_path='../config/generated_load_profile.csv',
        timezone='Europe/Berlin',
        fill_empty_with_average=True,
        smooth_base_load=True,  # Enable smoothing
        smoothing_threshold=1200,  # Set smoothing threshold in Watts
        smoothing_window_size=2,  # Set smoothing window size
        resample_freq='60min'
    )
    processor.run()
