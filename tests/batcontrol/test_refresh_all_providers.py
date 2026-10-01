"""Tests for the SIGHUP triggered refresh of all providers."""
import time
from unittest.mock import MagicMock

import pytz

from batcontrol.core import Batcontrol
from batcontrol.forecastsolar.baseclass import ForecastSolarBaseclass


class _DummySolar(ForecastSolarBaseclass):
    def __init__(self):
        super().__init__([{'name': 'a'}], pytz.UTC, 3600, 0)
        self.calls = 0

    def get_raw_data_from_provider(self, pvinstallation_name):
        self.calls += 1
        return {'result': {}}

    def get_forecast_from_raw_data(self):
        return {}

    def schedule_next_refresh(self):
        pass


def test_solar_force_bypasses_min_interval():
    solar = _DummySolar()
    solar.refresh_data()
    solar.refresh_data()
    assert solar.calls == 1
    solar.refresh_data(force=True)
    assert solar.calls == 2


def test_solar_force_respects_blackout_window():
    solar = _DummySolar()
    solar.refresh_data()
    solar.rate_limit_blackout_window_ts = time.time() + 600
    solar.refresh_data(force=True)
    assert solar.calls == 1


def test_refresh_all_providers_continues_after_error():
    bc = Batcontrol.__new__(Batcontrol)
    bc.fc_solar = MagicMock()
    bc.fc_solar.refresh_data.side_effect = RuntimeError('boom')
    bc.dynamic_tariff = MagicMock()
    bc.fc_consumption = MagicMock()

    bc.refresh_all_providers()

    bc.fc_solar.refresh_data.assert_called_once_with(force=True)
    bc.dynamic_tariff.refresh_data.assert_called_once_with(force=True)
    bc.fc_consumption.refresh_data.assert_called_once_with()
