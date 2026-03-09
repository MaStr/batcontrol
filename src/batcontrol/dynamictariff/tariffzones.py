"""Tariff_zones provider

Simple dynamic tariff provider that assigns a fixed price to each hour of the day
using up to three configurable zones.

Config options (in utility config for provider):
- type: tariff_zones
- tariff_zone_1: price for zone 1 hours (float, Euro/kWh incl. VAT/fees, required)
- zone_1_hours: comma-separated list of hours assigned to zone 1, e.g. "7,8,9,10"
- tariff_zone_2: price for zone 2 hours (float, Euro/kWh incl. VAT/fees, required)
- zone_2_hours: comma-separated list of hours assigned to zone 2, e.g. "0,1,2,3,4,5,6"
- tariff_zone_3: price for zone 3 hours (float, optional)
- zone_3_hours: comma-separated list of hours assigned to zone 3 (optional)

Rules:
- Every hour 0-23 must appear in exactly one zone (ValueError if any hour is missing).
- No hour may appear more than once across all zones (ValueError on duplicate).
- zone_3_hours and tariff_zone_3 must both be set or both omitted.

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
    """Multi-zone tariff with up to 3 zones; each zone owns a set of hours."""

    def __init__(
            self,
            timezone,
            min_time_between_API_calls=0,
            delay_evaluation_by_seconds=0,
            target_resolution: int = 60,
            tariff_zone_1: float = None,
            zone_1_hours=None,
            tariff_zone_2: float = None,
            zone_2_hours=None,
            tariff_zone_3: float = None,
            zone_3_hours=None,
    ):
        super().__init__(
            timezone,
            min_time_between_API_calls,
            delay_evaluation_by_seconds,
            target_resolution=target_resolution,
            native_resolution=60,
        )

        self._tariff_zone_1 = None
        self._tariff_zone_2 = None
        self._tariff_zone_3 = None
        self._zone_1_hours = None
        self._zone_2_hours = None
        self._zone_3_hours = None

        if tariff_zone_1 is not None:
            self.tariff_zone_1 = tariff_zone_1
        if zone_1_hours is not None:
            self.zone_1_hours = zone_1_hours
        if tariff_zone_2 is not None:
            self.tariff_zone_2 = tariff_zone_2
        if zone_2_hours is not None:
            self.zone_2_hours = zone_2_hours
        if tariff_zone_3 is not None:
            self.tariff_zone_3 = tariff_zone_3
        if zone_3_hours is not None:
            self.zone_3_hours = zone_3_hours

    def get_raw_data_from_provider(self) -> dict:
        """No external API — configuration is static."""
        return {}

    def _validate_configuration(self) -> None:
        """Raise RuntimeError/ValueError if the zone configuration is incomplete or invalid."""
        if self._tariff_zone_1 is None:
            raise RuntimeError('[TariffZones] tariff_zone_1 must be set')
        if self._zone_1_hours is None:
            raise RuntimeError('[TariffZones] zone_1_hours must be set')
        if self._tariff_zone_2 is None:
            raise RuntimeError('[TariffZones] tariff_zone_2 must be set')
        if self._zone_2_hours is None:
            raise RuntimeError('[TariffZones] zone_2_hours must be set')

        zone3_hours_set = self._zone_3_hours is not None
        zone3_price_set = self._tariff_zone_3 is not None
        if zone3_hours_set != zone3_price_set:
            raise RuntimeError(
                '[TariffZones] zone_3_hours and tariff_zone_3 must both be set or both omitted'
            )

        # Check for duplicate hours across all zones
        seen = {}
        for zone_name, hours in [
            ('zone_1_hours', self._zone_1_hours),
            ('zone_2_hours', self._zone_2_hours),
            ('zone_3_hours', self._zone_3_hours),
        ]:
            if hours is None:
                continue
            for h in hours:
                if h in seen:
                    raise ValueError(
                        f'Hour {h} is defined in both {seen[h]} and {zone_name}'
                    )
                seen[h] = zone_name

        # Check all 24 hours are covered
        missing = sorted(set(range(24)) - set(seen))
        if missing:
            raise ValueError(
                f'Hours {missing} are not assigned to any zone; '
                'all 24 hours (0-23) must be covered'
            )

    def _get_prices_native(self) -> dict[int, float]:
        """Build hourly prices for the next 48 hours, hour-aligned.

        Returns a dict mapping interval index (0 = start of current hour)
        to price (float).
        """
        self._validate_configuration()

        hour_to_price = {}
        for hours, price in [
            (self._zone_1_hours, self._tariff_zone_1),
            (self._zone_2_hours, self._tariff_zone_2),
            (self._zone_3_hours, self._tariff_zone_3),
        ]:
            if hours is not None:
                for h in hours:
                    hour_to_price[h] = price

        now = datetime.datetime.now().astimezone(self.timezone)
        current_hour_start = now.replace(minute=0, second=0, microsecond=0)

        prices = {}
        for rel_hour in range(48):
            ts = current_hour_start + datetime.timedelta(hours=rel_hour)
            prices[rel_hour] = hour_to_price[ts.hour]

        logger.debug('tariffZones: Generated %d hourly prices', len(prices))
        return prices

    @staticmethod
    def _parse_hours(value, name: str) -> list:
        """Parse hour specifications into a validated list of hours.

        Accepted formats (may be mixed):
        - Single integer:           5
        - Comma-separated values:   "0,1,2,3"
        - Inclusive ranges:         "0-5"  →  [0, 1, 2, 3, 4, 5]
        - Mixed:                    "0-5,6,7"  →  [0, 1, 2, 3, 4, 5, 6, 7]
        - Python list/tuple of ints or range-strings: [0, '1-3', 4]

        Raises ValueError if any hour is out of range [0, 23], if a range is
        invalid (start > end), or if an hour appears more than once within the
        same zone.
        """
        def expand_token(token: str) -> list:
            """Expand a single string token (range or integer) to a list of ints."""
            if '-' in token:
                parts = token.split('-', 1)
                try:
                    start, end = int(parts[0].strip()), int(parts[1].strip())
                except (ValueError, TypeError) as exc:
                    raise ValueError(
                        f'[{name}] invalid range: {token!r}'
                    ) from exc
                if start > end:
                    raise ValueError(
                        f'[{name}] range start must be <= end, got {token!r}'
                    )
                return list(range(start, end + 1))
            try:
                return [int(token)]
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    f'[{name}] invalid hour value: {token!r}'
                ) from exc

        if isinstance(value, int):
            raw_ints = [value]
            tokens = []
        elif isinstance(value, str):
            raw_ints = []
            tokens = [p.strip() for p in value.split(',') if p.strip()]
        elif isinstance(value, (list, tuple)):
            # split into direct integers (no range parsing) and string tokens
            raw_ints = [p for p in value if isinstance(p, int)]
            tokens = [str(p).strip() for p in value
                      if not isinstance(p, int) and str(p).strip()]
        else:
            raise ValueError(
                f'[{name}] must be a comma-separated string, list, or integer'
            )

        hours = []
        for h in raw_ints:
            if h < 0 or h > 23:
                raise ValueError(f'[{name}] hour {h} is out of range [0, 23]')
            if h in hours:
                raise ValueError(f'[{name}] hour {h} appears more than once')
            hours.append(h)

        for token in tokens:
            for h in expand_token(token):
                if h < 0 or h > 23:
                    raise ValueError(f'[{name}] hour {h} is out of range [0, 23]')
                if h in hours:
                    raise ValueError(f'[{name}] hour {h} appears more than once')
                hours.append(h)

        return hours

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
    def tariff_zone_3(self) -> float:
        return self._tariff_zone_3

    @tariff_zone_3.setter
    def tariff_zone_3(self, value: float) -> None:
        self._tariff_zone_3 = self._validate_price(value, 'tariff_zone_3')

    @property
    def zone_1_hours(self) -> list:
        return self._zone_1_hours

    @zone_1_hours.setter
    def zone_1_hours(self, value) -> None:
        self._zone_1_hours = self._parse_hours(value, 'zone_1_hours')

    @property
    def zone_2_hours(self) -> list:
        return self._zone_2_hours

    @zone_2_hours.setter
    def zone_2_hours(self, value) -> None:
        self._zone_2_hours = self._parse_hours(value, 'zone_2_hours')

    @property
    def zone_3_hours(self) -> list:
        return self._zone_3_hours

    @zone_3_hours.setter
    def zone_3_hours(self, value) -> None:
        self._zone_3_hours = self._parse_hours(value, 'zone_3_hours')
