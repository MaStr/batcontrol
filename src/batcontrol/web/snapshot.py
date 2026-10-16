""" Immutable per-cycle snapshot of the forecast arrays

The control loop writes its forecast arrays while the web thread reads them.
Instead of locking every read, the loop publishes one frozen snapshot per
control cycle and the reader picks it up with a single attribute read, which
is atomic. A request therefore either sees the previous cycle completely or
the new one completely - never prices from one cycle next to production from
the other.

Numpy arrays are converted to tuples of floats on publish: that copies the
data once, at a point where the loop owns it anyway, and leaves the reader
with an object nothing can mutate underneath it.
"""
import dataclasses
import logging
from typing import Optional, Sequence, Tuple

from ..interval_utils import align_to_interval, iter_slots

logger = logging.getLogger(__name__)

# Rounding per quantity. Prices need more digits than energy: 4 decimals keep
# sub-cent tariff differences, which are exactly what batcontrol decides on.
PRICE_DIGITS = 4
ENERGY_DIGITS = 1
POWER_DIGITS = 0


def _as_tuple(values) -> Tuple[float, ...]:
    """ Convert a numpy array, list or None into a tuple of floats. """
    if values is None:
        return ()
    return tuple(float(value) for value in values)


@dataclasses.dataclass(frozen=True)
class ForecastSnapshot:
    """ The forecast state of one completed control cycle.

    run_time is in epoch seconds, as used inside batcontrol. The API converts
    to epoch milliseconds at the edge.
    """
    run_time: float
    interval_minutes: int
    prices: Tuple[float, ...] = ()
    consumption: Tuple[float, ...] = ()
    production: Tuple[float, ...] = ()
    net_consumption: Tuple[float, ...] = ()

    @classmethod
    def build(cls, *, run_time: float,  # pylint: disable=too-many-arguments
              interval_minutes: int,
              prices=None, consumption=None, production=None,
              net_consumption=None) -> 'ForecastSnapshot':
        """ Build a snapshot from the loop's numpy arrays. """
        return cls(
            run_time=float(run_time),
            interval_minutes=int(interval_minutes),
            prices=_as_tuple(prices),
            consumption=_as_tuple(consumption),
            production=_as_tuple(production),
            net_consumption=_as_tuple(net_consumption),
        )

    @property
    def base_time(self) -> float:
        """ Start of slot 0, in epoch seconds. """
        return align_to_interval(self.run_time, self.interval_minutes)

    @property
    def interval_seconds(self) -> int:
        """ Slot width in seconds. """
        return self.interval_minutes * 60

    def slot_start_ms(self, index: int) -> int:
        """ Start of a slot in epoch milliseconds. """
        return int((self.base_time + index * self.interval_seconds) * 1000)

    def has_data(self) -> bool:
        """ True when at least the price forecast is populated. """
        return bool(self.prices)

    def current_index(self, now: float) -> int:
        """ Index of the slot that contains `now` (epoch seconds).

        Normally 0. It grows when cycles keep failing before a new snapshot
        is published (a tariff provider outage, for example): slot 0 then
        lies in the past and must not be presented as the current price.
        """
        return max(0, int((now - self.base_time) // self.interval_seconds))

    def slots(self, values: Sequence[float], *,  # pylint: disable=too-many-arguments
              start: int = 0, stop: Optional[int] = None, origin: int = 0,
              digits: int = ENERGY_DIGITS,
              include_power: bool = False) -> list:
        """ Render values[start:stop] as a list of slot dictionaries.

        Every slot carries its index relative to `origin` (the current slot)
        and its start/end in epoch milliseconds, from the same slot generator
        the MQTT API uses (interval_utils.iter_slots). Only the requested
        window is rendered. Energy series also get the average power over the
        slot, because 'Wh per interval' and 'W' differ by a factor of four at
        15-minute resolution and that has tripped up consumers before.
        """
        result = []
        for index, start_s, end_s, value, power in iter_slots(
                values, self.run_time, self.interval_minutes,
                start=start, stop=stop):
            entry = {
                'slot': index - origin,
                'time_start': int(start_s * 1000),
                'time_end': int(end_s * 1000),
                'value': round(float(value), digits),
            }
            if include_power:
                entry['power_w'] = round(float(power), POWER_DIGITS)
            result.append(entry)
        return result
