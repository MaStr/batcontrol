"""The providers align to the timestamp of the control cycle.

Every provider used to read the clock itself, some twice per call (start of
the hour, then the current interval). The control loop now passes one
timestamp, so slot 0 means the same interval everywhere.
"""
import datetime
from unittest.mock import patch

import pytest
import pytz

from batcontrol.dynamictariff.tariffzones import TariffZones

from .dynamictariff.test_baseclass import ConcreteTariffProvider
from .forecastconsumption.test_baseclass import ConcreteConsumptionForecast
from .forecastsolar.test_baseclass import ConcreteForecastSolar

BERLIN = pytz.timezone('Europe/Berlin')
# Deliberately not the wall clock: 10:50 is the fourth quarter of the hour.
CYCLE_NOW = BERLIN.localize(datetime.datetime(2024, 1, 15, 10, 50, 0))


class RecordingTariff(ConcreteTariffProvider):
    """Records the timestamp the base class hands to the provider."""
    received_now = 'not called'

    def _get_prices_native(self, now=None):
        self.received_now = now
        return super()._get_prices_native(now)


class RecordingConsumption(ConcreteConsumptionForecast):
    """Records the timestamp the base class hands to the provider."""
    received_now = 'not called'

    def _get_forecast_native(self, hours, now=None):
        self.received_now = now
        return super()._get_forecast_native(hours, now)


class RecordingSolar(ConcreteForecastSolar):
    """Records the timestamp the base class hands to the provider."""
    received_now = 'not called'

    def get_forecast_from_raw_data(self, now=None):
        self.received_now = now
        return {h: 100.0 * h for h in range(24)}


def test_tariff_aligns_to_the_passed_timestamp():
    provider = RecordingTariff(
        BERLIN, target_resolution=15, native_resolution=60)

    with patch('batcontrol.dynamictariff.baseclass.datetime') as mock_dt:
        prices = provider.get_prices(now=CYCLE_NOW)

    mock_dt.datetime.now.assert_not_called()
    assert provider.received_now is CYCLE_NOW
    # Three quarters of the current hour are dropped: slot 0 is the last
    # quarter at the price of hour 0, slot 1 starts the next hour.
    assert prices[0] == pytest.approx(0.10)
    assert prices[1] == pytest.approx(0.11)
    assert len(prices) == 24 * 4 - 3


def test_tariff_without_timestamp_reads_the_clock():
    provider = RecordingTariff(
        BERLIN, target_resolution=15, native_resolution=60)

    with patch('batcontrol.dynamictariff.baseclass.datetime') as mock_dt:
        mock_dt.datetime.now.return_value = CYCLE_NOW
        prices = provider.get_prices()

    assert provider.received_now is None
    assert len(prices) == 24 * 4 - 3


def test_consumption_aligns_to_the_passed_timestamp():
    provider = RecordingConsumption(
        BERLIN, target_resolution=15, native_resolution=60)

    with patch('batcontrol.forecastconsumption.baseclass.datetime') as mock_dt:
        forecast = provider.get_forecast(4, now=CYCLE_NOW)

    mock_dt.datetime.now.assert_not_called()
    assert provider.received_now is CYCLE_NOW
    # 4 slots plus the 3 dropped ones need two hours of source data.
    assert provider.requested_hours == [2]
    assert sorted(forecast) == [0, 1, 2, 3]


def test_solar_aligns_to_the_passed_timestamp():
    provider = RecordingSolar(
        [{'name': 'single'}], BERLIN,
        min_time_between_API_calls=900, delay_evaluation_by_seconds=0,
        target_resolution=15, native_resolution=60)
    provider.refresh_data = lambda force=False: None

    forecast = provider.get_forecast(now=CYCLE_NOW)

    assert provider.received_now is CYCLE_NOW
    # Slot 0 is 10:45. 24 hours of data from 10:00 end at 10:00 the next
    # day; padding reaches the midnight after that: 13.25 h + 24 h.
    assert len(forecast) == 149
    assert forecast[148] == 0.0


def test_provider_uses_the_timestamp_for_the_start_of_the_hour():
    """A real provider: index 0 is the hour of the passed timestamp."""
    provider = TariffZones(
        BERLIN, 900, 0, target_resolution=60,
        tariff_zone_1=0.30, zone_1_hours='0-9,11-23',
        tariff_zone_2=0.10, zone_2_hours='10')

    prices = provider.get_prices(now=CYCLE_NOW)

    assert prices[0] == 0.10
    assert prices[1] == 0.30


def test_timestamp_in_another_timezone_is_converted():
    provider = TariffZones(
        BERLIN, 900, 0, target_resolution=60,
        tariff_zone_1=0.30, zone_1_hours='0-9,11-23',
        tariff_zone_2=0.10, zone_2_hours='10')

    prices = provider.get_prices(
        now=CYCLE_NOW.astimezone(datetime.timezone.utc))

    assert prices[0] == 0.10


NAIVE_NOW = datetime.datetime(2024, 1, 15, 10, 50, 0)


def test_tariff_rejects_a_naive_timestamp():
    """astimezone() would read it as system local time, not as the
    configured timezone."""
    provider = RecordingTariff(
        BERLIN, target_resolution=15, native_resolution=60)

    with pytest.raises(ValueError, match='timezone-aware'):
        provider.get_prices(now=NAIVE_NOW)
    assert provider.received_now == 'not called'


def test_consumption_rejects_a_naive_timestamp():
    provider = RecordingConsumption(
        BERLIN, target_resolution=15, native_resolution=60)

    with pytest.raises(ValueError, match='timezone-aware'):
        provider.get_forecast(4, now=NAIVE_NOW)
    assert provider.received_now == 'not called'


def test_solar_rejects_a_naive_timestamp():
    provider = RecordingSolar(
        [{'name': 'single'}], BERLIN,
        min_time_between_API_calls=900, delay_evaluation_by_seconds=0,
        target_resolution=15, native_resolution=60)

    with pytest.raises(ValueError, match='timezone-aware'):
        provider.get_forecast(now=NAIVE_NOW)
    assert provider.received_now == 'not called'
