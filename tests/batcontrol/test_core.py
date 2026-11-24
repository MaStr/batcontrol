"""Tests for core batcontrol functionality including MODE_LIMIT_BATTERY_CHARGE_RATE"""
import datetime
import logging
import pytest
from unittest.mock import MagicMock, call, patch

from batcontrol.core import (
    Batcontrol,
    CONTROL_SOURCE_API,
    CONTROL_SOURCE_OPTIMIZER,
    MODE_ALLOW_DISCHARGING,
    MODE_FORCE_CHARGING,
    MODE_LIMIT_BATTERY_CHARGE_RATE,
)
from batcontrol.inverter import (
    InverterCommunicationError,
    InverterOutageError,
)
from batcontrol.inverter.resilient_wrapper import ResilientInverterWrapper
from batcontrol.logic.logic import Logic as LogicFactory
from batcontrol.logic.common import CommonLogic
from batcontrol.value_utils import parse_bool_flag
from batcontrol.mqtt_api import MqttConnectionError


class TestModeLimitBatteryChargeRate:
    """Test MODE_LIMIT_BATTERY_CHARGE_RATE (mode 8) functionality"""

    @pytest.fixture
    def mock_config(self):
        """Provide a minimal config for testing"""
        return {
            'timezone': 'Europe/Berlin',
            'time_resolution_minutes': 60,
            'inverter': {
                'type': 'dummy',
                'max_grid_charge_rate': 5000,
                'max_pv_charge_rate': 3000,
                'min_pv_charge_rate': 100
            },
            'utility': {
                'type': 'tibber',
                'apikey': 'test_token'
            },
            'pvinstallations': [],
            'consumption_forecast': {
                'type': 'simple',
                'value': 500
            },
            'battery_control': {
                'max_charging_from_grid_limit': 0.8,
                'min_price_difference': 0.05
            },
            'mqtt': {
                'enabled': False
            }
        }

    @pytest.fixture
    def core_dependencies(self, mocker):
        """Patch Batcontrol dependencies and return the mocked inverter."""
        mock_inverter = mocker.MagicMock()
        mock_inverter.max_pv_charge_rate = 3000
        mock_inverter.set_mode_limit_battery_charge = mocker.MagicMock()
        mock_inverter.get_max_capacity = mocker.MagicMock(return_value=10000)

        mocker.patch(
            'batcontrol.core.tariff_factory.create_tarif_provider',
            autospec=True,
            return_value=mocker.MagicMock(),
        )
        mocker.patch(
            'batcontrol.core.inverter_factory.create_inverter',
            autospec=True,
            return_value=mock_inverter,
        )
        mocker.patch(
            'batcontrol.core.solar_factory.create_solar_provider',
            autospec=True,
            return_value=mocker.MagicMock(),
        )
        mocker.patch(
            'batcontrol.core.consumption_factory.create_consumption',
            autospec=True,
            return_value=mocker.MagicMock(),
        )
        return mock_inverter

    def test_mode_constant_exists(self):
        """Test that MODE_LIMIT_BATTERY_CHARGE_RATE constant is defined"""
        assert MODE_LIMIT_BATTERY_CHARGE_RATE == 8

    def test_limit_battery_charge_rate_method(
            self, mock_config, core_dependencies):
        """Test limit_battery_charge_rate method applies correct limits"""
        mock_inverter = core_dependencies
        bc = Batcontrol(mock_config)

        bc.limit_battery_charge_rate(2000)

        mock_inverter.set_mode_limit_battery_charge.assert_called_once_with(2000)
        assert bc.last_mode == MODE_LIMIT_BATTERY_CHARGE_RATE

    def test_limit_battery_charge_rate_capped_by_max(
            self, mock_config, core_dependencies):
        """Test that limit is capped by max_pv_charge_rate"""
        mock_inverter = core_dependencies
        bc = Batcontrol(mock_config)

        bc.limit_battery_charge_rate(5000)

        mock_inverter.set_mode_limit_battery_charge.assert_called_once_with(3000)

    def test_limit_battery_charge_rate_floored_by_min(
            self, mock_config, core_dependencies):
        """Test that limit is floored by min_pv_charge_rate"""
        mock_inverter = core_dependencies
        bc = Batcontrol(mock_config)

        bc.limit_battery_charge_rate(50)

        mock_inverter.set_mode_limit_battery_charge.assert_called_once_with(100)

    def test_limit_battery_charge_rate_zero_allowed(
            self, mock_config, core_dependencies):
        """Test that limit=0 blocks charging when min=0"""
        mock_config['inverter']['min_pv_charge_rate'] = 0
        mock_inverter = core_dependencies
        bc = Batcontrol(mock_config)

        bc.limit_battery_charge_rate(0)

        mock_inverter.set_mode_limit_battery_charge.assert_called_once_with(0)

    def test_limit_battery_charge_rate_min_exceeds_max(
            self, mock_config, core_dependencies):
        """Test that when min_pv_charge_rate > max_pv_charge_rate, min is clamped to max at init"""
        mock_config['inverter']['min_pv_charge_rate'] = 4000
        mock_inverter = core_dependencies
        bc = Batcontrol(mock_config)

        assert bc.min_pv_charge_rate == 3000

        bc.limit_battery_charge_rate(1000)

        mock_inverter.set_mode_limit_battery_charge.assert_called_once_with(3000)

    def test_api_set_mode_accepts_mode_8(
            self, mock_config, core_dependencies):
        """Test that api_set_mode accepts MODE_LIMIT_BATTERY_CHARGE_RATE"""
        bc = Batcontrol(mock_config)

        bc._limit_battery_charge_rate = 2000
        bc.api_set_mode(MODE_LIMIT_BATTERY_CHARGE_RATE)

        assert bc.last_mode == MODE_LIMIT_BATTERY_CHARGE_RATE
        assert bc.api_overwrite is True

    def test_api_set_limit_battery_charge_rate(
            self, mock_config, core_dependencies, mocker):
        """Test api_set_limit_battery_charge_rate updates the dynamic value"""
        mock_inverter = core_dependencies
        bc = Batcontrol(mock_config)
        bc.mqtt_api = mocker.MagicMock()

        bc.api_set_limit_battery_charge_rate(2500)

        assert bc._limit_battery_charge_rate == 2500
        bc.mqtt_api.publish_limit_battery_charge_rate.assert_called_once_with(2500)
        mock_inverter.set_mode_limit_battery_charge.assert_not_called()

    def test_api_set_limit_applies_immediately_in_mode_8(
            self, mock_config, core_dependencies, mocker):
        """Test that changing limit applies immediately when in mode 8"""
        mock_inverter = core_dependencies
        bc = Batcontrol(mock_config)
        bc.mqtt_api = mocker.MagicMock()

        bc.limit_battery_charge_rate(1000)
        mock_inverter.set_mode_limit_battery_charge.reset_mock()
        bc.mqtt_api.reset_mock()

        bc.api_set_limit_battery_charge_rate(2000)

        mock_inverter.set_mode_limit_battery_charge.assert_called_once_with(2000)
        bc.mqtt_api.publish_limit_battery_charge_rate.assert_called_once_with(2000)


class TestTimeResolutionString:
    """Test that time_resolution_minutes provided as string (e.g. from Home Assistant) is handled correctly"""

    @pytest.fixture
    def base_mock_config(self):
        """Provide a minimal config for testing"""
        return {
            'timezone': 'Europe/Berlin',
            'inverter': {
                'type': 'dummy',
                'max_grid_charge_rate': 5000,
                'max_pv_charge_rate': 3000,
                'min_pv_charge_rate': 100
            },
            'utility': {
                'type': 'tibber',
                'apikey': 'test_token'
            },
            'pvinstallations': [],
            'consumption_forecast': {
                'type': 'simple',
                'value': 500
            },
            'battery_control': {
                'max_charging_from_grid_limit': 0.8,
                'min_price_difference': 0.05
            },
            'mqtt': {
                'enabled': False
            }
        }

    @pytest.mark.parametrize('resolution_str,expected_int', [('60', 60), ('15', 15)])
    @patch('batcontrol.core.tariff_factory.create_tarif_provider')
    @patch('batcontrol.core.inverter_factory.create_inverter')
    @patch('batcontrol.core.solar_factory.create_solar_provider')
    @patch('batcontrol.core.consumption_factory.create_consumption')
    def test_string_time_resolution_initialises_without_error(
        self, mock_consumption, mock_solar, mock_inverter_factory, mock_tariff,
        base_mock_config, resolution_str, expected_int
    ):
        """Batcontrol must not crash when time_resolution_minutes is a string"""
        mock_inverter = MagicMock()
        mock_inverter.get_max_capacity = MagicMock(return_value=10000)
        mock_inverter_factory.return_value = mock_inverter
        mock_tariff.return_value = MagicMock()
        mock_solar.return_value = MagicMock()
        mock_consumption.return_value = MagicMock()

        base_mock_config['time_resolution_minutes'] = resolution_str
        bc = Batcontrol(base_mock_config)

        assert isinstance(bc.time_resolution, int)
        assert bc.time_resolution == expected_int

    def test_invalid_time_resolution_names_the_value(self, base_mock_config):
        """Regression: the message used to contain a literal '%s'."""
        base_mock_config['time_resolution_minutes'] = 30
        with pytest.raises(ValueError, match="got 30[.]"):
            Batcontrol(base_mock_config)

    @pytest.mark.parametrize('raw, expected', [
        (' 15 ', 15), ('60', 60), (15, 15), (60.0, 60), ('15.0', 15)])
    @patch('batcontrol.core.tariff_factory.create_tarif_provider')
    @patch('batcontrol.core.inverter_factory.create_inverter')
    @patch('batcontrol.core.solar_factory.create_solar_provider')
    @patch('batcontrol.core.consumption_factory.create_consumption')
    def test_validated_int_is_written_back(
        self, mock_consumption, mock_solar, mock_inverter_factory,
        mock_tariff, base_mock_config, raw, expected
    ):
        """Every later reader of the dict gets the int, not the string."""
        mock_inverter = MagicMock()
        mock_inverter.get_max_capacity = MagicMock(return_value=10000)
        mock_inverter_factory.return_value = mock_inverter
        base_mock_config['time_resolution_minutes'] = raw
        bc = Batcontrol(base_mock_config)

        assert type(bc.time_resolution) is int
        assert bc.time_resolution == expected
        assert bc.intervals_per_hour == 60 // expected
        assert type(bc.intervals_per_hour) is int
        assert base_mock_config['time_resolution_minutes'] == expected
        assert type(base_mock_config['time_resolution_minutes']) is int

    @pytest.mark.parametrize('raw', [
        'quarter', '', '30', 30, 15.5, None, True, [15]])
    def test_invalid_time_resolution_stops_before_anything_is_created(
            self, mocker, base_mock_config, raw):
        """Clear error naming key and allowed values; no provider and
        no inverter exists yet."""
        factories = [
            mocker.patch(target, autospec=True) for target in (
                'batcontrol.core.tariff_factory.create_tarif_provider',
                'batcontrol.core.inverter_factory.create_inverters',
                'batcontrol.core.solar_factory.create_solar_provider',
                'batcontrol.core.consumption_factory.create_consumption',
                'batcontrol.core.validate_battery_control',
            )]
        base_mock_config['time_resolution_minutes'] = raw
        with pytest.raises(ValueError) as excinfo:
            Batcontrol(base_mock_config)
        message = str(excinfo.value)
        assert 'time_resolution_minutes' in message
        assert '15 (quarter-hourly) or 60 (hourly)' in message
        assert repr(raw) in message
        for factory in factories:
            factory.assert_not_called()

    def test_missing_timezone_is_reported(self, base_mock_config):
        """Regression: the handler itself raised a bare KeyError."""
        del base_mock_config['timezone']
        with pytest.raises(RuntimeError, match="'timezone' is missing"):
            Batcontrol(base_mock_config)

    @pytest.mark.parametrize('value', ['Mars/Phobos', 42])
    def test_invalid_timezone_names_the_value(self, base_mock_config, value):
        base_mock_config['timezone'] = value
        with pytest.raises(RuntimeError, match='not valid') as excinfo:
            Batcontrol(base_mock_config)
        assert repr(value) in str(excinfo.value)

    @patch('batcontrol.core.tariff_factory.create_tarif_provider')
    @patch('batcontrol.core.inverter_factory.create_inverter')
    @patch('batcontrol.core.solar_factory.create_solar_provider')
    @patch('batcontrol.core.consumption_factory.create_consumption')
    def test_shared_data_directory_is_forwarded_to_provider_factories(
        self, mock_consumption, mock_solar, mock_inverter_factory, mock_tariff,
        base_mock_config, tmp_path
    ):
        """Solar and tariff caches share one explicit application data root."""
        mock_inverter = MagicMock()
        mock_inverter.get_max_capacity = MagicMock(return_value=10000)
        mock_inverter_factory.return_value = mock_inverter
        mock_tariff.return_value = MagicMock()
        mock_solar.return_value = MagicMock()
        mock_consumption.return_value = MagicMock()

        Batcontrol(
            base_mock_config,
            data_directory=tmp_path,
        )

        assert (
            mock_solar.call_args.kwargs['persistent_cache_directory']
            == tmp_path / 'cache' / 'solar'
        )
        assert (
            mock_tariff.call_args.kwargs['persistent_cache_directory']
            == tmp_path / 'cache' / 'tariff'
        )

    @pytest.mark.parametrize('resolution_str', ['60', '15'])
    def test_logic_factory_accepts_string_resolution_as_int(self, resolution_str):
        """Logic factory must produce a valid logic instance when given an int resolution"""
        logic = LogicFactory.create_logic(
            int(resolution_str),
            {'battery_control': {'type': 'default'}},
            datetime.timezone.utc
        )
        assert logic is not None
        assert logic.interval_minutes == int(resolution_str)


class TestCoreRunDispatch:
    """Characterize Batcontrol.run() dispatch to inverter methods"""

    @pytest.fixture
    def mock_config(self):
        return {
            'timezone': 'Europe/Berlin',
            'time_resolution_minutes': 60,
            'inverter': {
                'type': 'dummy',
                'max_grid_charge_rate': 5000,
                'max_pv_charge_rate': 3000,
                'min_pv_charge_rate': 0,
            },
            'utility': {
                'type': 'tibber',
                'apikey': 'test_token'
            },
            'pvinstallations': [],
            'consumption_forecast': {
                'type': 'simple',
                'value': 500
            },
            'battery_control': {
                'max_charging_from_grid_limit': 0.8,
                'min_price_difference': 0.05
            },
            'mqtt': {'enabled': False}
        }

    @pytest.fixture
    def run_dispatch_setup(self, mock_config, mocker):
        core_module = "batcontrol.core"

        mock_inverter = mocker.MagicMock()
        mock_inverter.max_pv_charge_rate = 3000
        mock_inverter.max_grid_charge_rate = 5000
        mock_inverter.get_max_capacity.return_value = 10000
        mock_inverter.get_SOC.return_value = 50
        mock_inverter.get_stored_energy.return_value = 5000
        mock_inverter.get_stored_usable_energy.return_value = 4500
        mock_inverter.get_free_capacity.return_value = 5000

        mock_tariff_provider = mocker.MagicMock()
        mock_tariff_provider.get_prices.return_value = {0: 0.20, 1: 0.30, 2: 0.25}
        mock_tariff_provider.refresh_data = mocker.MagicMock()

        mock_solar_provider = mocker.MagicMock()
        mock_solar_provider.get_forecast.return_value = {0: 0, 1: 0, 2: 0}
        mock_solar_provider.refresh_data = mocker.MagicMock()

        mock_consumption_provider = mocker.MagicMock()
        mock_consumption_provider.get_forecast.return_value = {0: 500, 1: 500, 2: 500}
        mock_consumption_provider.refresh_data = mocker.MagicMock()

        fake_logic = mocker.MagicMock()
        fake_logic.calculate.return_value = True
        fake_logic.get_calculation_output.return_value = mocker.MagicMock(
            reserved_energy=0,
            required_recharge_energy=0,
            min_dynamic_price_difference=0.05,
        )

        mocker.patch(
            f"{core_module}.tariff_factory.create_tarif_provider",
            autospec=True,
            return_value=mock_tariff_provider,
        )
        mocker.patch(
            f"{core_module}.inverter_factory.create_inverter",
            autospec=True,
            return_value=mock_inverter,
        )
        mocker.patch(
            f"{core_module}.solar_factory.create_solar_provider",
            autospec=True,
            return_value=mock_solar_provider,
        )
        mocker.patch(
            f"{core_module}.consumption_factory.create_consumption",
            autospec=True,
            return_value=mock_consumption_provider,
        )
        mocker.patch(
            f"{core_module}.LogicFactory.create_logic",
            autospec=True,
            return_value=fake_logic,
        )

        bc = Batcontrol(mock_config)

        yield bc, mock_inverter, fake_logic

        bc.shutdown()

    def _patch_core_dependencies(self, mocker):
        core_module = "batcontrol.core"
        mock_inverter = mocker.MagicMock()
        mock_inverter.max_pv_charge_rate = 3000
        mock_inverter.get_max_capacity.return_value = 10000
        mocker.patch(
            f"{core_module}.tariff_factory.create_tarif_provider",
            autospec=True,
            return_value=mocker.MagicMock(),
        )
        mocker.patch(
            f"{core_module}.inverter_factory.create_inverter",
            autospec=True,
            return_value=mock_inverter,
        )
        mocker.patch(
            f"{core_module}.solar_factory.create_solar_provider",
            autospec=True,
            return_value=mocker.MagicMock(),
        )
        mocker.patch(
            f"{core_module}.consumption_factory.create_consumption",
            autospec=True,
            return_value=mocker.MagicMock(),
        )
        mocker.patch(
            f"{core_module}.LogicFactory.create_logic",
            autospec=True,
            return_value=mocker.MagicMock(),
        )

    def test_accepts_min_grid_charge_soc_numeric_string_config(
            self, mock_config, mocker):
        mock_config['battery_control']['min_grid_charge_soc'] = '0.55'
        self._patch_core_dependencies(mocker)

        bc = Batcontrol(mock_config)

        assert bc.min_grid_charge_soc == 0.55
        bc.shutdown()

    def test_second_shutdown_is_a_no_op(self, mock_config, mocker, caplog):
        self._patch_core_dependencies(mocker)
        bc = Batcontrol(mock_config)
        inverter = bc.inverter

        bc.shutdown()
        with caplog.at_level(logging.DEBUG, logger='batcontrol.core'):
            bc.shutdown()

        inverter.shutdown.assert_called_once()
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    def test_rejects_invalid_min_grid_charge_soc_config(
            self, mock_config, mocker):
        mock_config['battery_control']['min_grid_charge_soc'] = 'fifty-five'
        self._patch_core_dependencies(mocker)

        with pytest.raises(
                ValueError,
                match='battery_control.min_grid_charge_soc must be numeric'):
            Batcontrol(mock_config)

    def test_accepts_grid_charge_target_strategy_case_insensitively(
            self, mock_config, mocker):
        mock_config['battery_control']['grid_charge_target_strategy'] = 'Forecast'
        self._patch_core_dependencies(mocker)

        bc = Batcontrol(mock_config)

        assert bc.grid_charge_target_strategy == 'forecast'
        bc.shutdown()

    def test_accepts_grid_charge_target_strategy_with_whitespace(
            self, mock_config, mocker):
        mock_config['battery_control']['grid_charge_target_strategy'] = ' forecast '
        self._patch_core_dependencies(mocker)

        bc = Batcontrol(mock_config)

        assert bc.grid_charge_target_strategy == 'forecast'
        bc.shutdown()

    def test_rejects_unknown_grid_charge_target_strategy_config(
            self, mock_config, mocker):
        mock_config['battery_control']['grid_charge_target_strategy'] = 'dynamic'
        self._patch_core_dependencies(mocker)

        with pytest.raises(
                ValueError,
                match='battery_control.grid_charge_target_strategy must be one of'):
            Batcontrol(mock_config)

    def test_warns_when_min_grid_charge_soc_exceeds_grid_charge_limit(
            self, mock_config, mocker, caplog):
        core_module = "batcontrol.core"
        mock_config['battery_control']['min_grid_charge_soc'] = 0.85
        caplog.set_level(logging.WARNING, logger=core_module)

        mock_inverter = mocker.MagicMock()
        mock_inverter.max_pv_charge_rate = 3000
        mock_inverter.get_max_capacity.return_value = 10000
        mocker.patch(
            f"{core_module}.tariff_factory.create_tarif_provider",
            autospec=True,
            return_value=mocker.MagicMock(),
        )
        mocker.patch(
            f"{core_module}.inverter_factory.create_inverter",
            autospec=True,
            return_value=mock_inverter,
        )
        mocker.patch(
            f"{core_module}.solar_factory.create_solar_provider",
            autospec=True,
            return_value=mocker.MagicMock(),
        )
        mocker.patch(
            f"{core_module}.consumption_factory.create_consumption",
            autospec=True,
            return_value=mocker.MagicMock(),
        )
        mocker.patch(
            f"{core_module}.LogicFactory.create_logic",
            autospec=True,
            return_value=mocker.MagicMock(),
        )

        bc = Batcontrol(mock_config)

        assert bc.min_grid_charge_soc == 0.85
        assert any(
            'min_grid_charge_soc' in record.message
            and 'max_charging_from_grid_limit' in record.message
            and 'grid charging cannot reach' in record.message
            for record in caplog.records
        )
        bc.shutdown()

    def test_run_dispatches_allow_discharge(self, run_dispatch_setup):
        bc, mock_inverter, fake_logic = run_dispatch_setup
        fake_logic.get_inverter_control_settings.return_value = MagicMock(
            allow_discharge=True,
            charge_from_grid=False,
            charge_rate=0,
            limit_battery_charge_rate=-1,
        )

        bc.run()

        mock_inverter.set_mode_allow_discharge.assert_called_once_with()
        mock_inverter.set_mode_force_charge.assert_not_called()
        mock_inverter.set_mode_avoid_discharge.assert_not_called()
        mock_inverter.set_mode_limit_battery_charge.assert_not_called()

    def test_run_skips_cycle_on_communication_error(self, run_dispatch_setup):
        """A transient inverter outage aborts the cycle without raising."""
        bc, mock_inverter, _fake_logic = run_dispatch_setup
        mock_inverter.get_stored_energy.side_effect = \
            InverterCommunicationError("get_stored_energy")

        # Must not raise - the cycle is skipped and retried on the next run.
        bc.run()

        # No control action was applied this cycle.
        mock_inverter.set_mode_allow_discharge.assert_not_called()
        mock_inverter.set_mode_force_charge.assert_not_called()
        mock_inverter.set_mode_avoid_discharge.assert_not_called()

    def test_run_propagates_outage_error(self, run_dispatch_setup):
        """A permanent outage propagates so the main loop can terminate."""
        bc, mock_inverter, _fake_logic = run_dispatch_setup
        mock_inverter.get_stored_energy.side_effect = \
            InverterOutageError("permanent", outage_duration_seconds=9999)

        with pytest.raises(InverterOutageError):
            bc.run()

    def test_api_set_mode_swallows_communication_error(self, run_dispatch_setup):
        """Externally triggered control tolerates a transient outage."""
        bc, mock_inverter, _fake_logic = run_dispatch_setup
        mock_inverter.set_mode_avoid_discharge.side_effect = \
            InverterCommunicationError("set_mode_avoid_discharge")

        # api_set_mode runs on a background thread; it must not raise.
        from batcontrol.core import MODE_AVOID_DISCHARGING
        bc.api_set_mode(MODE_AVOID_DISCHARGING)

    def test_run_passes_preserve_min_grid_charge_soc_to_logic(
            self, run_dispatch_setup):
        bc, _mock_inverter, fake_logic = run_dispatch_setup
        bc.preserve_min_grid_charge_soc = True
        fake_logic.get_inverter_control_settings.return_value = MagicMock(
            allow_discharge=True,
            charge_from_grid=False,
            charge_rate=0,
            limit_battery_charge_rate=-1,
        )

        bc.run()

        calc_params = fake_logic.set_calculation_parameters.call_args.args[0]
        assert calc_params.preserve_min_grid_charge_soc is True

    def _make_batcontrol_for_grid_charge_target(
            self, mock_config, mocker, prices, production, consumption):
        core_module = "batcontrol.core"
        mock_inverter = mocker.MagicMock()
        mock_inverter.max_pv_charge_rate = 3000
        mock_inverter.max_grid_charge_rate = 5000
        mock_inverter.get_max_capacity.return_value = 10240
        mock_inverter.get_SOC.return_value = 8.5
        mock_inverter.get_stored_energy.return_value = 870.4
        mock_inverter.get_stored_usable_energy.return_value = 0.0
        mock_inverter.get_free_capacity.return_value = 8243.2

        mock_tariff_provider = mocker.MagicMock()
        mock_tariff_provider.get_prices.return_value = prices
        mock_tariff_provider.refresh_data = mocker.MagicMock()

        mock_solar_provider = mocker.MagicMock()
        mock_solar_provider.get_forecast.return_value = production
        mock_solar_provider.refresh_data = mocker.MagicMock()

        mock_consumption_provider = mocker.MagicMock()
        mock_consumption_provider.get_forecast.return_value = consumption
        mock_consumption_provider.refresh_data = mocker.MagicMock()

        fake_logic = mocker.MagicMock()
        fake_logic.calculate.return_value = True
        fake_logic.get_calculation_output.return_value = mocker.MagicMock(
            reserved_energy=0,
            required_recharge_energy=0,
            min_dynamic_price_difference=0.05,
            effective_min_grid_charge_soc=None,
        )
        fake_logic.get_inverter_control_settings.return_value = MagicMock(
            allow_discharge=True,
            charge_from_grid=False,
            charge_rate=0,
            limit_battery_charge_rate=-1,
        )

        mocker.patch(
            f"{core_module}.tariff_factory.create_tarif_provider",
            autospec=True,
            return_value=mock_tariff_provider,
        )
        mocker.patch(
            f"{core_module}.inverter_factory.create_inverter",
            autospec=True,
            return_value=mock_inverter,
        )
        mocker.patch(
            f"{core_module}.solar_factory.create_solar_provider",
            autospec=True,
            return_value=mock_solar_provider,
        )
        mocker.patch(
            f"{core_module}.consumption_factory.create_consumption",
            autospec=True,
            return_value=mock_consumption_provider,
        )
        mocker.patch(
            f"{core_module}.LogicFactory.create_logic",
            autospec=True,
            return_value=fake_logic,
        )

        return Batcontrol(mock_config), fake_logic

    def test_run_reduces_horizon_to_short_consumption_forecast(
            self, mock_config, mocker):
        """A consumption forecast shorter than prices/production shortens
        the horizon instead of raising (regression: HA consumption is
        limited to 48h while prices and production reach further)."""
        bc, fake_logic = self._make_batcontrol_for_grid_charge_target(
            mock_config,
            mocker,
            prices={i: 0.3 for i in range(10)},
            production={i: 0 for i in range(10)},
            consumption={0: 500, 1: 500, 2: 500},
        )
        handle_error = mocker.patch.object(bc, 'handle_forecast_error')

        try:
            bc.run()

            handle_error.assert_not_called()
            bc.fc_consumption.get_forecast.assert_called_once_with(
                10, now=mocker.ANY)
            calc_input = fake_logic.calculate.call_args.args[0]
            assert len(calc_input.consumption) == 3
            assert len(calc_input.prices) == 3
        finally:
            bc.shutdown()

    @pytest.mark.parametrize("hours, expected_slots", [
        (0, 10),      # default: no limit
        (4, 4),
        (2.5, 3),     # a started slot is kept
        (0.01, 1),    # the current slot always stays
        (100, 10),    # longer than the forecasts: no effect
    ])
    def test_run_limits_horizon_to_max_forecast_hours(
            self, mock_config, mocker, hours, expected_slots):
        """battery_control_expert.max_forecast_hours cuts the horizon for
        the consumption request and the logic."""
        mock_config['battery_control_expert'] = {'max_forecast_hours': hours}
        bc, fake_logic = self._make_batcontrol_for_grid_charge_target(
            mock_config,
            mocker,
            prices={i: 0.3 for i in range(10)},
            production={i: 0 for i in range(10)},
            consumption={i: 500 for i in range(10)},
        )
        assert bc.time_resolution == 60
        bc.fc_consumption.get_forecast.side_effect = (
            lambda slots, now=None: {i: 500 for i in range(slots)})

        try:
            bc.run()

            bc.fc_consumption.get_forecast.assert_called_once_with(
                expected_slots, now=mocker.ANY)
            calc_input = fake_logic.calculate.call_args.args[0]
            assert len(calc_input.prices) == expected_slots
            assert len(calc_input.production) == expected_slots
            assert len(calc_input.consumption) == expected_slots
        finally:
            bc.shutdown()

    def test_max_forecast_slots_follow_the_time_resolution(
            self, mock_config, mocker):
        """48 hours are 192 slots at 15 minutes."""
        mock_config['battery_control_expert'] = {'max_forecast_hours': '48'}
        bc, _ = self._make_batcontrol_for_grid_charge_target(
            mock_config,
            mocker,
            prices={0: 0.3},
            production={0: 0},
            consumption={0: 500},
        )
        try:
            assert bc.max_forecast_hours == 48
            assert bc._max_forecast_slots() == 48
            bc.time_resolution = 15
            assert bc._max_forecast_slots() == 192
        finally:
            bc.shutdown()

    def test_run_uses_one_timestamp_per_cycle(self, mock_config, mocker):
        """Providers, the scaling of slot 0, the logic and the published run
        time all work on the same instant. Separate clock reads could land
        on different sides of a slot boundary, since the loop starts on
        full minutes."""
        bc, fake_logic = self._make_batcontrol_for_grid_charge_target(
            mock_config,
            mocker,
            prices={i: 0.3 for i in range(4)},
            production={i: 0 for i in range(4)},
            consumption={i: 500 for i in range(4)},
        )

        try:
            bc.run()

            cycle_now = bc.dynamic_tariff.get_prices.call_args.kwargs['now']
            assert isinstance(cycle_now, datetime.datetime)
            assert cycle_now.tzinfo is not None
            assert bc.fc_solar.get_forecast.call_args.kwargs['now'] is cycle_now
            assert (bc.fc_consumption.get_forecast.call_args.kwargs['now']
                    is cycle_now)
            calc_input, calc_timestamp = fake_logic.calculate.call_args.args
            assert calc_timestamp is cycle_now
            assert bc.last_run_time == cycle_now.timestamp()

            elapsed = ((cycle_now.minute % bc.time_resolution
                        + cycle_now.second / 60) / bc.time_resolution)
            assert calc_input.consumption[0] == pytest.approx(
                500 * (1 - elapsed))
        finally:
            bc.shutdown()

    def test_run_treats_empty_consumption_forecast_as_error(
            self, mock_config, mocker):
        bc, fake_logic = self._make_batcontrol_for_grid_charge_target(
            mock_config,
            mocker,
            prices={i: 0.3 for i in range(6)},
            production={i: 0 for i in range(6)},
            consumption={},
        )
        handle_error = mocker.patch.object(bc, 'handle_forecast_error')

        try:
            bc.run()

            handle_error.assert_called_once()
            fake_logic.calculate.assert_not_called()
        finally:
            bc.shutdown()

    @pytest.mark.parametrize('source', ['prices', 'production', 'consumption'])
    def test_run_treats_gap_in_forecast_as_error(
            self, mock_config, mocker, source):
        """A missing slot in provider data is a forecast error, it must not
        raise out of run() and end the process."""
        forecasts = {
            'prices': {i: 0.3 for i in range(4)},
            'production': {i: 0 for i in range(4)},
            'consumption': {i: 500 for i in range(4)},
        }
        del forecasts[source][2]
        bc, fake_logic = self._make_batcontrol_for_grid_charge_target(
            mock_config, mocker, **forecasts)
        handle_error = mocker.patch.object(bc, 'handle_forecast_error')

        try:
            bc.run()

            handle_error.assert_called_once()
            fake_logic.calculate.assert_not_called()
        finally:
            bc.shutdown()

    def test_run_falls_back_to_discharge_when_calculation_raises(
            self, run_dispatch_setup, caplog):
        """An exception inside the logic must not end the process; the
        cycle falls back to allow discharging."""
        bc, mock_inverter, fake_logic = run_dispatch_setup
        fake_logic.calculate.side_effect = ZeroDivisionError('boom')

        with caplog.at_level(logging.ERROR, logger='batcontrol.core'):
            bc.run()

        mock_inverter.set_mode_allow_discharge.assert_called_once_with()
        mock_inverter.set_mode_force_charge.assert_not_called()
        mock_inverter.set_mode_avoid_discharge.assert_not_called()
        fake_logic.get_inverter_control_settings.assert_not_called()
        assert any('Calculation raised an exception' in record.message
                   and record.exc_info for record in caplog.records)
        last_event = bc.decision_journal.last_event()
        assert last_event.trace.decisive_record().reason == 'CALCULATION_FAILED'

    def test_run_falls_back_to_discharge_when_calculation_returns_false(
            self, run_dispatch_setup):
        bc, mock_inverter, fake_logic = run_dispatch_setup
        fake_logic.calculate.return_value = False

        bc.run()

        mock_inverter.set_mode_allow_discharge.assert_called_once_with()
        fake_logic.get_inverter_control_settings.assert_not_called()

    def test_run_passes_grid_charge_target_config_to_logic(
            self, mock_config, mocker):
        mock_config['battery_control'].update({
            'min_grid_charge_soc': 0.55,
            'grid_charge_target_strategy': 'forecast',
        })
        bc, fake_logic = self._make_batcontrol_for_grid_charge_target(
            mock_config,
            mocker,
            prices={0: 0.4635, 1: 0.7018},
            production={0: 0, 1: 0},
            consumption={0: 500, 1: 5000},
        )

        try:
            bc.run()

            calc_params = fake_logic.set_calculation_parameters.call_args.args[0]
            assert calc_params.min_grid_charge_soc == 0.55
            assert calc_params.grid_charge_target.strategy == 'forecast'
        finally:
            bc.shutdown()

    def test_run_passes_fixed_grid_charge_target_by_default(
            self, mock_config, mocker):
        mock_config['battery_control']['min_grid_charge_soc'] = 0.55
        bc, fake_logic = self._make_batcontrol_for_grid_charge_target(
            mock_config,
            mocker,
            prices={0: 0.4635, 1: 0.7018, 2: 0.7018},
            production={0: 0, 1: 0, 2: 0},
            consumption={0: 500, 1: 5000, 2: 5000},
        )

        try:
            bc.run()

            calc_params = fake_logic.set_calculation_parameters.call_args.args[0]
            assert calc_params.min_grid_charge_soc == 0.55
        finally:
            bc.shutdown()

    def test_run_publishes_fixed_grid_charge_target_as_effective_by_default(
            self, mock_config, mocker):
        mock_config['battery_control']['min_grid_charge_soc'] = 0.55
        bc, fake_logic = self._make_batcontrol_for_grid_charge_target(
            mock_config,
            mocker,
            prices={0: 0.4635, 1: 0.7018, 2: 0.7018},
            production={0: 0, 1: 0, 2: 0},
            consumption={0: 500, 1: 5000, 2: 5000},
        )
        fake_logic.get_calculation_output.return_value.effective_min_grid_charge_soc = 0.55
        bc.mqtt_api = mocker.MagicMock()

        try:
            bc.run()

            bc.mqtt_api.publish_effective_min_grid_charge_soc.assert_called_once_with(
                0.55)
        finally:
            bc.shutdown()

    def test_run_skips_effective_grid_charge_target_publish_when_unset(
            self, mock_config, mocker):
        bc, _fake_logic = self._make_batcontrol_for_grid_charge_target(
            mock_config,
            mocker,
            prices={0: 0.4635, 1: 0.7018, 2: 0.7018},
            production={0: 0, 1: 0, 2: 0},
            consumption={0: 500, 1: 5000, 2: 5000},
        )
        bc.mqtt_api = mocker.MagicMock()

        try:
            bc.run()

            bc.mqtt_api.publish_effective_min_grid_charge_soc.assert_not_called()
        finally:
            bc.shutdown()

    def test_run_keeps_configured_floor_when_forecast_strategy_is_enabled(
            self, mock_config, mocker):
        mock_config['battery_control'].update({
            'min_grid_charge_soc': 0.55,
            'max_charging_from_grid_limit': 0.89,
            'grid_charge_target_strategy': 'forecast',
        })
        bc, fake_logic = self._make_batcontrol_for_grid_charge_target(
            mock_config,
            mocker,
            prices={0: 0.4635, 1: 0.7018, 2: 0.7018, 3: 0.7018,
                    4: 0.7018, 5: 0.4635},
            production={0: 149, 1: 569, 2: 1488, 3: 2678, 4: 3500, 5: 4000},
            consumption={0: 547, 1: 731, 2: 3427, 3: 3497, 4: 3700, 5: 500},
        )

        try:
            bc.run()

            calc_params = fake_logic.set_calculation_parameters.call_args.args[0]
            assert calc_params.min_grid_charge_soc == 0.55
            assert calc_params.grid_charge_target.strategy == 'forecast'
        finally:
            bc.shutdown()

    def test_run_publishes_effective_grid_charge_target(
            self, mock_config, mocker):
        mock_config['battery_control'].update({
            'min_grid_charge_soc': 0.55,
            'max_charging_from_grid_limit': 0.89,
            'grid_charge_target_strategy': 'forecast',
        })
        bc, fake_logic = self._make_batcontrol_for_grid_charge_target(
            mock_config,
            mocker,
            prices={0: 0.4635, 1: 0.7018, 2: 0.7018, 3: 0.7018,
                    4: 0.7018, 5: 0.4635},
            production={0: 149, 1: 569, 2: 1488, 3: 2678, 4: 3500, 5: 4000},
            consumption={0: 547, 1: 731, 2: 3427, 3: 3497, 4: 3700, 5: 500},
        )
        fake_logic.get_calculation_output.return_value.effective_min_grid_charge_soc = 0.79
        bc.mqtt_api = mocker.MagicMock()

        try:
            bc.run()

            published_target = (
                bc.mqtt_api.publish_effective_min_grid_charge_soc.call_args.args[0]
            )
            assert published_target == pytest.approx(0.79, abs=0.01)
        finally:
            bc.shutdown()

    def test_run_dispatches_force_charge(self, run_dispatch_setup):
        bc, mock_inverter, fake_logic = run_dispatch_setup
        fake_logic.get_inverter_control_settings.return_value = MagicMock(
            allow_discharge=False,
            charge_from_grid=True,
            charge_rate=2345,
            limit_battery_charge_rate=-1,
        )

        bc.run()

        mock_inverter.set_mode_force_charge.assert_called_once_with(2345)
        mock_inverter.set_mode_allow_discharge.assert_not_called()
        mock_inverter.set_mode_avoid_discharge.assert_not_called()
        mock_inverter.set_mode_limit_battery_charge.assert_not_called()

    def test_run_dispatches_avoid_discharge(self, run_dispatch_setup):
        bc, mock_inverter, fake_logic = run_dispatch_setup
        fake_logic.get_inverter_control_settings.return_value = MagicMock(
            allow_discharge=False,
            charge_from_grid=False,
            charge_rate=0,
            limit_battery_charge_rate=-1,
        )

        bc.run()

        mock_inverter.set_mode_avoid_discharge.assert_called_once_with()
        mock_inverter.set_mode_allow_discharge.assert_not_called()
        mock_inverter.set_mode_force_charge.assert_not_called()
        mock_inverter.set_mode_limit_battery_charge.assert_not_called()

    def test_run_dispatches_limit_battery_charge_rate(self, run_dispatch_setup):
        bc, mock_inverter, fake_logic = run_dispatch_setup
        fake_logic.get_inverter_control_settings.return_value = MagicMock(
            allow_discharge=True,
            charge_from_grid=False,
            charge_rate=0,
            limit_battery_charge_rate=1800,
        )

        bc.run()

        mock_inverter.set_mode_limit_battery_charge.assert_called_once_with(1800)
        mock_inverter.set_mode_allow_discharge.assert_not_called()
        mock_inverter.set_mode_force_charge.assert_not_called()
        mock_inverter.set_mode_avoid_discharge.assert_not_called()

    def test_run_publishes_optimizer_control_source(self, run_dispatch_setup):
        bc, _mock_inverter, fake_logic = run_dispatch_setup
        bc.mqtt_api = MagicMock()
        fake_logic.get_inverter_control_settings.return_value = MagicMock(
            allow_discharge=True,
            charge_from_grid=False,
            charge_rate=0,
            limit_battery_charge_rate=-1,
        )

        bc.run()

        assert bc.last_control_source == CONTROL_SOURCE_OPTIMIZER
        bc.mqtt_api.publish_control_source.assert_called_with(CONTROL_SOURCE_OPTIMIZER)


class TestApiOverrideMqttState:
    """API override state should be observable via MQTT."""

    @pytest.fixture
    def mock_config(self):
        return {
            'timezone': 'Europe/Berlin',
            'time_resolution_minutes': 60,
            'inverter': {
                'type': 'dummy',
                'max_grid_charge_rate': 5000,
                'max_pv_charge_rate': 3000,
                'min_pv_charge_rate': 0,
            },
            'utility': {
                'type': 'tibber',
                'apikey': 'test_token'
            },
            'pvinstallations': [],
            'consumption_forecast': {
                'type': 'simple',
                'value': 500
            },
            'battery_control': {
                'max_charging_from_grid_limit': 0.8,
                'min_price_difference': 0.05
            },
            'mqtt': {'enabled': False}
        }

    @pytest.fixture
    def run_dispatch_setup(self, mock_config, mocker):
        core_module = "batcontrol.core"

        mock_inverter = mocker.MagicMock()
        mock_inverter.max_pv_charge_rate = 3000
        mock_inverter.max_grid_charge_rate = 5000
        mock_inverter.get_max_capacity.return_value = 10000
        mock_inverter.get_SOC.return_value = 50
        mock_inverter.get_stored_energy.return_value = 5000
        mock_inverter.get_stored_usable_energy.return_value = 4500
        mock_inverter.get_free_capacity.return_value = 5000

        mock_tariff_provider = mocker.MagicMock()
        mock_tariff_provider.get_prices.return_value = {0: 0.20, 1: 0.30, 2: 0.25}
        mock_tariff_provider.refresh_data = mocker.MagicMock()

        mock_solar_provider = mocker.MagicMock()
        mock_solar_provider.get_forecast.return_value = {0: 0, 1: 0, 2: 0}
        mock_solar_provider.refresh_data = mocker.MagicMock()

        mock_consumption_provider = mocker.MagicMock()
        mock_consumption_provider.get_forecast.return_value = {0: 500, 1: 500, 2: 500}
        mock_consumption_provider.refresh_data = mocker.MagicMock()

        fake_logic = mocker.MagicMock()
        fake_logic.calculate.return_value = True
        fake_logic.get_calculation_output.return_value = mocker.MagicMock(
            reserved_energy=0,
            required_recharge_energy=0,
            min_dynamic_price_difference=0.05,
        )
        fake_logic.get_inverter_control_settings.return_value = mocker.MagicMock(
            allow_discharge=True,
            charge_from_grid=False,
            charge_rate=0,
            limit_battery_charge_rate=-1,
        )

        mocker.patch(
            f"{core_module}.tariff_factory.create_tarif_provider",
            autospec=True,
            return_value=mock_tariff_provider,
        )
        mocker.patch(
            f"{core_module}.inverter_factory.create_inverter",
            autospec=True,
            return_value=mock_inverter,
        )
        mocker.patch(
            f"{core_module}.solar_factory.create_solar_provider",
            autospec=True,
            return_value=mock_solar_provider,
        )
        mocker.patch(
            f"{core_module}.consumption_factory.create_consumption",
            autospec=True,
            return_value=mock_consumption_provider,
        )
        mocker.patch(
            f"{core_module}.LogicFactory.create_logic",
            autospec=True,
            return_value=fake_logic,
        )

        bc = Batcontrol(mock_config)

        yield bc, mock_inverter, fake_logic

        bc.shutdown()

    def test_api_set_mode_publishes_override_active(self, run_dispatch_setup):
        bc, _mock_inverter, _fake_logic = run_dispatch_setup
        bc.mqtt_api = MagicMock()

        bc.api_set_mode(MODE_ALLOW_DISCHARGING)

        bc.mqtt_api.publish_api_override_active.assert_called_once_with(True)

    def test_api_set_charge_rate_publishes_override_active(self, run_dispatch_setup):
        bc, _mock_inverter, _fake_logic = run_dispatch_setup
        bc.mqtt_api = MagicMock()

        bc.api_set_charge_rate(1200)

        bc.mqtt_api.publish_api_override_active.assert_called_once_with(True)

    def test_api_set_mode_publishes_api_control_source(self, run_dispatch_setup):
        bc, _mock_inverter, _fake_logic = run_dispatch_setup
        bc.mqtt_api = MagicMock()

        bc.api_set_mode(MODE_ALLOW_DISCHARGING)

        assert bc.last_control_source == CONTROL_SOURCE_API
        bc.mqtt_api.publish_control_source.assert_called_once_with(CONTROL_SOURCE_API)

    def test_api_set_charge_rate_publishes_api_control_source(self, run_dispatch_setup):
        bc, _mock_inverter, _fake_logic = run_dispatch_setup
        bc.mqtt_api = MagicMock()

        bc.api_set_charge_rate(1200)

        assert bc.last_control_source == CONTROL_SOURCE_API
        bc.mqtt_api.publish_control_source.assert_called_once_with(CONTROL_SOURCE_API)

    def test_refresh_static_values_does_not_republish_control_source(self, run_dispatch_setup):
        bc, _mock_inverter, _fake_logic = run_dispatch_setup
        bc.mqtt_api = MagicMock()
        bc.last_control_source = CONTROL_SOURCE_API

        bc.refresh_static_values()

        bc.mqtt_api.publish_control_source.assert_not_called()

    def test_api_set_mode_does_not_republish_unchanged_control_source(
            self, run_dispatch_setup):
        bc, _mock_inverter, _fake_logic = run_dispatch_setup
        bc.mqtt_api = MagicMock()
        bc.last_mode = MODE_ALLOW_DISCHARGING
        bc.last_control_source = CONTROL_SOURCE_API

        bc.api_set_mode(MODE_ALLOW_DISCHARGING)

        bc.mqtt_api.publish_control_source.assert_not_called()

    def test_run_clears_override_and_publishes_inactive_state(self, run_dispatch_setup):
        bc, _mock_inverter, _fake_logic = run_dispatch_setup
        bc.mqtt_api = MagicMock()
        bc.api_overwrite = True

        bc.run()

        assert bc.api_overwrite is False
        assert bc.mqtt_api.publish_api_override_active.call_args_list[-1] == call(False)

    def test_api_set_min_price_difference_publishes_immediately(self, run_dispatch_setup):
        bc, _mock_inverter, _fake_logic = run_dispatch_setup
        bc.mqtt_api = MagicMock()

        bc.api_set_min_price_difference(0.075)

        assert bc.min_price_difference == 0.075
        bc.mqtt_api.publish_min_price_difference.assert_called_once_with(0.075)

    def test_api_set_min_price_difference_rel_publishes_immediately(self, run_dispatch_setup):
        bc, _mock_inverter, _fake_logic = run_dispatch_setup
        bc.mqtt_api = MagicMock()

        bc.api_set_min_price_difference_rel(0.15)

        assert bc.min_price_difference_rel == 0.15
        bc.mqtt_api.publish_min_price_difference_rel.assert_called_once_with(0.15)

    def test_refresh_static_values_publishes_current_control_state(self, run_dispatch_setup):
        bc, _mock_inverter, _fake_logic = run_dispatch_setup
        bc.mqtt_api = MagicMock()
        bc.last_mode = MODE_LIMIT_BATTERY_CHARGE_RATE
        bc.last_charge_rate = 1200
        bc._limit_battery_charge_rate = 1800
        bc.api_overwrite = True

        bc.refresh_static_values()

        bc.mqtt_api.publish_mode.assert_called_once_with(MODE_LIMIT_BATTERY_CHARGE_RATE)
        bc.mqtt_api.publish_charge_rate.assert_called_once_with(1200)
        bc.mqtt_api.publish_limit_battery_charge_rate.assert_called_once_with(1800)
        bc.mqtt_api.publish_api_override_active.assert_called_once_with(True)

    def test_refresh_static_values_publishes_min_grid_charge_soc_when_set(
            self, run_dispatch_setup):
        bc, _mock_inverter, _fake_logic = run_dispatch_setup
        bc.mqtt_api = MagicMock()
        bc.min_grid_charge_soc = 0.55

        bc.refresh_static_values()

        bc.mqtt_api.publish_min_grid_charge_soc.assert_called_once_with(0.55)

    def test_refresh_static_values_skips_min_grid_charge_soc_when_unset(
            self, run_dispatch_setup):
        bc, _mock_inverter, _fake_logic = run_dispatch_setup
        bc.mqtt_api = MagicMock()
        bc.min_grid_charge_soc = None

        bc.refresh_static_values()

        bc.mqtt_api.publish_min_grid_charge_soc.assert_not_called()

    def test_refresh_static_values_skips_unknown_mode(self, run_dispatch_setup):
        bc, _mock_inverter, _fake_logic = run_dispatch_setup
        bc.mqtt_api = MagicMock()
        bc.last_mode = None

        bc.refresh_static_values()

        bc.mqtt_api.publish_mode.assert_not_called()


class TestEvccPeakShavingGuard:
    """Test evcc peak shaving guard in core.py run loop."""

    @pytest.fixture
    def mock_config(self):
        """Provide a minimal config for testing."""
        return {
            'timezone': 'Europe/Berlin',
            'time_resolution_minutes': 60,
            'inverter': {
                'type': 'dummy',
                'max_grid_charge_rate': 5000,
                'max_pv_charge_rate': 3000,
                'min_pv_charge_rate': 100
            },
            'utility': {
                'type': 'tibber',
                'apikey': 'test_token'
            },
            'pvinstallations': [],
            'consumption_forecast': {
                'type': 'simple',
                'value': 500
            },
            'battery_control': {
                'max_charging_from_grid_limit': 0.8,
                'min_price_difference': 0.05
            },
            'peak_shaving': {
                'enabled': True,
                'allow_full_battery_after': 14
            },
            'mqtt': {
                'enabled': False
            }
        }

    def _create_bc(self, mock_config, mock_inverter_factory, mock_tariff,
                   mock_solar, mock_consumption):
        """Helper to create a Batcontrol with mocked dependencies."""
        mock_inverter = MagicMock()
        mock_inverter.max_pv_charge_rate = 3000
        mock_inverter.set_mode_limit_battery_charge = MagicMock()
        mock_inverter.get_max_capacity = MagicMock(return_value=10000)
        mock_inverter_factory.return_value = mock_inverter

        mock_tariff.return_value = MagicMock()
        mock_solar.return_value = MagicMock()
        mock_consumption.return_value = MagicMock()

        bc = Batcontrol(mock_config)
        return bc

    @patch('batcontrol.core.tariff_factory.create_tarif_provider')
    @patch('batcontrol.core.inverter_factory.create_inverter')
    @patch('batcontrol.core.solar_factory.create_solar_provider')
    @patch('batcontrol.core.consumption_factory.create_consumption')
    def test_evcc_charging_disables_peak_shaving_in_calc_params(
        self, mock_consumption, mock_solar, mock_inverter_factory, mock_tariff,
        mock_config):
        """When evcc is actively charging, peak_shaving_enabled is False in calc_params."""
        bc = self._create_bc(mock_config, mock_inverter_factory, mock_tariff,
                             mock_solar, mock_consumption)

        mock_evcc = MagicMock()
        mock_evcc.evcc_is_charging = True
        mock_evcc.evcc_ev_expects_pv_surplus = False
        bc.evcc_api = mock_evcc

        # Replicate the pre-calculation evcc check from core.py
        from batcontrol.logic.logic_interface import (
            CalculationParameters, PeakShavingConfig)
        evcc_disable_peak_shaving = (
            bc.evcc_api.evcc_is_charging or
            bc.evcc_api.evcc_ev_expects_pv_surplus
        )
        peak_shaving_config = mock_config.get('peak_shaving', {})
        calc_params = CalculationParameters(
            max_charging_from_grid_limit=0.8,
            min_price_difference=0.05,
            min_price_difference_rel=0.0,
            max_capacity=10000,
            peak_shaving=PeakShavingConfig(
                enabled=peak_shaving_config.get('enabled', False)
                and not evcc_disable_peak_shaving),
        )

        assert calc_params.peak_shaving.enabled is False

    @patch('batcontrol.core.tariff_factory.create_tarif_provider')
    @patch('batcontrol.core.inverter_factory.create_inverter')
    @patch('batcontrol.core.solar_factory.create_solar_provider')
    @patch('batcontrol.core.consumption_factory.create_consumption')
    def test_evcc_pv_mode_disables_peak_shaving_in_calc_params(
        self, mock_consumption, mock_solar, mock_inverter_factory, mock_tariff,
        mock_config):
        """When EV connected in PV mode, peak_shaving_enabled is False in calc_params."""
        bc = self._create_bc(mock_config, mock_inverter_factory, mock_tariff,
                             mock_solar, mock_consumption)

        mock_evcc = MagicMock()
        mock_evcc.evcc_is_charging = False
        mock_evcc.evcc_ev_expects_pv_surplus = True
        bc.evcc_api = mock_evcc

        from batcontrol.logic.logic_interface import (
            CalculationParameters, PeakShavingConfig)
        evcc_disable_peak_shaving = (
            bc.evcc_api.evcc_is_charging or
            bc.evcc_api.evcc_ev_expects_pv_surplus
        )
        peak_shaving_config = mock_config.get('peak_shaving', {})
        calc_params = CalculationParameters(
            max_charging_from_grid_limit=0.8,
            min_price_difference=0.05,
            min_price_difference_rel=0.0,
            max_capacity=10000,
            peak_shaving=PeakShavingConfig(
                enabled=peak_shaving_config.get('enabled', False)
                and not evcc_disable_peak_shaving),
        )

        assert calc_params.peak_shaving.enabled is False

    @patch('batcontrol.core.tariff_factory.create_tarif_provider')
    @patch('batcontrol.core.inverter_factory.create_inverter')
    @patch('batcontrol.core.solar_factory.create_solar_provider')
    @patch('batcontrol.core.consumption_factory.create_consumption')
    def test_evcc_not_active_keeps_peak_shaving_enabled(
        self, mock_consumption, mock_solar, mock_inverter_factory, mock_tariff,
        mock_config):
        """When evcc is not charging and no PV mode, peak_shaving_enabled stays True."""
        bc = self._create_bc(mock_config, mock_inverter_factory, mock_tariff,
                             mock_solar, mock_consumption)

        mock_evcc = MagicMock()
        mock_evcc.evcc_is_charging = False
        mock_evcc.evcc_ev_expects_pv_surplus = False
        bc.evcc_api = mock_evcc

        from batcontrol.logic.logic_interface import (
            CalculationParameters, PeakShavingConfig)
        evcc_disable_peak_shaving = (
            bc.evcc_api.evcc_is_charging or
            bc.evcc_api.evcc_ev_expects_pv_surplus
        )
        peak_shaving_config = mock_config.get('peak_shaving', {})
        calc_params = CalculationParameters(
            max_charging_from_grid_limit=0.8,
            min_price_difference=0.05,
            min_price_difference_rel=0.0,
            max_capacity=10000,
            peak_shaving=PeakShavingConfig(
                enabled=peak_shaving_config.get('enabled', False)
                and not evcc_disable_peak_shaving),
        )

        assert calc_params.peak_shaving.enabled is True

    @patch('batcontrol.core.tariff_factory.create_tarif_provider')
    @patch('batcontrol.core.inverter_factory.create_inverter')
    @patch('batcontrol.core.solar_factory.create_solar_provider')
    @patch('batcontrol.core.consumption_factory.create_consumption')
    def test_evcc_no_limit_active_no_change(
        self, mock_consumption, mock_solar, mock_inverter_factory, mock_tariff,
        mock_config):
        """When evcc is charging but peak shaving was off in config, it stays disabled."""
        mock_config = dict(mock_config)
        mock_config['peak_shaving'] = {'enabled': False, 'allow_full_battery_after': 14}
        bc = self._create_bc(mock_config, mock_inverter_factory, mock_tariff,
                             mock_solar, mock_consumption)

        mock_evcc = MagicMock()
        mock_evcc.evcc_is_charging = True
        mock_evcc.evcc_ev_expects_pv_surplus = False
        bc.evcc_api = mock_evcc

        from batcontrol.logic.logic_interface import (
            CalculationParameters, PeakShavingConfig)
        evcc_disable_peak_shaving = (
            bc.evcc_api.evcc_is_charging or
            bc.evcc_api.evcc_ev_expects_pv_surplus
        )
        peak_shaving_config = mock_config.get('peak_shaving', {})
        calc_params = CalculationParameters(
            max_charging_from_grid_limit=0.8,
            min_price_difference=0.05,
            min_price_difference_rel=0.0,
            max_capacity=10000,
            peak_shaving=PeakShavingConfig(
                enabled=peak_shaving_config.get('enabled', False)
                and not evcc_disable_peak_shaving),
        )

        assert calc_params.peak_shaving.enabled is False


class TestMarketPriceRefresh:
    """Tests for the configurable market price hard refresh (issue #366)."""

    BASE_CONFIG = {
        'timezone': 'Europe/Berlin',
        'time_resolution_minutes': 60,
        'inverter': {
            'type': 'dummy',
            'max_grid_charge_rate': 5000,
            'max_pv_charge_rate': 3000,
            'min_pv_charge_rate': 0,
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

    def _patch_core(self, mocker):
        mock_inverter = mocker.MagicMock()
        mock_inverter.max_pv_charge_rate = 3000
        mock_inverter.get_max_capacity.return_value = 10000
        mocker.patch('batcontrol.core.tariff_factory.create_tarif_provider',
                     autospec=True, return_value=mocker.MagicMock())
        mocker.patch('batcontrol.core.inverter_factory.create_inverter',
                     autospec=True, return_value=mock_inverter)
        mocker.patch('batcontrol.core.solar_factory.create_solar_provider',
                     autospec=True, return_value=mocker.MagicMock())
        mocker.patch('batcontrol.core.consumption_factory.create_consumption',
                     autospec=True, return_value=mocker.MagicMock())

    def test_default_market_price_refresh_time(self, mocker):
        """Without expert config, market_price_refresh_time defaults to 12:30."""
        self._patch_core(mocker)
        config = dict(self.BASE_CONFIG)
        bc = Batcontrol(config)
        assert bc.market_price_refresh_time == "12:30"
        bc.shutdown()

    def test_custom_market_price_refresh_time_from_expert_config(self, mocker):
        """market_price_refresh_time from battery_control_expert is used."""
        self._patch_core(mocker)
        config = dict(self.BASE_CONFIG)
        config['battery_control_expert'] = {'market_price_refresh_time': '13:00'}
        bc = Batcontrol(config)
        assert bc.market_price_refresh_time == "13:00"
        bc.shutdown()

    def test_hard_refresh_prices_calls_force_refresh(self, mocker):
        """_hard_refresh_prices() must call dynamic_tariff.refresh_data(force=True)."""
        self._patch_core(mocker)
        config = dict(self.BASE_CONFIG)
        bc = Batcontrol(config)

        mock_refresh = mocker.MagicMock()
        bc.dynamic_tariff.refresh_data = mock_refresh

        bc._hard_refresh_prices()

        mock_refresh.assert_called_once_with(force=True)
        bc.shutdown()

    @pytest.mark.parametrize("invalid_value", ["25:99", "noon", "12-30", "", 1230])
    def test_invalid_market_price_refresh_time_raises(self, mocker, invalid_value):
        """Invalid market_price_refresh_time must raise ValueError at init."""
        self._patch_core(mocker)
        config = dict(self.BASE_CONFIG)
        config['battery_control_expert'] = {
            'market_price_refresh_time': invalid_value
        }
        with pytest.raises(ValueError, match="market_price_refresh_time"):
            Batcontrol(config)

    @pytest.mark.parametrize("valid_value", ["12:30", "00:00", "23:59", "13:00"])
    def test_valid_market_price_refresh_time_accepted(self, mocker, valid_value):
        """Valid HH:MM values must be accepted without error."""
        self._patch_core(mocker)
        config = dict(self.BASE_CONFIG)
        config['battery_control_expert'] = {
            'market_price_refresh_time': valid_value
        }
        bc = Batcontrol(config)
        assert bc.market_price_refresh_time == valid_value
        bc.shutdown()

    def test_shutdown_clears_scheduled_jobs(self, mocker):
        """shutdown() must clear the job registry so multiple instantiations do not accumulate jobs."""
        from batcontrol import scheduler as sched_module
        self._patch_core(mocker)
        sched_module.reset_scheduler()

        bc = Batcontrol(dict(self.BASE_CONFIG))
        jobs_after_init = len(sched_module.get_jobs())
        assert jobs_after_init > 0

        bc.shutdown()

        assert sched_module.get_jobs() == []


class TestChargeRateMultiplierWiring:
    """battery_control_expert.charge_rate_multiplier must reach CommonLogic (issue #424)."""

    # Shared with TestMarketPriceRefresh, which exercises the same
    # battery_control_expert-driven Batcontrol.__init__() wiring.
    BASE_CONFIG = TestMarketPriceRefresh.BASE_CONFIG

    def _patch_core(self, mocker):
        mock_inverter = mocker.MagicMock()
        mock_inverter.max_pv_charge_rate = 3000
        mock_inverter.get_max_capacity.return_value = 10000
        mocker.patch('batcontrol.core.tariff_factory.create_tarif_provider',
                     autospec=True, return_value=mocker.MagicMock())
        mocker.patch('batcontrol.core.inverter_factory.create_inverter',
                     autospec=True, return_value=mock_inverter)
        mocker.patch('batcontrol.core.solar_factory.create_solar_provider',
                     autospec=True, return_value=mocker.MagicMock())
        mocker.patch('batcontrol.core.consumption_factory.create_consumption',
                     autospec=True, return_value=mocker.MagicMock())

    def setup_method(self):
        # CommonLogic is a singleton; reset it so each test observes the
        # multiplier from its own Batcontrol(config) call.
        CommonLogic._instance = None

    def teardown_method(self):
        CommonLogic._instance = None

    def test_default_charge_rate_multiplier(self, mocker):
        """Without expert config, the CommonLogic default of 1.1 is used."""
        self._patch_core(mocker)
        bc = Batcontrol(dict(self.BASE_CONFIG))
        assert bc.general_logic.charge_rate_multiplier == 1.1
        assert bc.general_logic.calculate_charge_rate(1000) == 1100
        bc.shutdown()

    def test_charge_rate_multiplier_from_expert_config(self, mocker):
        """battery_control_expert.charge_rate_multiplier reaches CommonLogic
        and is applied by calculate_charge_rate()."""
        self._patch_core(mocker)
        config = dict(self.BASE_CONFIG)
        config['battery_control_expert'] = {'charge_rate_multiplier': 1.25}
        bc = Batcontrol(config)
        assert bc.general_logic.charge_rate_multiplier == 1.25
        assert bc.general_logic.calculate_charge_rate(1000) == 1250
        bc.shutdown()

    def test_legacy_battery_control_location_is_mapped_with_warning(self, mocker, caplog):
        """The undocumented battery_control.charge_rate_multiplier still
        applies (for migration) but logs a deprecation warning."""
        self._patch_core(mocker)
        config = dict(self.BASE_CONFIG)
        config['battery_control'] = dict(
            self.BASE_CONFIG['battery_control'], charge_rate_multiplier=1.3)
        with caplog.at_level(logging.WARNING):
            bc = Batcontrol(config)
        assert bc.general_logic.charge_rate_multiplier == 1.3
        assert any('battery_control.charge_rate_multiplier is deprecated' in msg
                   for msg in caplog.messages)
        bc.shutdown()

    def test_expert_config_takes_priority_over_legacy_location(self, mocker):
        """If both locations are set, battery_control_expert wins."""
        self._patch_core(mocker)
        config = dict(self.BASE_CONFIG)
        config['battery_control'] = dict(
            self.BASE_CONFIG['battery_control'], charge_rate_multiplier=1.3)
        config['battery_control_expert'] = {'charge_rate_multiplier': 1.25}
        bc = Batcontrol(config)
        assert bc.general_logic.charge_rate_multiplier == 1.25
        bc.shutdown()

    def test_invalid_legacy_value_ignored_when_expert_config_takes_priority(
            self, mocker, caplog):
        """An invalid deprecated battery_control value must not block
        startup when battery_control_expert (which wins) is valid; it
        should only be reported as ignored, not parsed/validated."""
        self._patch_core(mocker)
        config = dict(self.BASE_CONFIG)
        config['battery_control'] = dict(
            self.BASE_CONFIG['battery_control'], charge_rate_multiplier="not-a-number")
        config['battery_control_expert'] = {'charge_rate_multiplier': 1.25}
        with caplog.at_level(logging.WARNING):
            bc = Batcontrol(config)
        assert bc.general_logic.charge_rate_multiplier == 1.25
        assert any('battery_control.charge_rate_multiplier is deprecated' in msg
                   and 'ignored' in msg
                   for msg in caplog.messages)
        bc.shutdown()

    @pytest.mark.parametrize("invalid_value", [
        None, "fast", "", True, 0, -1.1,
        float("nan"), float("inf"), float("-inf"),
    ])
    def test_invalid_expert_charge_rate_multiplier_raises(self, mocker, invalid_value):
        """A non-positive or non-numeric expert value must raise ValueError
        at init instead of failing later inside calculate_charge_rate()."""
        self._patch_core(mocker)
        config = dict(self.BASE_CONFIG)
        config['battery_control_expert'] = {
            'charge_rate_multiplier': invalid_value}
        with pytest.raises(ValueError, match="charge_rate_multiplier"):
            Batcontrol(config)

    @pytest.mark.parametrize("invalid_value", ["fast", True, 0, -1.1])
    def test_invalid_legacy_charge_rate_multiplier_raises(self, mocker, invalid_value):
        """Same validation applies to the deprecated battery_control location."""
        self._patch_core(mocker)
        config = dict(self.BASE_CONFIG)
        config['battery_control'] = dict(
            self.BASE_CONFIG['battery_control'],
            charge_rate_multiplier=invalid_value)
        with pytest.raises(ValueError, match="charge_rate_multiplier"):
            Batcontrol(config)

    @pytest.mark.parametrize("valid_value", [1, 1.5, "1.2"])
    def test_valid_charge_rate_multiplier_accepted(self, mocker, valid_value):
        """Ints and numeric strings are coerced to float, matching the
        other battery_control_expert numeric parsers."""
        self._patch_core(mocker)
        config = dict(self.BASE_CONFIG)
        config['battery_control_expert'] = {
            'charge_rate_multiplier': valid_value}
        bc = Batcontrol(config)
        assert bc.general_logic.charge_rate_multiplier == float(valid_value)
        bc.shutdown()

    @pytest.mark.parametrize("invalid_value", ["not-a-mapping", ["list"], 1])
    def test_non_dict_battery_control_expert_raises(self, mocker, invalid_value):
        """battery_control_expert must be a mapping; a non-dict value must
        raise a clear ValueError instead of an AttributeError deep inside
        the config parsing."""
        self._patch_core(mocker)
        config = dict(self.BASE_CONFIG)
        config['battery_control_expert'] = invalid_value
        with pytest.raises(ValueError, match="battery_control_expert"):
            Batcontrol(config)


class TestExpertConfigValidation:
    """Invalid battery_control_expert values stop batcontrol at startup, with
    a message naming the key - not later inside a grid recharge calculation."""

    BASE_CONFIG = TestMarketPriceRefresh.BASE_CONFIG
    _patch_core = TestChargeRateMultiplierWiring._patch_core

    def setup_method(self):
        CommonLogic._instance = None

    def teardown_method(self):
        CommonLogic._instance = None

    @pytest.mark.parametrize("key, invalid_value", [
        ('soften_price_difference_on_charging_factor', 0),
        ('soften_price_difference_on_charging_factor', -5),
        ('soften_price_difference_on_charging_factor', 'five'),
        ('soften_price_difference_on_charging_factor', None),
        ('soften_price_difference_on_charging', 'maybe'),
        ('soften_price_difference_on_charging', 2),
        ('spread_grid_charge_over_charge_window', 'sometimes'),
        ('round_price_digits', 2.5),
        ('round_price_digits', 'four'),
        ('round_price_digits', True),
        ('production_offset_percent', -0.1),
        ('production_offset_percent', 'snow'),
        ('max_forecast_hours', -1),
        ('max_forecast_hours', 'two days'),
        ('max_forecast_hours', True),
        ('preserve_min_grid_charge_soc', 'perhaps'),
    ])
    def test_invalid_expert_value_raises_at_startup(
            self, mocker, key, invalid_value):
        self._patch_core(mocker)
        config = dict(self.BASE_CONFIG)
        config['battery_control_expert'] = {key: invalid_value}
        with pytest.raises(
                ValueError, match=f"battery_control_expert.{key}") as excinfo:
            Batcontrol(config)
        assert repr(invalid_value) in str(excinfo.value) \
            or type(invalid_value).__name__ in str(excinfo.value)

    def test_string_values_of_a_form_field_are_parsed(self, mocker):
        """"false" must not be treated as enabled, "2" must be usable as a
        number of digits."""
        self._patch_core(mocker)
        config = dict(self.BASE_CONFIG)
        config['battery_control_expert'] = {
            'soften_price_difference_on_charging': 'false',
            'soften_price_difference_on_charging_factor': '5',
            'spread_grid_charge_over_charge_window': 'False',
            'round_price_digits': '2',
            'production_offset_percent': '0.8',
            'preserve_min_grid_charge_soc': 'true',
        }
        bc = Batcontrol(config)
        try:
            assert bc.round_price_digits == 2
            assert bc.production_offset_percent == 0.8
            assert bc.preserve_min_grid_charge_soc is True
            logic = LogicFactory.create_logic(
                60, config, datetime.timezone.utc)
            assert logic.soften_price_difference_on_charging is False
            assert logic.soften_price_difference_on_charging_factor == 5.0
            assert logic.spread_grid_charge_over_charge_window is False
            assert logic.round_price_digits == 2
        finally:
            bc.shutdown()

    def test_template_values_are_accepted(self, mocker):
        """The values of config/batcontrol_config_dummy.yaml stay valid."""
        self._patch_core(mocker)
        config = dict(self.BASE_CONFIG)
        config['battery_control_expert'] = {
            'charge_rate_multiplier': 1.1,
            'soften_price_difference_on_charging': False,
            'soften_price_difference_on_charging_factor': 5,
            'spread_grid_charge_over_charge_window': True,
            'round_price_digits': 4,
            'production_offset_percent': 1.0,
            'preserve_min_grid_charge_soc': False,
            'max_forecast_hours': 0,
        }
        bc = Batcontrol(config)
        bc.shutdown()

    @pytest.mark.parametrize("rate, expected", [
        (5000, 5000.0), (3600.5, 3600.5), (0, 0.0), (-1, 0.0),
        (None, 0.0), (True, 0.0), ('5000', 0.0),
    ])
    def test_known_max_grid_charge_rate(self, mocker, rate, expected):
        """Only a real positive number is handed to the logic as the maximum
        grid charge rate; anything else counts as unknown (0)."""
        self._patch_core(mocker)
        bc = Batcontrol(dict(self.BASE_CONFIG))
        try:
            bc.inverter.max_grid_charge_rate = rate
            assert bc._known_max_grid_charge_rate() == expected
        finally:
            bc.shutdown()

    def test_mock_inverter_rate_counts_as_unknown(self, mocker):
        self._patch_core(mocker)
        bc = Batcontrol(dict(self.BASE_CONFIG))
        try:
            assert bc._known_max_grid_charge_rate() == 0.0
        finally:
            bc.shutdown()


class TestApiSetGridChargeLock:
    """External grid-charge lock signal (e.g. HEMS/grid operator, section 14a EnWG)."""

    BASE_CONFIG = {
        'timezone': 'Europe/Berlin',
        'time_resolution_minutes': 60,
        'inverter': {
            'type': 'dummy',
            'max_grid_charge_rate': 5000,
            'max_pv_charge_rate': 3000,
            'min_pv_charge_rate': 0,
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

    @pytest.fixture
    def bc(self, mocker):
        mock_inverter = mocker.MagicMock()
        mock_inverter.max_pv_charge_rate = 3000
        mock_inverter.max_grid_charge_rate = 5000
        mock_inverter.get_max_capacity.return_value = 10000
        mocker.patch('batcontrol.core.tariff_factory.create_tarif_provider',
                     autospec=True, return_value=mocker.MagicMock())
        mocker.patch('batcontrol.core.inverter_factory.create_inverter',
                     autospec=True, return_value=mock_inverter)
        mocker.patch('batcontrol.core.solar_factory.create_solar_provider',
                     autospec=True, return_value=mocker.MagicMock())
        mocker.patch('batcontrol.core.consumption_factory.create_consumption',
                     autospec=True, return_value=mocker.MagicMock())
        instance = Batcontrol(dict(self.BASE_CONFIG))
        yield instance
        instance.shutdown()

    def test_lock_zeroes_grid_charge_limit_and_remembers_previous(self, bc):
        bc.mqtt_api = MagicMock()

        bc.api_set_grid_charge_lock(True)

        assert bc.max_charging_from_grid_limit == 0.0
        assert bc._grid_charge_locked is True
        assert bc._pre_lock_max_charging_from_grid_limit == 0.8
        bc.mqtt_api.publish_grid_charge_locked.assert_called_once_with(True)

    def test_unlock_restores_previous_grid_charge_limit(self, bc):
        bc.mqtt_api = MagicMock()
        bc.api_set_grid_charge_lock(True)

        bc.api_set_grid_charge_lock(False)

        assert bc.max_charging_from_grid_limit == 0.8
        assert bc._grid_charge_locked is False
        assert bc._pre_lock_max_charging_from_grid_limit is None
        bc.mqtt_api.publish_grid_charge_locked.assert_called_with(False)

    def test_unlock_without_prior_lock_is_a_noop(self, bc):
        bc.mqtt_api = MagicMock()

        bc.api_set_grid_charge_lock(False)

        assert bc.max_charging_from_grid_limit == 0.8
        bc.mqtt_api.publish_grid_charge_locked.assert_not_called()

    def test_repeated_lock_is_idempotent(self, bc):
        bc.mqtt_api = MagicMock()
        bc.api_set_grid_charge_lock(True)

        bc.api_set_grid_charge_lock(True)

        bc.mqtt_api.publish_grid_charge_locked.assert_called_once_with(True)
        assert bc._pre_lock_max_charging_from_grid_limit == 0.8

    def test_lock_cancels_active_force_charge(self, bc):
        bc.mqtt_api = MagicMock()
        bc.force_charge(1000, control_source=CONTROL_SOURCE_API)
        assert bc.last_mode == MODE_FORCE_CHARGING

        bc.api_set_grid_charge_lock(True)

        assert bc.last_mode == MODE_ALLOW_DISCHARGING
        assert bc.max_charging_from_grid_limit == 0.0

    def test_unlock_with_lowered_always_allow_discharge_limit(self, bc):
        """If always_allow_discharge_limit was lowered below the remembered
        value while locked, the unlock must not leave the limit stuck at 0.0
        (issue found in PR #416 review). The limit in effect stays below
        always_allow_discharge_limit and returns to the remembered value
        once that is raised again."""
        bc.mqtt_api = MagicMock()
        bc.api_set_grid_charge_lock(True)
        bc.set_always_allow_discharge_limit(0.5)

        bc.api_set_grid_charge_lock(False)

        assert bc.max_charging_from_grid_limit == pytest.approx(0.49)
        assert bc._grid_charge_locked is False
        assert bc._pre_lock_max_charging_from_grid_limit is None

        bc.set_always_allow_discharge_limit(0.9)
        assert bc.max_charging_from_grid_limit == 0.8


class TestEffectiveGridChargeLimit:
    """max_charging_from_grid_limit in effect is derived from the requested
    value and always_allow_discharge_limit, never written back over it."""

    BASE_CONFIG = TestApiSetGridChargeLock.BASE_CONFIG
    bc = TestApiSetGridChargeLock.bc

    def test_limit_returns_after_discharge_limit_is_raised_again(self, bc):
        """Regression: lowering always_allow_discharge_limit for a while
        used to leave max_charging_from_grid_limit lowered until restart."""
        bc.set_always_allow_discharge_limit(0.5)

        assert bc.max_charging_from_grid_limit == pytest.approx(0.49)
        assert bc.get_max_charging_from_grid_limit() == 0.8

        bc.set_always_allow_discharge_limit(0.9)

        assert bc.max_charging_from_grid_limit == 0.8

    def test_control_cycle_keeps_the_requested_limit(self, bc, mocker):
        """The cycle applies the constraint without overwriting the
        requested value."""
        bc.general_logic.set_always_allow_discharge_limit(0.5)
        mocker.patch.object(bc, 'handle_forecast_error')

        bc.run()

        assert bc.max_charging_from_grid_limit == pytest.approx(0.49)
        assert bc.get_max_charging_from_grid_limit() == 0.8

        bc.general_logic.set_always_allow_discharge_limit(0.9)
        bc.run()

        assert bc.max_charging_from_grid_limit == 0.8

    def test_equal_limits_are_not_lowered(self, bc):
        bc.set_always_allow_discharge_limit(0.8)

        assert bc.max_charging_from_grid_limit == 0.8

    def test_effective_limit_is_published(self, bc):
        bc.mqtt_api = MagicMock()

        bc.set_always_allow_discharge_limit(0.5)

        published = (
            bc.mqtt_api.publish_max_charging_from_grid_limit.call_args.args[0])
        assert published == pytest.approx(0.49)

    def test_unchanged_limit_is_not_published_again(self, bc):
        bc.set_always_allow_discharge_limit(0.9)
        bc.mqtt_api = MagicMock()

        bc.set_always_allow_discharge_limit(0.95)

        bc.mqtt_api.publish_max_charging_from_grid_limit.assert_not_called()

    def test_value_above_discharge_limit_is_kept_as_requested(self, bc):
        """A requested value above always_allow_discharge_limit (0.9) is
        accepted; the limit in effect stays below the discharge limit and
        follows it."""
        # CommonLogic is a singleton, so set the discharge limit explicitly.
        bc.set_always_allow_discharge_limit(0.9)
        bc.mqtt_api = MagicMock()

        bc.set_max_charging_from_grid_limit(0.95)

        assert bc.get_max_charging_from_grid_limit() == 0.95
        assert bc.max_charging_from_grid_limit == pytest.approx(0.89)
        published = (
            bc.mqtt_api.publish_max_charging_from_grid_limit.call_args.args[0])
        assert published == pytest.approx(0.89)

        bc.set_always_allow_discharge_limit(1.0)
        assert bc.max_charging_from_grid_limit == 0.95

    @pytest.mark.parametrize('limit', [0, 0.0, -0.1, 1.01, float('nan')])
    def test_api_rejects_invalid_discharge_limit(self, bc, caplog, limit):
        """0 is rejected like at startup: discharging at every SoC would
        switch the price based control off. The current value stays."""
        bc.mqtt_api = MagicMock()
        bc.set_always_allow_discharge_limit(0.8)
        bc.mqtt_api.reset_mock()

        with caplog.at_level(logging.WARNING, logger='batcontrol.core'):
            bc.api_set_always_allow_discharge_limit(limit)

        assert bc.get_always_allow_discharge_limit() == 0.8
        bc.mqtt_api.publish_always_allow_discharge_limit.assert_not_called()
        assert 'Invalid always allow discharge limit' in caplog.text

    @pytest.mark.parametrize('limit', [0.01, 0.5, 1.0])
    def test_api_accepts_discharge_limit_above_zero_up_to_one(self, bc, limit):
        bc.mqtt_api = MagicMock()
        bc.api_set_always_allow_discharge_limit(limit)
        assert bc.get_always_allow_discharge_limit() == limit

    def test_discharge_limit_of_zero_does_not_go_negative(self, bc):
        bc.set_always_allow_discharge_limit(0.0)

        assert bc.max_charging_from_grid_limit == 0.0


class TestGridChargeLockTopicRegistration:
    """Wiring of the optional mqtt.grid_charge_lock_topic config option."""

    BASE_CONFIG = {
        'timezone': 'Europe/Berlin',
        'time_resolution_minutes': 60,
        'inverter': {
            'type': 'dummy',
            'max_grid_charge_rate': 5000,
            'max_pv_charge_rate': 3000,
            'min_pv_charge_rate': 0,
        },
        'utility': {'type': 'tibber', 'apikey': 'test_token'},
        'pvinstallations': [],
        'consumption_forecast': {'type': 'simple', 'value': 500},
        'battery_control': {
            'max_charging_from_grid_limit': 0.8,
            'min_price_difference': 0.05,
        },
    }

    def _patch_core(self, mocker):
        mock_inverter = mocker.MagicMock()
        mock_inverter.max_pv_charge_rate = 3000
        mock_inverter.get_max_capacity.return_value = 10000
        mocker.patch('batcontrol.core.tariff_factory.create_tarif_provider',
                     autospec=True, return_value=mocker.MagicMock())
        mocker.patch('batcontrol.core.inverter_factory.create_inverter',
                     autospec=True, return_value=mock_inverter)
        mocker.patch('batcontrol.core.solar_factory.create_solar_provider',
                     autospec=True, return_value=mocker.MagicMock())
        mocker.patch('batcontrol.core.consumption_factory.create_consumption',
                     autospec=True, return_value=mocker.MagicMock())

    def test_registers_external_topic_when_configured(self, mocker):
        self._patch_core(mocker)
        mock_mqtt_api = mocker.MagicMock()
        mocker.patch('batcontrol.core.MqttApi', return_value=mock_mqtt_api)
        config = dict(self.BASE_CONFIG)
        config['mqtt'] = {
            'enabled': True,
            'broker': 'localhost',
            'port': 1883,
            'topic': 'house/batcontrol',
            'grid_charge_lock_topic': 'hems/batcontrol/grid_charge_lock',
        }

        bc = Batcontrol(config)

        mock_mqtt_api.register_external_topic_callback.assert_called_once_with(
            'hems/batcontrol/grid_charge_lock',
            bc.api_set_grid_charge_lock,
            parse_bool_flag,
        )
        bc.shutdown()

    def test_no_registration_when_topic_not_configured(self, mocker):
        self._patch_core(mocker)
        mock_mqtt_api = mocker.MagicMock()
        mocker.patch('batcontrol.core.MqttApi', return_value=mock_mqtt_api)
        config = dict(self.BASE_CONFIG)
        config['mqtt'] = {
            'enabled': True,
            'broker': 'localhost',
            'port': 1883,
            'topic': 'house/batcontrol',
        }

        bc = Batcontrol(config)

        mock_mqtt_api.register_external_topic_callback.assert_not_called()
        bc.shutdown()


class TestPeakShavingStartupAnnouncement:
    """Startup announcement when peak_shaving.enabled is true.

    Up to 0.10.0 peak shaving only ran with battery_control.type "next" and
    was documented as having no effect otherwise, so an existing config may
    carry enabled: true from a time when it did nothing. It now applies to
    every logic type, so the activation is announced once at startup
    instead of only showing up in the per-cycle [PeakShaving] lines.
    """

    BASE_CONFIG = {
        'timezone': 'Europe/Berlin',
        'time_resolution_minutes': 60,
        'inverter': {
            'type': 'dummy',
            'max_grid_charge_rate': 5000,
            'max_pv_charge_rate': 0,
            'min_pv_charge_rate': 0,
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

    def _patch_core(self, mocker):
        mock_inverter = mocker.MagicMock()
        mock_inverter.max_pv_charge_rate = 0
        mock_inverter.get_max_capacity.return_value = 10000
        mocker.patch('batcontrol.core.tariff_factory.create_tarif_provider',
                     autospec=True, return_value=mocker.MagicMock())
        mocker.patch('batcontrol.core.inverter_factory.create_inverter',
                     autospec=True, return_value=mock_inverter)
        mocker.patch('batcontrol.core.solar_factory.create_solar_provider',
                     autospec=True, return_value=mocker.MagicMock())
        mocker.patch('batcontrol.core.consumption_factory.create_consumption',
                     autospec=True, return_value=mocker.MagicMock())

    def _build(self, mocker, caplog, logic_type, peak_shaving_enabled):
        self._patch_core(mocker)
        config = dict(self.BASE_CONFIG)
        config['battery_control'] = dict(self.BASE_CONFIG['battery_control'])
        config['battery_control']['type'] = logic_type
        config['peak_shaving'] = {
            'enabled': peak_shaving_enabled,
            'allow_full_battery_after': 14,
        }
        with caplog.at_level(logging.INFO, logger='batcontrol.core'):
            bc = Batcontrol(config)
        bc.shutdown()
        return caplog.text

    @pytest.mark.parametrize('logic_type', ['default', 'next'])
    def test_enabled_is_announced_for_every_logic_type(
            self, mocker, caplog, logic_type):
        """The announcement does not depend on battery_control.type."""
        text = self._build(mocker, caplog, logic_type, True)

        assert 'Peak shaving is ENABLED' in text
        assert 'allow_full_battery_after=14:00' in text

    @pytest.mark.parametrize('logic_type', ['default', 'next'])
    def test_disabled_is_not_announced(self, mocker, caplog, logic_type):
        """No announcement when the feature stays off."""
        text = self._build(mocker, caplog, logic_type, False)

        assert 'Peak shaving is ENABLED' not in text

class TestMqttBrokerUnreachable:
    """R-M01: without a reachable broker batcontrol stops, after restoring
    the inverter settings it has already changed."""

    CONFIG = dict(TestGridChargeLockTopicRegistration.BASE_CONFIG, mqtt={
        'enabled': True,
        'broker': 'mqtt.lan',
        'port': 1883,
        'topic': 'house/batcontrol',
    })

    def _patch_core(self, mocker):
        mock_inverter = mocker.MagicMock()
        mock_inverter.max_pv_charge_rate = 3000
        mock_inverter.get_max_capacity.return_value = 10000
        mocker.patch('batcontrol.core.tariff_factory.create_tarif_provider',
                     autospec=True, return_value=mocker.MagicMock())
        mocker.patch('batcontrol.core.inverter_factory.create_inverter',
                     autospec=True, return_value=mock_inverter)
        mocker.patch('batcontrol.core.solar_factory.create_solar_provider',
                     autospec=True, return_value=mocker.MagicMock())
        mocker.patch('batcontrol.core.consumption_factory.create_consumption',
                     autospec=True, return_value=mocker.MagicMock())
        return mock_inverter

    def test_connect_failure_restores_inverter_and_raises(self, mocker):
        inverter = self._patch_core(mocker)
        mocker.patch('batcontrol.core.MqttApi',
                     side_effect=MqttConnectionError('unreachable'))

        with pytest.raises(MqttConnectionError, match='unreachable'):
            Batcontrol(dict(self.CONFIG))

        inverter.shutdown.assert_called_once()

    def test_not_ready_restores_inverter_and_raises(self, mocker):
        """R-M27: wait_ready() == False (e.g. bad credentials) also stops."""
        inverter = self._patch_core(mocker)
        mock_mqtt_api = mocker.MagicMock()
        mock_mqtt_api.wait_ready.return_value = False
        mocker.patch('batcontrol.core.MqttApi', return_value=mock_mqtt_api)

        with pytest.raises(MqttConnectionError, match='mqtt.lan:1883'):
            Batcontrol(dict(self.CONFIG))

        mock_mqtt_api.shutdown.assert_called_once()
        inverter.shutdown.assert_called_once()
        inverter.activate_mqtt.assert_not_called()

    def test_invalid_mqtt_settings_also_restore_inverter(self, mocker):
        inverter = self._patch_core(mocker)
        mocker.patch('batcontrol.core.MqttApi',
                     side_effect=ValueError('mqtt: port must be a number'))

        with pytest.raises(ValueError, match='port'):
            Batcontrol(dict(self.CONFIG))

        inverter.shutdown.assert_called_once()

    def test_failed_inverter_restore_is_logged(self, mocker, caplog):
        inverter = self._patch_core(mocker)
        inverter.shutdown.side_effect = RuntimeError('fronius offline')
        mocker.patch('batcontrol.core.MqttApi',
                     side_effect=MqttConnectionError('unreachable'))

        with pytest.raises(MqttConnectionError):
            Batcontrol(dict(self.CONFIG))

        assert 'fronius offline' in caplog.text

    def test_evcc_broker_failure_restores_inverter_and_disconnects(
            self, mocker):
        inverter = self._patch_core(mocker)
        mock_mqtt_api = mocker.MagicMock()
        mock_mqtt_api.wait_ready.return_value = True
        mocker.patch('batcontrol.core.MqttApi', return_value=mock_mqtt_api)
        mock_evcc = mocker.MagicMock()
        mock_evcc.start.side_effect = MqttConnectionError('evcc unreachable')
        mocker.patch('batcontrol.core.EvccApi', return_value=mock_evcc)
        config = dict(self.CONFIG, evcc={
            'enabled': True, 'broker': 'evcc.lan', 'port': 1883,
            'status_topic': 'evcc/status',
            'loadpoint_topic': ['evcc/loadpoints/1/charging'],
        })

        with pytest.raises(MqttConnectionError, match='evcc unreachable'):
            Batcontrol(config)

        mock_evcc.shutdown.assert_called_once()
        mock_mqtt_api.shutdown.assert_called_once()
        inverter.shutdown.assert_called_once()

    def test_invalid_evcc_config_restores_inverter(self, mocker):
        inverter = self._patch_core(mocker)
        mock_mqtt_api = mocker.MagicMock()
        mock_mqtt_api.wait_ready.return_value = True
        mocker.patch('batcontrol.core.MqttApi', return_value=mock_mqtt_api)
        mocker.patch('batcontrol.core.EvccApi', side_effect=ValueError(
            'evcc: tls is enabled but cafile is missing or empty'))
        config = dict(self.CONFIG, evcc={'enabled': True})

        with pytest.raises(ValueError, match='cafile'):
            Batcontrol(config)

        mock_mqtt_api.shutdown.assert_called_once()
        inverter.shutdown.assert_called_once()


class TestInverterShutdownFailure:
    """R-I17: a failed inverter restore must be logged with the inverter
    identity and must not stop the rest of the shutdown."""

    CONFIG = TestMqttBrokerUnreachable.CONFIG

    @staticmethod
    def _failing_inverter(mocker):
        inverter = mocker.MagicMock()
        inverter.max_pv_charge_rate = 3000
        inverter.get_max_capacity.return_value = 10000
        inverter.inverter_num = 0
        inverter.inverter_type = 'fronius_gen24'
        inverter.shutdown.side_effect = RuntimeError(
            'failed to restore battery config')
        return inverter

    @staticmethod
    def _patch_core(mocker, inverter):
        mocker.patch('batcontrol.core.tariff_factory.create_tarif_provider',
                     autospec=True, return_value=mocker.MagicMock())
        mocker.patch('batcontrol.core.inverter_factory.create_inverter',
                     autospec=True, return_value=inverter)
        mocker.patch('batcontrol.core.solar_factory.create_solar_provider',
                     autospec=True, return_value=mocker.MagicMock())
        mocker.patch('batcontrol.core.consumption_factory.create_consumption',
                     autospec=True, return_value=mocker.MagicMock())

    @staticmethod
    def _errors(caplog):
        return [r.getMessage() for r in caplog.records
                if r.levelno >= logging.ERROR
                and 'failed to restore battery config' in r.getMessage()]

    def test_wrapped_restore_failure_on_broker_abort_is_logged(
            self, mocker, caplog):
        inner = self._failing_inverter(mocker)
        self._patch_core(mocker, ResilientInverterWrapper(inner))
        mocker.patch('batcontrol.core.MqttApi',
                     side_effect=MqttConnectionError('unreachable'))

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(MqttConnectionError):
                Batcontrol(dict(self.CONFIG))

        inner.shutdown.assert_called_once()
        errors = self._errors(caplog)
        assert len(errors) == 1
        assert 'Inverter 0 (fronius_gen24)' in errors[0]

    def test_wrapped_restore_failure_on_shutdown_is_logged(
            self, mocker, caplog):
        inner = self._failing_inverter(mocker)
        self._patch_core(mocker, ResilientInverterWrapper(inner))
        mock_mqtt_api = mocker.MagicMock()
        mock_mqtt_api.wait_ready.return_value = True
        mocker.patch('batcontrol.core.MqttApi', return_value=mock_mqtt_api)
        bc = Batcontrol(dict(self.CONFIG))

        with caplog.at_level(logging.DEBUG):
            bc.shutdown()

        inner.shutdown.assert_called_once()
        mock_mqtt_api.shutdown.assert_called_once()
        errors = self._errors(caplog)
        assert len(errors) == 1
        assert 'Inverter 0 (fronius_gen24)' in errors[0]

    def test_unwrapped_restore_failure_still_shuts_down_mqtt(
            self, mocker, caplog):
        """enable_resilient_wrapper: false - the raising inverter must not
        skip the MQTT disconnect, and the failure must be logged."""
        inverter = self._failing_inverter(mocker)
        self._patch_core(mocker, inverter)
        mock_mqtt_api = mocker.MagicMock()
        mock_mqtt_api.wait_ready.return_value = True
        mocker.patch('batcontrol.core.MqttApi', return_value=mock_mqtt_api)
        bc = Batcontrol(dict(self.CONFIG))

        with caplog.at_level(logging.DEBUG):
            bc.shutdown()

        mock_mqtt_api.shutdown.assert_called_once()
        errors = self._errors(caplog)
        assert len(errors) == 1
        assert 'Inverter 0 (fronius_gen24)' in errors[0]
        assert 'during shutdown' in errors[0]
        assert 'RuntimeError: failed to restore battery config' in errors[0]


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
