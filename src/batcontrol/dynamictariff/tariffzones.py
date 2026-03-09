"""Tariff_zones provider

Simple dynamic tariff provider that returns a repeating two zone tariff.
Config options (in utility config for provider):
- type: tariff_zones
- tariff_zone_1: price for zone 1 hours (float, Euro/kWh incl. VAT/fees, required)
- tariff_zone_2: price for zone 2 hours (float, Euro/kWh incl. VAT/fees, required)
- zone_1_start: hour when tariff zone 1 starts (int, default 7)
- zone_1_end: hour when tariff zone 1 ends (int, default 22)

Wrap-around is supported: setting zone_1_start=22 and zone_1_end=6 means zone 1
covers hours 22-23 and 0-5. When zone_1_start == zone_1_end, zone 1 covers 0 hours.

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
            tariff_zone_1: float = None,
            tariff_zone_2: float = None,
            zone_1_start: int = 7,
            zone_1_end: int = 22,
    ):
        super().__init__(
            timezone,
            min_time_between_API_calls,
            delay_evaluation_by_seconds,
            target_resolution=target_resolution,
            native_resolution=60,
        )

        self._zone_1_start = self._validate_hour(zone_1_start, 'zone_1_start')
        self._zone_1_end = self._validate_hour(zone_1_end, 'zone_1_end')
        self._tariff_zone_1 = None
        self._tariff_zone_2 = None
        if tariff_zone_1 is not None:
            self.tariff_zone_1 = tariff_zone_1
        if tariff_zone_2 is not None:
            self.tariff_zone_2 = tariff_zone_2

    def get_raw_data_from_provider(self) -> dict:
        """No external API — configuration is static."""
        return {}

    def _get_prices_native(self) -> dict[int, float]:
        """Build hourly prices for the next 48 hours, hour-aligned.

        Returns a dict mapping interval index (0 = start of current hour)
        to price (float).
        """
        if self._tariff_zone_1 is None or self._tariff_zone_2 is None:
            raise RuntimeError(
                '[TariffZones] tariff_zone_1 and tariff_zone_2 must be set '
                'before generating prices'
            )

        zone_1_start = self._zone_1_start
        zone_1_end = self._zone_1_end

        if zone_1_start == zone_1_end:
            logger.warning(
                'tariffZones: zone_1_start == zone_1_end (%d): zone 1 covers 0 hours',
                zone_1_start
            )

        now = datetime.datetime.now().astimezone(self.timezone)
        current_hour_start = now.replace(minute=0, second=0, microsecond=0)

        prices = {}
        for rel_hour in range(48):
            ts = current_hour_start + datetime.timedelta(hours=rel_hour)
            h = ts.hour
            if zone_1_start < zone_1_end:
                is_zone_1 = zone_1_start <= h < zone_1_end
            elif zone_1_start > zone_1_end:
                # wrap-around (e.g., zone_1_start=22, zone_1_end=6)
                is_zone_1 = h >= zone_1_start or h < zone_1_end
            else:
                # zone_1_start == zone_1_end: no zone 1 hours
                is_zone_1 = False

            prices[rel_hour] = self._tariff_zone_1 if is_zone_1 else self._tariff_zone_2

        logger.debug('tariffZones: Generated %d hourly prices', len(prices))
        return prices

    @staticmethod
    def _validate_hour(val, name: str) -> int:
        try:
            ival = int(val)
        except (ValueError, TypeError) as exc:
            raise ValueError(f'[{name}] must be an integer between 0 and 23') from exc
        if ival < 0 or ival > 23:
            raise ValueError(f'[{name}] must be between 0 and 23 (got {ival})')
        return ival

    @staticmethod
    def _validate_price(val, name: str) -> float:
        try:
            fval = float(val)
        except (ValueError, TypeError) as exc:
            raise ValueError(f'[{name}] must be a positive number') from exc
        if fval <= 0:
            raise ValueError(f'[{name}] must be positive (got {fval})')
        return fval

    @property
    def tariff_zone_1(self) -> float:
        return self._tariff_zone_1

    @tariff_zone_1.setter
    def tariff_zone_1(self, value: float) -> None:
        self._tariff_zone_1 = self._validate_price(value, 'tariff_zone_1')

    @property
    def tariff_zone_2(self) -> float:
        return self._tariff_zone_2

    @tariff_zone_2.setter
    def tariff_zone_2(self, value: float) -> None:
        self._tariff_zone_2 = self._validate_price(value, 'tariff_zone_2')

    @property
    def zone_1_start(self) -> int:
        return self._zone_1_start

    @zone_1_start.setter
    def zone_1_start(self, value: int) -> None:
        self._zone_1_start = self._validate_hour(value, 'zone_1_start')

    @property
    def zone_1_end(self) -> int:
        return self._zone_1_end

    @zone_1_end.setter
    def zone_1_end(self, value: int) -> None:
        self._zone_1_end = self._validate_hour(value, 'zone_1_end')
