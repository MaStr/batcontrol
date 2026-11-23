"""Fallback load profile is shipped with the package (R-K01)."""
import filecmp
import os
from pathlib import Path

import pytest
import pytz

from batcontrol.forecastconsumption import consumption
from batcontrol.forecastconsumption.consumption import Consumption
from batcontrol.forecastconsumption.forecast_csv import ForecastConsumptionCsv

REPO_ROOT = Path(__file__).resolve().parents[3]
TZ = pytz.timezone('Europe/Berlin')


@pytest.fixture(name='empty_cwd')
def fixture_empty_cwd(tmp_path, monkeypatch):
    """Run from a directory without a config/ folder (like a fresh volume)."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_bundled_profile_exists():
    assert os.path.isfile(consumption.default_load_profile_path())


def test_bundled_profile_matches_config_template():
    """The add-on and Docker template use config/load_profile_default.csv."""
    assert filecmp.cmp(consumption.default_load_profile_path(),
                       REPO_ROOT / 'config' / 'load_profile_default.csv',
                       shallow=False)


@pytest.mark.parametrize('config', [
    {'type': 'csv', 'annual_consumption': 4000},
    {'type': 'csv', 'annual_consumption': 4000, 'load_profile': None},
    {'type': 'csv', 'annual_consumption': 4000, 'load_profile': ''},
    {'type': 'csv', 'csv': {'annual_consumption': 4000}},
])
def test_fallback_works_without_config_folder(empty_cwd, config):
    # pylint: disable=unused-argument
    forecaster = Consumption.create_consumption(TZ, config)
    assert isinstance(forecaster, ForecastConsumptionCsv)
    assert forecaster.path_to_load_profile == consumption.default_load_profile_path()
    forecast = forecaster.get_forecast(24)
    assert len(forecast) >= 24
    assert all(value > 0 for value in forecast.values())


def test_configured_profile_still_read_from_config_folder(empty_cwd):
    (empty_cwd / 'config').mkdir()
    profile = empty_cwd / 'config' / 'my_profile.csv'
    profile.write_bytes(Path(consumption.default_load_profile_path()).read_bytes())
    forecaster = Consumption.create_consumption(
        TZ, {'type': 'csv', 'load_profile': 'my_profile.csv'})
    assert forecaster.path_to_load_profile == 'config/my_profile.csv'


def test_missing_configured_profile_still_fails(empty_cwd):
    # pylint: disable=unused-argument
    with pytest.raises(RuntimeError, match='not found'):
        Consumption.create_consumption(
            TZ, {'type': 'csv', 'load_profile': 'missing.csv'})
