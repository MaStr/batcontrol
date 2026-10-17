"""evcc based consumption forecasting

This module provides consumption forecasting using the household consumption
recorded by evcc (energy group "home"). The profile building is shared with
the HomeAssistant provider (ForecastConsumptionHistoryBaseclass), only the
read of the history is specific: one request to the evcc REST API delivers
the 15 minute slots of the whole needed time range, which are summed to
hours here.

The evcc endpoint /api/history/energy is marked as experimental by evcc.
See https://docs.evcc.io/integrations/rest-api/operations/getenergyhistory
"""

import contextlib
import datetime
import logging
import math
from typing import Dict, Optional

import requests

from .history_baseclass import ForecastConsumptionHistoryBaseclass

logger = logging.getLogger(__name__)
logger.info('Loading module')

EVCC_GROUP = 'home'
EVCC_MIN_VERSION = '0.311.0'
SLOT_SECONDS = 15 * 60
SLOTS_PER_HOUR = 4
HOUR_SECONDS = 3600


# pylint: disable=too-many-instance-attributes
class ForecastConsumptionEvccHome(ForecastConsumptionHistoryBaseclass):
    """Forecasts consumption based on the history of the evcc group "home"

    The history of the configured days (e.g., last 7, 14, 21 days) is read
    with a single request per refresh and averaged with weights for each hour
    of the week, like for the HomeAssistant provider.

    Attributes:
        base_url: evcc base URL without /api
        timeout: Request timeout in seconds
    """

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def __init__(
        self,
        url: str,
        timezone,
        history_days=None,
        history_weights=None,
        cache_ttl_hours: float = 48.0,
        multiplier: float = 1.0,
        target_resolution: int = 60,
        timeout: int = 30
    ) -> None:
        """Initialize evcc consumption forecaster

        Args:
            url: evcc base URL (e.g., "http://evcc.local:7070")
            timezone: Timezone object for data processing
            history_days: List of negative day offsets to fetch (e.g., [-7, -14, -21])
            history_weights: Weight for each history period (1-10), same length as history_days
            cache_ttl_hours: Time-to-live for cached statistics in hours (default: 48)
            multiplier: Multiplier applied to all forecast values (default: 1.0)
            target_resolution: Target resolution in minutes (15 or 60)
            timeout: Request timeout in seconds (default: 30)
        """
        super().__init__(
            timezone,
            history_days=history_days,
            history_weights=history_weights,
            cache_ttl_hours=cache_ttl_hours,
            multiplier=multiplier,
            target_resolution=target_resolution)

        self.base_url = self._normalize_url(url)
        self.timeout = timeout
        # Wh per full UTC hour (epoch seconds), only filled inside a session
        self._hourly_wh: Optional[Dict[int, float]] = None

        logger.info(
            "Initialized evcc consumption forecaster: url=%s, history_days=%s, "
            "weights=%s, cache_ttl=%0.1fh, multiplier=%0.2f",
            self.base_url, self.history_days, self.history_weights,
            cache_ttl_hours, multiplier
        )

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Validate the evcc URL and strip a trailing slash and /api

        Raises:
            ValueError: If the URL is not an http(s) address
        """
        cleaned = (url or '').strip()
        lowered = cleaned.lower()

        if lowered.startswith(('ws://', 'wss://')):
            raise ValueError(
                f"evcc-home expects http(s):// as url, got '{cleaned}'. "
                f"A ws:// address is only used for HomeAssistant."
            )
        if not lowered.startswith(('http://', 'https://')):
            raise ValueError(
                f"evcc-home expects http(s):// as url, got '{cleaned}' "
                f"(e.g. http://evcc.local:7070)"
            )

        cleaned = cleaned.rstrip('/')
        if cleaned.lower().endswith('/api'):
            cleaned = cleaned[:-len('/api')].rstrip('/')
        return cleaned

    @staticmethod
    def _is_ambiguous(epoch: int, timezone) -> bool:
        """True if the local wall time of epoch occurs twice (end of DST)"""
        wall = datetime.datetime.fromtimestamp(
            epoch, tz=timezone).replace(tzinfo=None)
        for offset in (-HOUR_SECONDS, HOUR_SECONDS):
            other = datetime.datetime.fromtimestamp(
                epoch + offset, tz=timezone).replace(tzinfo=None)
            if other == wall:
                return True
        return False

    def _request_history(
        self,
        range_from: datetime.datetime,
        range_to: datetime.datetime
    ) -> list:
        """Read the energy history from evcc

        Returns:
            The decoded JSON list of series

        Raises:
            RuntimeError: With a readable message if the request fails
        """
        url = f"{self.base_url}/api/history/energy"
        params = {
            'group': EVCC_GROUP,
            'aggregate': '15m',
            'format': 'json',
            'from': range_from.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'to': range_to.strftime('%Y-%m-%dT%H:%M:%SZ'),
        }
        logger.debug("Requesting evcc history: %s %s", url, params)

        try:
            response = requests.get(url, params=params, timeout=self.timeout)
        except requests.exceptions.Timeout as e:
            raise RuntimeError(
                f"evcc request timed out after {self.timeout} s: {url}") from e
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(
                f"evcc is not reachable at {self.base_url}, "
                f"check the url: {e}") from e
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"evcc request failed: {e}") from e

        status = response.status_code
        if status == 404:
            raise RuntimeError(
                f"evcc returned HTTP 404 for {url}: evcc is too old or the "
                f"url is wrong, evcc >= {EVCC_MIN_VERSION} is required")
        if status == 400:
            raise RuntimeError(
                f"evcc returned HTTP 400, the evcc database is offline or "
                f"the parameters are invalid: {str(response.text)[:200]}")
        if status != 200:
            raise RuntimeError(
                f"evcc returned HTTP {status} for {url}: "
                f"{str(response.text)[:200]}")

        try:
            return response.json()
        except ValueError as e:
            raise RuntimeError(
                f"evcc returned no valid JSON: {e}") from e

    def _quarters_from_series(self, series_list) -> Dict[int, float]:
        """Wh per quarter hour (epoch of the slot start) of the group home

        Several series of the group are summed per slot. Slots without a
        usable value and slots of an ambiguous DST hour are left out.
        """
        quarters: Dict[int, float] = {}
        if not isinstance(series_list, list):
            return quarters

        for series in series_list:
            if not isinstance(series, dict) or series.get('group') != EVCC_GROUP:
                continue
            for slot in series.get('data') or []:
                epoch = self._slot_epoch(slot)
                if epoch is None:
                    continue
                energy = slot.get('energy')
                if isinstance(energy, bool) or not isinstance(energy, (int, float)):
                    logger.debug("Skipping non-numeric energy %s", energy)
                    continue
                if not math.isfinite(energy) or energy < 0:
                    logger.debug("Skipping invalid energy %s", energy)
                    continue
                # evcc delivers kWh, batcontrol works with Wh
                quarters[epoch] = quarters.get(epoch, 0.0) + energy * 1000.0
        return quarters

    def _slot_epoch(self, slot) -> Optional[int]:
        """UTC epoch of the slot start, None if the slot is not usable"""
        if not isinstance(slot, dict):
            return None
        try:
            start = datetime.datetime.fromisoformat(str(slot.get('start')))
        except ValueError:
            logger.debug("Skipping slot with invalid start %s", slot)
            return None
        if start.tzinfo is None:
            logger.debug("Skipping slot without time zone %s", slot)
            return None

        epoch = int(start.timestamp())
        if epoch % SLOT_SECONDS != 0:
            logger.debug("Skipping slot not aligned to 15 minutes %s", slot)
            return None
        if self._is_ambiguous(epoch, self.timezone):
            logger.debug(
                "Skipping slot %s in the repeated hour of the end of daylight "
                "saving time", slot.get('start'))
            return None
        return epoch

    @staticmethod
    def _sum_to_hours(quarters: Dict[int, float]) -> Dict[int, float]:
        """Sum quarter hours to full UTC hours, only hours with 4 of 4 slots"""
        sums: Dict[int, float] = {}
        counts: Dict[int, int] = {}
        for epoch, wh in quarters.items():
            hour = epoch // HOUR_SECONDS * HOUR_SECONDS
            sums[hour] = sums.get(hour, 0.0) + wh
            counts[hour] = counts.get(hour, 0) + 1

        hourly = {}
        for hour, wh in sums.items():
            if counts[hour] == SLOTS_PER_HOUR:
                hourly[hour] = wh
            else:
                logger.debug(
                    "Incomplete hour %s (%d of %d slots), treated as gap",
                    datetime.datetime.fromtimestamp(
                        hour, tz=datetime.timezone.utc).isoformat(),
                    counts[hour], SLOTS_PER_HOUR)
        return hourly

    @contextlib.contextmanager
    def _history_session(self, earliest_start, latest_end):
        """Read the whole needed time range with a single request

        Raises:
            RuntimeError: If evcc cannot be read or has no data for "home"
        """
        utc = datetime.timezone.utc
        now_hour = datetime.datetime.now(tz=utc).replace(
            minute=0, second=0, microsecond=0)
        range_from = earliest_start.astimezone(utc).replace(
            minute=0, second=0, microsecond=0)
        # Only complete hours have data
        range_to = min(
            latest_end.astimezone(utc).replace(
                minute=0, second=0, microsecond=0),
            now_hour)

        try:
            if range_to <= range_from:
                logger.warning(
                    "No completed hour in the requested history range, "
                    "evcc is not queried")
                self._hourly_wh = {}
            else:
                series_list = self._request_history(range_from, range_to)
                quarters = self._quarters_from_series(series_list)
                if not quarters:
                    raise RuntimeError(
                        f"evcc returned no energy history for the group "
                        f"'{EVCC_GROUP}' between {range_from.isoformat()} and "
                        f"{range_to.isoformat()}. The history starts when evcc "
                        f"began recording, check evcc >= {EVCC_MIN_VERSION} "
                        f"and the evcc database")
                self._hourly_wh = self._sum_to_hours(quarters)
                logger.debug(
                    "evcc history: %d quarter hours, %d complete hours",
                    len(quarters), len(self._hourly_wh))
            yield
        finally:
            self._hourly_wh = None

    # pylint: disable=unused-argument
    def _fetch_slot_value(self, start_time, end_time) -> float:
        """Energy in Wh of the hour starting at start_time, -1 without data"""
        if self._hourly_wh is None:
            raise RuntimeError("evcc history was not loaded")

        hour = int(start_time.timestamp()) // HOUR_SECONDS * HOUR_SECONDS
        return self._hourly_wh.get(hour, -1)
