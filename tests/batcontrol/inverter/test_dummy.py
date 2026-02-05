import pytest
import sys
import os

# Add the src directory to Python path for testing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'src'))

from batcontrol.inverter.dummy import Dummy
from batcontrol.inverter.inverter import Inverter
from batcontrol.inverter.resilient_wrapper import ResilientInverterWrapper


class TestDummyInverter:
    """Test the Dummy inverter implementation"""

    def test_dummy_initialization(self):
        """Test that dummy inverter initializes with correct default values"""
        config = {'max_grid_charge_rate': 5000}
        dummy = Dummy(config)

        assert dummy.get_capacity() == 10000  # 10 kWh in Wh
        assert dummy.get_SOC() == 65.0
        assert dummy.mode == 'allow_discharge'
        assert dummy.min_soc == 10
        assert dummy.max_soc == 95

    def test_dummy_mode_changes(self):
        """Test that mode changes work correctly"""
        config = {'max_grid_charge_rate': 5000}
        dummy = Dummy(config)

        # Test force charge mode
        dummy.set_mode_force_charge(1000)
        assert dummy.mode == 'force_charge'

        # Test allow discharge mode
        dummy.set_mode_allow_discharge()
        assert dummy.mode == 'allow_discharge'

        # Test avoid discharge mode
        dummy.set_mode_avoid_discharge()
        assert dummy.mode == 'avoid_discharge'

        # Test limit battery charge mode
        dummy.set_mode_limit_battery_charge(2000)
        assert dummy.mode == 'limit_battery_charge'

        # Test with zero charge rate
        dummy.set_mode_limit_battery_charge(0)
        assert dummy.mode == 'limit_battery_charge'

    def test_dummy_mqtt_activation(self):
        """Test that MQTT activation doesn't crash (it's ignored)"""
        config = {'max_grid_charge_rate': 5000}
        dummy = Dummy(config)

        # Should not crash even with None
        dummy.activate_mqtt(None)
        dummy.refresh_api_values()

    def test_dummy_shutdown(self):
        """Test that shutdown doesn't crash"""
        config = {'max_grid_charge_rate': 5000}
        dummy = Dummy(config)

        # Should not crash
        dummy.shutdown()

    def test_dummy_factory_creation(self):
        """Test that the factory can create a dummy inverter wrapped in resilient wrapper"""
        config = {
            'type': 'dummy',
            'max_grid_charge_rate': 3000,
            'enable_resilient_wrapper': True
        }

        inverter = Inverter.create_inverter(config)
        # Factory now returns ResilientInverterWrapper
        assert isinstance(inverter, ResilientInverterWrapper)
        # Wrapped inverter should be Dummy
        assert isinstance(inverter.wrapped_inverter, Dummy)
        assert inverter.max_grid_charge_rate == 3000

    def test_dummy_factory_creation_case_insensitive(self):
        """Test that the factory works with different case"""
        config = {
            'type': 'DUMMY',
            'max_grid_charge_rate': 3000,
            'enable_resilient_wrapper': True
        }

        inverter = Inverter.create_inverter(config)
        # Factory now returns ResilientInverterWrapper
        assert isinstance(inverter, ResilientInverterWrapper)
        # Wrapped inverter should be Dummy
        assert isinstance(inverter.wrapped_inverter, Dummy)

    def test_dummy_energy_calculations(self):
        """Test energy-related calculations work"""
        config = {'max_grid_charge_rate': 5000}
        dummy = Dummy(config)

        # Test that inherited methods work
        stored_energy = dummy.get_stored_energy()
        assert stored_energy == 6500  # 65% of 10000 Wh

        stored_usable_energy = dummy.get_stored_usable_energy()
        assert stored_usable_energy == 5500  # 65% - 10% of 10000 Wh

        free_capacity = dummy.get_free_capacity()
        assert free_capacity == 3000  # (95% - 65%) of 10000 Wh

    def test_dummy_factory_with_resilient_wrapper_disabled(self):
        """Test that factory returns unwrapped inverter when resilient wrapper is disabled"""
        config = {
            'type': 'dummy',
            'max_grid_charge_rate': 3000,
            'enable_resilient_wrapper': False
        }
        
        inverter = Inverter.create_inverter(config)
        # When disabled, factory returns the raw Dummy inverter
        assert isinstance(inverter, Dummy)
        assert not isinstance(inverter, ResilientInverterWrapper)
        assert inverter.max_grid_charge_rate == 3000

    def test_dummy_factory_with_resilient_wrapper_enabled_explicitly(self):
        """Test that factory returns wrapped inverter when resilient wrapper is explicitly enabled"""
        config = {
            'type': 'dummy',
            'max_grid_charge_rate': 3000,
            'enable_resilient_wrapper': True
        }
        
        inverter = Inverter.create_inverter(config)
        # When enabled, factory returns ResilientInverterWrapper
        assert isinstance(inverter, ResilientInverterWrapper)
        assert isinstance(inverter.wrapped_inverter, Dummy)