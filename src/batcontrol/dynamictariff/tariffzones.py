"""Tariff_zones provider

Simple dynamic tariff provider that returns a repeating two zone tariff.
Config options (in utility config for provider):
- type: tariff_zones
- tariff_zone_1: price for zone 1 hours (float)
- tariff_zone_2: price for zone 2 hours (float)
- zone_1_start: hour when tariff zone 1 starts (int, default 7)
- zone_1_end: hour when tariff zone 1 ends (int, default 22)

The class produces hourly prices (native_resolution=60) for the next 48
hours aligned to the current hour. The baseclass will handle conversion to
15min if the target resolution is 15.

Note:
The charge rate is not evenly distributed across the low price hours.
If you prefer a more even distribution during the low price hours, you can adjust the
soften_price_difference_on_charging to enabled
and
max_grid_charge_rate to a low value, e.g. capacity of the battery divided
by the hours of low price periods.

If you prefer a late charging start (=optimize efficiency, have battery only short
time at high SOC), you can adjust the
soften_price_difference_on_charging to disabled
"""
import datetime
import logging
from .baseclass import DynamicTariffBaseclass

logger = logging.getLogger(__name__)


class TariffZones(DynamicTariffBaseclass):
    """Two-tier tariff: zone 1 / zone 2 fixed prices."""

    def __init__(
            self,
            timezone,
            min_time_between_API_calls=0,
            delay_evaluation_by_seconds=0,
            target_resolution: int = 60,
    ):
        super().__init__(
            timezone,
            min_time_between_API_calls,
            delay_evaluation_by_seconds,
            target_resolution=target_resolution,
            native_resolution=60,
        )



    def _get_prices_native(self) -> dict[int, float]:
        """Build hourly prices for the next 48 hours, hour-aligned.

        Returns a dict mapping interval index (0 = start of current hour)
        to price (float).
        """
        raw = self.get_raw_data()
        # allow values from raw data (cache) if present
        tariff_zone_1 = raw.get('tariff_zone_1', self.tariff_zone_1)
        tariff_zone_2 = raw.get('tariff_zone_2', self.tariff_zone_2)
        zone_1_start = int(raw.get('zone_1_start', self.zone_1_start))
        zone_1_end = int(raw.get('zone_1_end', self.zone_1_end))

        now = datetime.datetime.now().astimezone(self.timezone)
        # Align to start of current hour
        current_hour_start = now.replace(minute=0, second=0, microsecond=0)

        prices = {}
        # produce next 48 hours
        for rel_hour in range(0, 48):
            ts = current_hour_start + datetime.timedelta(hours=rel_hour)
            h = ts.hour
            if zone_1_start <= zone_1_end:
                is_day = (h >= zone_1_start and h < zone_1_end)
            else:
                # wrap-around (e.g., zone_1_start=20, zone_1_end=6)
                is_day = not (h >= zone_1_end and h < zone_1_start)

            prices[rel_hour] = tariff_zone_1 if is_day else tariff_zone_2

        logger.debug('tariffZones: Generated %d hourly prices', len(prices))
        return prices
