"""Tests for Fronius inverter configurable IDs."""
import unittest
from unittest.mock import Mock, patch
import json
from packaging import version

from batcontrol.inverter.fronius import FroniusWR


class TestFroniusConfigurableIDs(unittest.TestCase):
    """Test configurable inverter and controller IDs."""

    def setUp(self):
        """Set up test fixtures."""
        self.base_config = {
            'address': '192.168.1.100',
            'user': 'customer',
            'password': 'testpass',
            'max_grid_charge_rate': 5000,
            'max_pv_charge_rate': 0
        }

    def _setup_mocks(self, mock_get_firmware, mock_get_battery, mock_get_powerunit,
                     mock_send_request=None, inverter_id='1', controller_id='0'):
        """Helper method to set up common mocks."""
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

        # Mock send_request for ID verification
        # Returns appropriate responses for verification calls during __init__
        if mock_send_request:
            responses = []
            # First call - powerflow for inverter ID verification
            mock_powerflow = Mock()
            mock_powerflow.text = json.dumps({
                'Body': {
                    'Data': {
                        'Inverters': {
                            inverter_id: {'SOC': 50}
                        }
                    }
                }
            })
            responses.append(mock_powerflow)

            # Second call - storage for controller ID verification
            mock_storage = Mock()
            mock_storage.text = json.dumps({
                'Body': {
                    'Data': {
                        controller_id: {
                            'Controller': {'DesignedCapacity': 10000}
                        }
                    }
                }
            })
            responses.append(mock_storage)

            mock_send_request.side_effect = responses

    @patch('batcontrol.inverter.fronius.FroniusWR.get_firmware_version')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_battery_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_powerunit_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.backup_time_of_use')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_solar_api_active')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_allow_grid_charging')
    @patch('batcontrol.inverter.fronius.FroniusWR.send_request')
    def test_default_inverter_id(self, mock_send_request, mock_set_allow, mock_set_solar,
                                 mock_backup_tou, mock_get_powerunit, mock_get_battery,
                                 mock_get_firmware):
        """Test that default inverter_id is '1' when not specified."""
        self._setup_mocks(mock_get_firmware,
                          mock_get_battery, mock_get_powerunit, mock_send_request,
                          inverter_id='1', controller_id='0')

        # Create inverter without specifying fronius_inverter_id
        inverter = FroniusWR(self.base_config)

        # Assert default value
        self.assertEqual(inverter.inverter_id, '1')

    @patch('batcontrol.inverter.fronius.FroniusWR.get_firmware_version')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_battery_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_powerunit_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.backup_time_of_use')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_solar_api_active')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_allow_grid_charging')
    @patch('batcontrol.inverter.fronius.FroniusWR.send_request')
    def test_default_controller_id(self, mock_send_request, mock_set_allow, mock_set_solar, mock_backup_tou,
                                   mock_get_powerunit, mock_get_battery, mock_get_firmware):
        """Test that default controller_id is '0' when not specified."""
        self._setup_mocks(mock_get_firmware,
                          mock_get_battery, mock_get_powerunit, mock_send_request,
                          inverter_id='1', controller_id='0')

        # Create inverter without specifying fronius_controller_id
        inverter = FroniusWR(self.base_config)

        # Assert default value
        self.assertEqual(inverter.controller_id, '0')

    @patch('batcontrol.inverter.fronius.FroniusWR.get_firmware_version')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_battery_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_powerunit_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.backup_time_of_use')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_solar_api_active')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_allow_grid_charging')
    @patch('batcontrol.inverter.fronius.FroniusWR.send_request')
    def test_custom_inverter_id(self, mock_send_request, mock_set_allow, mock_set_solar, mock_backup_tou,
                                mock_get_powerunit, mock_get_battery, mock_get_firmware):
        """Test that custom inverter_id can be set."""
        self._setup_mocks(mock_get_firmware,
                          mock_get_battery, mock_get_powerunit, mock_send_request,
                          inverter_id='2', controller_id='0')

        # Create inverter with custom fronius_inverter_id
        config = self.base_config.copy()
        config['fronius_inverter_id'] = '2'
        inverter = FroniusWR(config)

        # Assert custom value
        self.assertEqual(inverter.inverter_id, '2')

    @patch('batcontrol.inverter.fronius.FroniusWR.get_firmware_version')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_battery_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_powerunit_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.backup_time_of_use')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_solar_api_active')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_allow_grid_charging')
    @patch('batcontrol.inverter.fronius.FroniusWR.send_request')
    def test_custom_controller_id(self, mock_send_request, mock_set_allow, mock_set_solar, mock_backup_tou,
                                  mock_get_powerunit, mock_get_battery, mock_get_firmware):
        """Test that custom controller_id can be set."""
        self._setup_mocks(mock_get_firmware,
                          mock_get_battery, mock_get_powerunit, mock_send_request,
                          inverter_id='1', controller_id='3')

        # Create inverter with custom fronius_controller_id
        config = self.base_config.copy()
        config['fronius_controller_id'] = '3'
        inverter = FroniusWR(config)

        # Assert custom value
        self.assertEqual(inverter.controller_id, '3')

    @patch('batcontrol.inverter.fronius.FroniusWR.get_firmware_version')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_battery_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_powerunit_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.backup_time_of_use')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_solar_api_active')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_allow_grid_charging')
    @patch('batcontrol.inverter.fronius.FroniusWR.send_request')
    def test_get_soc_uses_inverter_id(self, mock_send_request, mock_set_allow, mock_set_solar,
                                      mock_backup_tou, mock_get_powerunit, mock_get_battery,
                                      mock_get_firmware):
        """Test that get_SOC uses the configured inverter_id."""
        self._setup_mocks(mock_get_firmware,
                          mock_get_battery, mock_get_powerunit, mock_send_request,
                          inverter_id='2', controller_id='0')

        # Add a third response for the actual get_SOC call
        mock_soc_response = Mock()
        mock_soc_response.text = json.dumps({
            'Body': {
                'Data': {
                    'Inverters': {
                        '2': {'SOC': 75.5}
                    }
                }
            }
        })
        # Extend the side_effect list with the additional response
        current_side_effect = list(mock_send_request.side_effect)
        current_side_effect.append(mock_soc_response)
        mock_send_request.side_effect = current_side_effect

        # Create inverter with custom inverter_id
        config = self.base_config.copy()
        config['fronius_inverter_id'] = '2'
        inverter = FroniusWR(config)

        # Get SOC
        soc = inverter.get_SOC()

        # Assert SOC is read from correct inverter ID
        self.assertEqual(soc, 75.5)

    @patch('batcontrol.inverter.fronius.FroniusWR.get_firmware_version')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_battery_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_powerunit_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.backup_time_of_use')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_solar_api_active')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_allow_grid_charging')
    @patch('batcontrol.inverter.fronius.FroniusWR.send_request')
    def test_get_capacity_uses_controller_id(self, mock_send_request, mock_set_allow,
                                             mock_set_solar, mock_backup_tou,
                                             mock_get_powerunit, mock_get_battery,
                                             mock_get_firmware):
        """Test that get_capacity uses the configured controller_id."""
        self._setup_mocks(mock_get_firmware,
                          mock_get_battery, mock_get_powerunit, mock_send_request,
                          inverter_id='1', controller_id='1')

        # Add a third response for the actual get_capacity call
        mock_capacity_response = Mock()
        mock_capacity_response.text = json.dumps({
            'Body': {
                'Data': {
                    '1': {
                        'Controller': {'DesignedCapacity': 12500}
                    }
                }
            }
        })
        # Extend the side_effect list with the additional response
        current_side_effect = list(mock_send_request.side_effect)
        current_side_effect.append(mock_capacity_response)
        mock_send_request.side_effect = current_side_effect

        # Create inverter with custom controller_id
        config = self.base_config.copy()
        config['fronius_controller_id'] = '1'
        inverter = FroniusWR(config)

        # Get capacity
        capacity = inverter.get_capacity()

        # Assert capacity is read from correct controller ID
        self.assertEqual(capacity, 12500)

    @patch('batcontrol.inverter.fronius.FroniusWR.get_firmware_version')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_battery_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_powerunit_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.backup_time_of_use')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_solar_api_active')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_allow_grid_charging')
    @patch('batcontrol.inverter.fronius.FroniusWR.send_request')
    def test_ids_converted_to_string(self, mock_send_request, mock_set_allow, mock_set_solar, mock_backup_tou,
                                     mock_get_powerunit, mock_get_battery, mock_get_firmware):
        """Test that IDs are converted to strings even if provided as integers."""
        self._setup_mocks(mock_get_firmware,
                          mock_get_battery, mock_get_powerunit, mock_send_request,
                          inverter_id='2', controller_id='1')

        # Create inverter with integer IDs
        config = self.base_config.copy()
        config['fronius_inverter_id'] = 2
        config['fronius_controller_id'] = 1
        inverter = FroniusWR(config)

        # Assert IDs are strings
        self.assertIsInstance(inverter.inverter_id, str)
        self.assertIsInstance(inverter.controller_id, str)
        self.assertEqual(inverter.inverter_id, '2')
        self.assertEqual(inverter.controller_id, '1')

    @patch('batcontrol.inverter.fronius.FroniusWR.get_firmware_version')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_battery_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_powerunit_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.backup_time_of_use')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_solar_api_active')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_allow_grid_charging')
    @patch('batcontrol.inverter.fronius.FroniusWR.send_request')
    def test_invalid_inverter_id_raises_error(self, mock_send_request, mock_set_allow,
                                              mock_set_solar, mock_backup_tou,
                                              mock_get_powerunit, mock_get_battery,
                                              mock_get_firmware):
        """Test that an invalid inverter_id raises RuntimeError."""
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

        # Mock send_request to return data without the requested inverter ID
        def send_request_side_effect(path, *args, **kwargs):
            mock_response = Mock()
            if 'powerflow' in path.lower() or 'GetPowerFlowRealtimeData' in path:
                # Only has inverter ID '1', not '99'
                mock_response.text = json.dumps({
                    'Body': {
                        'Data': {
                            'Inverters': {
                                '1': {'SOC': 50}
                            }
                        }
                    }
                })
            elif 'storage' in path.lower() or 'GetStorageRealtimeData' in path:
                mock_response.text = json.dumps({
                    'Body': {
                        'Data': {
                            '0': {
                                'Controller': {'DesignedCapacity': 10000}
                            }
                        }
                    }
                })
            return mock_response
        mock_send_request.side_effect = send_request_side_effect

        # Try to create inverter with invalid inverter_id
        config = self.base_config.copy()
        config['fronius_inverter_id'] = '99'

        with self.assertRaises(RuntimeError) as context:
            FroniusWR(config)

        self.assertIn('Invalid fronius_inverter_id', str(context.exception))
        self.assertIn('99', str(context.exception))

    @patch('batcontrol.inverter.fronius.FroniusWR.get_firmware_version')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_battery_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_powerunit_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.backup_time_of_use')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_solar_api_active')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_allow_grid_charging')
    @patch('batcontrol.inverter.fronius.FroniusWR.send_request')
    def test_invalid_controller_id_raises_error(self, mock_send_request, mock_set_allow,
                                                mock_set_solar, mock_backup_tou,
                                                mock_get_powerunit, mock_get_battery,
                                                mock_get_firmware):
        """Test that an invalid controller_id raises RuntimeError."""
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

        # Mock send_request to return data without the requested controller ID
        def send_request_side_effect(path, *args, **kwargs):
            mock_response = Mock()
            if 'powerflow' in path.lower() or 'GetPowerFlowRealtimeData' in path:
                mock_response.text = json.dumps({
                    'Body': {
                        'Data': {
                            'Inverters': {
                                '1': {'SOC': 50}
                            }
                        }
                    }
                })
            elif 'storage' in path.lower() or 'GetStorageRealtimeData' in path:
                # Only has controller ID '0', not '99'
                mock_response.text = json.dumps({
                    'Body': {
                        'Data': {
                            '0': {
                                'Controller': {'DesignedCapacity': 10000}
                            }
                        }
                    }
                })
            return mock_response
        mock_send_request.side_effect = send_request_side_effect

        # Try to create inverter with invalid controller_id
        config = self.base_config.copy()
        config['fronius_controller_id'] = '99'

        with self.assertRaises(RuntimeError) as context:
            FroniusWR(config)

        self.assertIn('Invalid fronius_controller_id', str(context.exception))
        self.assertIn('99', str(context.exception))

    @patch('batcontrol.inverter.fronius.FroniusWR.get_firmware_version')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_battery_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_powerunit_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.backup_time_of_use')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_solar_api_active')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_allow_grid_charging')
    @patch('batcontrol.inverter.fronius.FroniusWR.send_request')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_time_of_use')
    def test_set_mode_limit_battery_charge(self, mock_set_tou, mock_send_request,
                                          mock_set_allow, mock_set_solar, mock_backup_tou,
                                          mock_get_powerunit, mock_get_battery, mock_get_firmware):
        """Test that limit battery charge mode sets correct TimeOfUse rule"""
        self._setup_mocks(mock_get_firmware, mock_get_battery, mock_get_powerunit,
                         mock_send_request, inverter_id='1', controller_id='0')
        mock_set_tou.return_value = Mock()

        inverter = FroniusWR(self.base_config)

        # Set mode to limit battery charge with max rate 2000W
        inverter.set_mode_limit_battery_charge(2000)

        # Verify set_time_of_use was called with correct parameters
        mock_set_tou.assert_called_once()
        tou_list = mock_set_tou.call_args[0][0]

        self.assertEqual(len(tou_list), 1)
        self.assertEqual(tou_list[0]['Power'], 2000)
        self.assertEqual(tou_list[0]['ScheduleType'], 'CHARGE_MAX')
        self.assertTrue(tou_list[0]['Active'])

    @patch('batcontrol.inverter.fronius.FroniusWR.get_firmware_version')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_battery_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_powerunit_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.backup_time_of_use')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_solar_api_active')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_allow_grid_charging')
    @patch('batcontrol.inverter.fronius.FroniusWR.send_request')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_time_of_use')
    def test_set_mode_limit_battery_charge_zero(self, mock_set_tou, mock_send_request,
                                                mock_set_allow, mock_set_solar, mock_backup_tou,
                                                mock_get_powerunit, mock_get_battery, mock_get_firmware):
        """Test that limit=0 blocks all charging"""
        self._setup_mocks(mock_get_firmware, mock_get_battery, mock_get_powerunit,
                         mock_send_request, inverter_id='1', controller_id='0')
        mock_set_tou.return_value = Mock()

        inverter = FroniusWR(self.base_config)

        # Set mode to limit battery charge with limit=0 (no charging)
        inverter.set_mode_limit_battery_charge(0)

        # Verify set_time_of_use was called with Power=0
        mock_set_tou.assert_called_once()
        tou_list = mock_set_tou.call_args[0][0]

        self.assertEqual(len(tou_list), 1)
        self.assertEqual(tou_list[0]['Power'], 0)
        self.assertEqual(tou_list[0]['ScheduleType'], 'CHARGE_MAX')

    @patch('batcontrol.inverter.fronius.FroniusWR.get_firmware_version')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_battery_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.get_powerunit_config')
    @patch('batcontrol.inverter.fronius.FroniusWR.backup_time_of_use')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_solar_api_active')
    @patch('batcontrol.inverter.fronius.FroniusWR.set_allow_grid_charging')
    @patch('batcontrol.inverter.fronius.FroniusWR.send_request')
    def test_set_mode_limit_battery_charge_negative_value_raises_error(
        self, mock_send_request, mock_set_allow, mock_set_solar, mock_backup_tou,
        mock_get_powerunit, mock_get_battery, mock_get_firmware):
        """Test that negative values raise ValueError"""
        self._setup_mocks(mock_get_firmware, mock_get_battery, mock_get_powerunit,
                         mock_send_request, inverter_id='1', controller_id='0')

        inverter = FroniusWR(self.base_config)

        # Attempt to set negative limit
        with self.assertRaises(ValueError) as context:
            inverter.set_mode_limit_battery_charge(-100)

        self.assertIn('must be >= 0', str(context.exception))


if __name__ == '__main__':
    unittest.main()

