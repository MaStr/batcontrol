"""Tests for optional battery capacity override in the Fronius GEN24 inverter."""
import unittest
from unittest.mock import Mock, patch
import json
from packaging import version

from batcontrol.inverter.fronius import FroniusWR


class TestFroniusCapacityOverride(unittest.TestCase):
    """Test that a configured capacity overrides the value queried from the inverter."""

    def setUp(self):
        self.base_config = {
            'address': '192.168.1.100',
            'user': 'customer',
            'password': 'testpass',
            'max_grid_charge_rate': 5000,
            'max_pv_charge_rate': 0
        }

    def _setup_mocks(self, mock_get_firmware, mock_get_battery, mock_get_powerunit,
                     mock_send_request, designed_capacity=10000):
        mock_get_firmware.return_value = version.parse("1.36.0")
        mock_get_battery.return_value = {
            'HYB_EM_MODE': 0,
            'HYB_EM_POWER': 0,
            'BAT_M0_SOC_MIN': 5,
            'BAT_M0_SOC_MAX': 100,
            'HYB_BACKUP_RESERVED': 10
        }
        mock_get_powerunit.return_value = {
            'backuppower': {'DEVICE_MODE_BACKUPMODE_TYPE_U16': 0}
        }

        mock_powerflow = Mock()
        mock_powerflow.text = json.dumps({
            'Body': {'Data': {'Inverters': {'1': {'SOC': 50}}}}
        })
        mock_storage = Mock()
        mock_storage.text = json.dumps({
            'Body': {'Data': {'0': {
                'Controller': {'DesignedCapacity': designed_capacity}
            }}}
        })
        mock_send_request.side_effect = [mock_powerflow, mock_storage]

    @patch('batcontrol.inverter.fronius.FroniusWR.get_firmware_version')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_battery_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_powerunit_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.backup_time_of_use')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_solar_api_active')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_allow_grid_charging')
    @patch('batcontrol.inverter.fronius.FroniusWR.send_request')
    def test_capacity_queried_from_inverter_by_default(
        self, mock_send_request, mock_set_allow, mock_set_solar, mock_backup_tou,
        mock_get_powerunit, mock_get_battery, mock_get_firmware):
        """Without a 'capacity' config key, the value comes from the inverter API."""
        self._setup_mocks(mock_get_firmware, mock_get_battery, mock_get_powerunit,
                          mock_send_request, designed_capacity=10000)
        mock_capacity_response = Mock()
        mock_capacity_response.text = json.dumps({
            'Body': {'Data': {'0': {'Controller': {'DesignedCapacity': 10000}}}}
        })
        mock_send_request.side_effect = list(mock_send_request.side_effect) + [
            mock_capacity_response
        ]

        inverter = FroniusWR(self.base_config)

        self.assertEqual(inverter.get_capacity(), 10000)
        # One extra call to the storage endpoint was needed to fetch capacity.
        self.assertEqual(mock_send_request.call_count, 3)

    @patch('batcontrol.inverter.fronius.FroniusWR.get_firmware_version')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_battery_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_powerunit_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.backup_time_of_use')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_solar_api_active')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_allow_grid_charging')
    @patch('batcontrol.inverter.fronius.FroniusWR.send_request')
    def test_capacity_override_from_config(
        self, mock_send_request, mock_set_allow, mock_set_solar, mock_backup_tou,
        mock_get_powerunit, mock_get_battery, mock_get_firmware):
        """A configured 'capacity' overrides the value queried from the inverter."""
        self._setup_mocks(mock_get_firmware, mock_get_battery, mock_get_powerunit,
                          mock_send_request, designed_capacity=10000)

        config = self.base_config.copy()
        config['capacity'] = 7500
        inverter = FroniusWR(config)

        self.assertEqual(inverter.get_capacity(), 7500)
        # No additional call to the storage endpoint for capacity was needed.
        self.assertEqual(mock_send_request.call_count, 2)

    @patch('batcontrol.inverter.fronius.FroniusWR.get_firmware_version')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_battery_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_powerunit_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.backup_time_of_use')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_solar_api_active')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_allow_grid_charging')
    @patch('batcontrol.inverter.fronius.FroniusWR.send_request')
    def test_max_capacity_uses_configured_capacity(
        self, mock_send_request, mock_set_allow, mock_set_solar, mock_backup_tou,
        mock_get_powerunit, mock_get_battery, mock_get_firmware):
        """get_max_capacity() reflects the configured capacity override."""
        self._setup_mocks(mock_get_firmware, mock_get_battery, mock_get_powerunit,
                          mock_send_request, designed_capacity=10000)

        config = self.base_config.copy()
        config['capacity'] = 8000
        inverter = FroniusWR(config)

        # BAT_M0_SOC_MAX is 100 in the mocked battery config.
        self.assertEqual(inverter.get_max_capacity(), 8000)

    @patch('batcontrol.inverter.fronius.FroniusWR.get_firmware_version')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_battery_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_powerunit_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.backup_time_of_use')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_solar_api_active')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_allow_grid_charging')
    @patch('batcontrol.inverter.fronius.FroniusWR.send_request')
    def test_capacity_string_from_yaml_is_coerced(
        self, mock_send_request, mock_set_allow, mock_set_solar, mock_backup_tou,
        mock_get_powerunit, mock_get_battery, mock_get_firmware):
        """A quoted numeric string in YAML (e.g. '10000') is coerced to a number."""
        self._setup_mocks(mock_get_firmware, mock_get_battery, mock_get_powerunit,
                          mock_send_request, designed_capacity=10000)

        config = self.base_config.copy()
        config['capacity'] = '7500'
        inverter = FroniusWR(config)

        self.assertEqual(inverter.get_capacity(), 7500)

    @patch('batcontrol.inverter.fronius.FroniusWR.get_firmware_version')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_battery_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_powerunit_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.backup_time_of_use')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_solar_api_active')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_allow_grid_charging')
    @patch('batcontrol.inverter.fronius.FroniusWR.send_request')
    def test_invalid_capacity_raises_error(
        self, mock_send_request, mock_set_allow, mock_set_solar, mock_backup_tou,
        mock_get_powerunit, mock_get_battery, mock_get_firmware):
        """A non-numeric capacity value raises a clear RuntimeError."""
        config = self.base_config.copy()
        config['capacity'] = 'not-a-number'

        with self.assertRaises(RuntimeError) as context:
            FroniusWR(config)

        self.assertIn('capacity', str(context.exception))

    @patch('batcontrol.inverter.fronius.FroniusWR.get_firmware_version')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_battery_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_powerunit_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.backup_time_of_use')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_solar_api_active')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_allow_grid_charging')
    @patch('batcontrol.inverter.fronius.FroniusWR.send_request')
    def test_non_finite_capacity_raises_error(
        self, mock_send_request, mock_set_allow, mock_set_solar, mock_backup_tou,
        mock_get_powerunit, mock_get_battery, mock_get_firmware):
        """'inf'/'nan' capacity values are rejected instead of propagating NaN/inf."""
        for bad_value in ('inf', '-inf', 'nan', float('inf'), float('nan')):
            config = self.base_config.copy()
            config['capacity'] = bad_value

            with self.assertRaises(RuntimeError) as context:
                FroniusWR(config)

            self.assertIn('capacity', str(context.exception))


if __name__ == '__main__':
    unittest.main()
