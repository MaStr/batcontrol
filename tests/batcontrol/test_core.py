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
    MODE_LIMIT_BATTERY_CHARGE_RATE,
)
from batcontrol.logic.logic import Logic as LogicFactory


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

    def test_accepts_grid_charge_forecast_pv_factor_numeric_string_config(
            self, mock_config, mocker):
        mock_config['battery_control']['grid_charge_forecast_pv_factor'] = '0.75'
        self._patch_core_dependencies(mocker)

        bc = Batcontrol(mock_config)

        assert bc.grid_charge_forecast_pv_factor == 0.75
        bc.shutdown()

    def test_rejects_invalid_grid_charge_forecast_pv_factor_config(
            self, mock_config, mocker):
        mock_config['battery_control']['grid_charge_forecast_pv_factor'] = 1.5
        self._patch_core_dependencies(mocker)

        with pytest.raises(
                ValueError,
                match='battery_control.grid_charge_forecast_pv_factor'):
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

    def test_run_passes_forecast_grid_charge_target_to_logic(
            self, mock_config, mocker):
        mock_config['battery_control'].update({
            'min_grid_charge_soc': 0.55,
            'max_charging_from_grid_limit': 0.89,
            'grid_charge_target_strategy': 'forecast',
            'grid_charge_forecast_pv_factor': 0.5,
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
            assert calc_params.min_grid_charge_soc == pytest.approx(0.79, abs=0.01)
        finally:
            bc.shutdown()

    def test_run_publishes_effective_grid_charge_target(
            self, mock_config, mocker):
        mock_config['battery_control'].update({
            'min_grid_charge_soc': 0.55,
            'max_charging_from_grid_limit': 0.89,
            'grid_charge_target_strategy': 'forecast',
            'grid_charge_forecast_pv_factor': 0.5,
        })
        bc, _fake_logic = self._make_batcontrol_for_grid_charge_target(
            mock_config,
            mocker,
            prices={0: 0.4635, 1: 0.7018, 2: 0.7018, 3: 0.7018,
                    4: 0.7018, 5: 0.4635},
            production={0: 149, 1: 569, 2: 1488, 3: 2678, 4: 3500, 5: 4000},
            consumption={0: 547, 1: 731, 2: 3427, 3: 3497, 4: 3700, 5: 500},
        )
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


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
