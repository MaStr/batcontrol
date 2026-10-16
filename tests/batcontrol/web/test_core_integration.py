"""The contract between the control loop and the read-only API.

The service layer only reads values the loop has already cached, so these
tests pin the loop's side of that deal. They use a real Batcontrol instance
with mocked providers, because the point is what core.py actually does.
"""
import pytest

from batcontrol.core import Batcontrol
from batcontrol.web.service import BatcontrolService
from batcontrol.web.snapshot import ForecastSnapshot


@pytest.fixture(name='config')
def fixture_config():
    """Minimal config with every API and MQTT switch off."""
    return {
        'timezone': 'Europe/Berlin',
        'time_resolution_minutes': 60,
        'inverter': {
            'type': 'dummy',
            'max_grid_charge_rate': 5000,
            'max_pv_charge_rate': 3000,
            'min_pv_charge_rate': 100,
        },
        'utility': {'type': 'tibber', 'apikey': 'test_token'},
        'pvinstallations': [],
        'consumption_forecast': {'type': 'simple', 'value': 500},
        'battery_control': {
            'max_charging_from_grid_limit': 0.8,
            'min_price_difference': 0.05,
        },
        'mqtt': {'enabled': False},
    }


@pytest.fixture(name='inverter')
def fixture_inverter(mocker):
    """Patch the provider factories and return the mocked inverter."""
    mock_inverter = mocker.MagicMock()
    mock_inverter.get_SOC.return_value = 63.5
    mock_inverter.get_max_capacity.return_value = 10000
    mock_inverter.get_stored_energy.return_value = 6350
    mock_inverter.get_stored_usable_energy.return_value = 5350
    mock_inverter.get_free_capacity.return_value = 3650
    mock_inverter.min_soc = 10
    mock_inverter.max_soc = 95
    for name in ('tariff_factory.create_tarif_provider',
                 'solar_factory.create_solar_provider',
                 'consumption_factory.create_consumption'):
        mocker.patch(f'batcontrol.core.{name}', autospec=True,
                     return_value=mocker.MagicMock())
    mocker.patch('batcontrol.core.inverter_factory.create_inverter',
                 autospec=True, return_value=mock_inverter)
    return mock_inverter


def test_api_is_disabled_without_an_api_block(config, inverter):
    """An existing installation must see no behaviour change."""
    del inverter
    batcontrol = Batcontrol(config)
    try:
        assert batcontrol.api_config.enabled is False
        assert batcontrol.web_server is None
    finally:
        batcontrol.shutdown()


def test_refresh_populates_the_soc_cache_without_mqtt(config, inverter):
    """Regression: the SoC was only fetched inside the MQTT branch.

    With MQTT disabled last_SOC stayed on its -1 sentinel forever, so the
    REST API and the dashboard reported soc: null on a perfectly healthy
    system. The cached values are core state, not an MQTT detail.
    """
    del inverter
    batcontrol = Batcontrol(config)
    try:
        assert batcontrol.mqtt_api is None
        assert batcontrol.last_SOC == -1  # sentinel before the first refresh

        batcontrol.refresh_static_values()

        assert batcontrol.last_SOC == 63.5
        assert batcontrol.last_stored_energy == 6350
        battery = BatcontrolService(batcontrol).battery()
        assert battery['soc'] == 63.5
        # cached by the core, never read from the inverter by the web thread
        assert battery['soc_limits'] == {'min_soc': 10.0, 'max_soc': 95.0}
    finally:
        batcontrol.shutdown()


def test_forecast_snapshot_is_published_by_a_cycle(config, inverter):
    """__save_run_data has to hand the web layer a complete snapshot."""
    import numpy as np  # pylint: disable=import-outside-toplevel

    del inverter
    batcontrol = Batcontrol(config)
    try:
        assert batcontrol.forecast_snapshot is None
        batcontrol.last_run_time = 1792083600.0
        # pylint: disable=protected-access
        batcontrol._Batcontrol__save_run_data(
            np.array([0.0, 500.0]), np.array([400.0, 450.0]),
            np.array([400.0, -50.0]), np.array([0.30, 0.25]))

        snapshot = batcontrol.forecast_snapshot
        assert isinstance(snapshot, ForecastSnapshot)
        assert snapshot.prices == (0.30, 0.25)
        assert snapshot.interval_minutes == 60
    finally:
        batcontrol.shutdown()


def test_snapshot_does_not_alias_the_loops_arrays(config, inverter):
    """A reader must not see the next cycle's numbers in the old snapshot."""
    import numpy as np  # pylint: disable=import-outside-toplevel

    del inverter
    batcontrol = Batcontrol(config)
    try:
        prices = np.array([0.30, 0.25])
        batcontrol.last_run_time = 1792083600.0
        # pylint: disable=protected-access
        batcontrol._Batcontrol__save_run_data(
            np.zeros(2), np.zeros(2), np.zeros(2), prices)
        prices[0] = 9.99
        assert batcontrol.forecast_snapshot.prices == (0.30, 0.25)
    finally:
        batcontrol.shutdown()


def test_a_broken_api_config_does_not_stop_battery_control(config, inverter, caplog):
    """An unusable api: block must not take the control loop down with it.

    The API is a read-only accessory. core.py's own comment promises that a
    failure there cannot stop the control loop from coming up; that has to
    include parsing its configuration.
    """
    del inverter
    config['api'] = {'enabled': True, 'port': 'eighty'}
    with caplog.at_level('ERROR'):
        batcontrol = Batcontrol(config)
    try:
        assert 'Invalid api configuration' in caplog.text
        assert batcontrol.api_config.enabled is False
        assert batcontrol.web_server is None
        # and the parts that actually control the battery are up
        assert batcontrol.inverter is not None
        assert batcontrol.general_logic is not None
    finally:
        batcontrol.shutdown()
