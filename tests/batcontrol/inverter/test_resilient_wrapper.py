"""
Tests for the ResilientInverterWrapper class.

These tests verify that the resilient wrapper:
1. Fails fast on first connection attempt (config errors)
2. Uses cached values during temporary outages
3. Raises InverterOutageError after 24-minute timeout
4. Recovers properly when connection is restored
"""

import pytest
import time
from unittest.mock import Mock

from batcontrol.inverter.resilient_wrapper import (
    ResilientInverterWrapper,
    CachedValues,
)
from batcontrol.inverter.exceptions import InverterOutageError


class MockInverter:
    """Mock inverter for testing."""

    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.failure_count = 0
        self.min_soc = 10
        self.max_soc = 95
        self.mqtt_api = None
        self.capacity = 10000
        self.inverter_num = 0
        self.max_grid_charge_rate = 5000
        self.max_pv_charge_rate = 0

        # Track calls
        self.get_soc_calls = 0
        self.set_mode_calls = []

    def get_SOC(self):
        self.get_soc_calls += 1
        if self.should_fail:
            self.failure_count += 1
            raise ConnectionError("Inverter unreachable")
        return 75.0

    def get_stored_energy(self):
        if self.should_fail:
            raise ConnectionError("Inverter unreachable")
        return 7500.0

    def get_stored_usable_energy(self):
        if self.should_fail:
            raise ConnectionError("Inverter unreachable")
        return 6500.0

    def get_capacity(self):
        if self.should_fail:
            raise ConnectionError("Inverter unreachable")
        return 10000.0

    def get_free_capacity(self):
        if self.should_fail:
            raise ConnectionError("Inverter unreachable")
        return 2500.0

    def get_max_capacity(self):
        if self.should_fail:
            raise ConnectionError("Inverter unreachable")
        return 9500.0

    def set_mode_force_charge(self, chargerate):
        self.set_mode_calls.append(('force_charge', chargerate))
        if self.should_fail:
            raise ConnectionError("Inverter unreachable")

    def set_mode_avoid_discharge(self):
        self.set_mode_calls.append(('avoid_discharge',))
        if self.should_fail:
            raise ConnectionError("Inverter unreachable")

    def set_mode_allow_discharge(self):
        self.set_mode_calls.append(('allow_discharge',))
        if self.should_fail:
            raise ConnectionError("Inverter unreachable")

    def set_mode_limit_battery_charge(self, limit_charge_rate):
        self.set_mode_calls.append(('limit_battery_charge', limit_charge_rate))
        if self.should_fail:
            raise ConnectionError("Inverter unreachable")

    def activate_mqtt(self, api):
        self.mqtt_api = api

    def refresh_api_values(self):
        if self.should_fail:
            raise ConnectionError("Inverter unreachable")

    def shutdown(self):
        pass


class TestCachedValues:
    """Tests for the CachedValues dataclass."""

    def test_default_values(self):
        cache = CachedValues()
        assert cache.soc is None
        assert cache.stored_energy is None
        assert cache.capacity is None

    def test_is_valid_when_empty(self):
        cache = CachedValues()
        assert cache.is_valid() is False

    def test_is_valid_with_required_values(self):
        cache = CachedValues(soc=75.0, capacity=10000.0)
        assert cache.is_valid() is True

    def test_is_valid_partial(self):
        cache = CachedValues(soc=75.0)
        assert cache.is_valid() is False


class TestResilientWrapperFirstRun:
    """Tests for first-run behavior (config error detection)."""

    def test_first_run_failure_propagates(self):
        """First connection failure should propagate immediately."""
        mock_inverter = MockInverter(should_fail=True)
        wrapper = ResilientInverterWrapper(mock_inverter)

        with pytest.raises(ConnectionError):
            wrapper.get_SOC()

    def test_first_run_success_sets_flag(self):
        """Successful set_mode should set the initialization flag."""
        mock_inverter = MockInverter(should_fail=False)
        wrapper = ResilientInverterWrapper(mock_inverter)

        # get_SOC doesn't mark initialization complete
        wrapper.get_SOC()
        assert wrapper._initialization_complete is False

        # set_mode marks initialization complete
        wrapper.set_mode_allow_discharge()
        assert wrapper._initialization_complete is True

    def test_first_run_caches_values(self):
        """Successful first run should cache values."""
        mock_inverter = MockInverter(should_fail=False)
        wrapper = ResilientInverterWrapper(mock_inverter)

        wrapper.get_SOC()

        assert wrapper._cache.soc == 75.0


class TestResilientWrapperOutage:
    """Tests for outage handling behavior."""

    def test_subsequent_failure_uses_cache(self):
        """After initialization, failures should use cached values."""
        mock_inverter = MockInverter(should_fail=False)
        wrapper = ResilientInverterWrapper(mock_inverter, outage_tolerance_seconds=60)

        # Initialize with set_mode and cache SOC
        wrapper.set_mode_allow_discharge()
        soc1 = wrapper.get_SOC()
        assert soc1 == 75.0

        # Now fail
        mock_inverter.should_fail = True
        soc2 = wrapper.get_SOC()

        # Should return cached value
        assert soc2 == 75.0
        assert wrapper._consecutive_failures == 1

    def test_failure_tracking_starts_on_first_failure(self):
        """Failure timer should start on first failure after initialization."""
        mock_inverter = MockInverter(should_fail=False)
        wrapper = ResilientInverterWrapper(mock_inverter, outage_tolerance_seconds=60)

        # Initialize with set_mode and cache values
        wrapper.set_mode_allow_discharge()
        wrapper.get_SOC()
        assert wrapper._first_failure_time is None

        # Now fail
        mock_inverter.should_fail = True
        wrapper.get_SOC()

        assert wrapper._first_failure_time is not None

    def test_outage_timeout_raises_error(self):
        """After timeout, should raise InverterOutageError."""
        mock_inverter = MockInverter(should_fail=False)
        wrapper = ResilientInverterWrapper(
            mock_inverter,
            outage_tolerance_seconds=0.1,
            retry_backoff_seconds=0.05  # Short backoff for testing
        )

        # Initialize with set_mode and cache values
        wrapper.set_mode_allow_discharge()
        wrapper.get_SOC()

        # Now fail
        mock_inverter.should_fail = True

        # First failure uses cache
        wrapper.get_SOC()

        # Wait for timeout (and backoff to expire)
        time.sleep(0.2)

        # Next failure should raise InverterOutageError
        with pytest.raises(InverterOutageError) as exc_info:
            wrapper.get_SOC()

        assert exc_info.value.outage_duration_seconds >= 0.1

    def test_recovery_resets_failure_tracking(self):
        """Successful call after failures should reset tracking."""
        mock_inverter = MockInverter(should_fail=False)
        wrapper = ResilientInverterWrapper(
            mock_inverter,
            outage_tolerance_seconds=60,
            retry_backoff_seconds=0.05  # Short backoff for testing
        )

        # Initialize with set_mode
        wrapper.set_mode_allow_discharge()
        wrapper.get_SOC()

        # Fail
        mock_inverter.should_fail = True
        wrapper.get_SOC()
        assert wrapper._first_failure_time is not None
        assert wrapper._consecutive_failures == 1

        # Wait for backoff to expire
        time.sleep(0.1)

        # Recover
        mock_inverter.should_fail = False
        wrapper.get_SOC()

        assert wrapper._first_failure_time is None
        assert wrapper._consecutive_failures == 0


class TestResilientWrapperBackoff:
    """Tests for retry backoff behavior."""

    def test_backoff_skips_inverter_call(self):
        """During backoff period, actual inverter calls should be skipped."""
        mock_inverter = MockInverter(should_fail=False)
        wrapper = ResilientInverterWrapper(
            mock_inverter,
            outage_tolerance_seconds=60,
            retry_backoff_seconds=0.5  # 500ms backoff for testing
        )

        # Initialize with set_mode and cache values
        wrapper.set_mode_allow_discharge()
        wrapper.get_SOC()

        # Now fail
        mock_inverter.should_fail = True
        wrapper.get_SOC()  # This will fail and start backoff
        call_count_after_failure = mock_inverter.get_soc_calls

        # Subsequent calls during backoff should NOT hit the inverter
        for _ in range(3):
            wrapper.get_SOC()

        # Call count should be same (no new calls during backoff)
        assert mock_inverter.get_soc_calls == call_count_after_failure

    def test_backoff_uses_cached_value(self):
        """During backoff period, cached values should be returned."""
        mock_inverter = MockInverter(should_fail=False)
        wrapper = ResilientInverterWrapper(
            mock_inverter,
            outage_tolerance_seconds=60,
            retry_backoff_seconds=0.5
        )

        # Initialize with set_mode and cache value
        wrapper.set_mode_allow_discharge()
        soc1 = wrapper.get_SOC()
        assert soc1 == 75.0

        # Fail to start backoff
        mock_inverter.should_fail = True
        wrapper.get_SOC()

        # During backoff, should return cached value without calling inverter
        soc2 = wrapper.get_SOC()
        assert soc2 == 75.0

    def test_backoff_expires_and_retries(self):
        """After backoff period, should retry actual inverter call."""
        mock_inverter = MockInverter(should_fail=False)
        wrapper = ResilientInverterWrapper(
            mock_inverter,
            outage_tolerance_seconds=60,
            retry_backoff_seconds=0.1  # 100ms backoff
        )

        # Initialize with set_mode and cache values
        wrapper.set_mode_allow_discharge()
        wrapper.get_SOC()

        # Fail to start backoff
        mock_inverter.should_fail = True
        wrapper.get_SOC()
        call_count_after_first_failure = mock_inverter.get_soc_calls

        # Wait for backoff to expire
        time.sleep(0.15)

        # Now call should actually hit the inverter again
        wrapper.get_SOC()
        assert mock_inverter.get_soc_calls > call_count_after_first_failure

    def test_backoff_recovery_resets_backoff(self):
        """When inverter recovers, backoff should be reset."""
        mock_inverter = MockInverter(should_fail=False)
        wrapper = ResilientInverterWrapper(
            mock_inverter,
            outage_tolerance_seconds=60,
            retry_backoff_seconds=0.5
        )

        # Initialize with set_mode
        wrapper.set_mode_allow_discharge()
        wrapper.get_SOC()

        # Fail
        mock_inverter.should_fail = True
        wrapper.get_SOC()
        assert wrapper._is_in_backoff_period() is True

        # Wait for backoff to expire
        time.sleep(0.6)

        # Recover
        mock_inverter.should_fail = False
        wrapper.get_SOC()

        # Backoff should be reset
        assert wrapper._is_in_backoff_period() is False
        assert wrapper._last_failure_time is None

    def test_outage_status_includes_backoff_info(self):
        """Outage status should include backoff information."""
        mock_inverter = MockInverter(should_fail=False)
        wrapper = ResilientInverterWrapper(
            mock_inverter,
            outage_tolerance_seconds=60,
            retry_backoff_seconds=1.0
        )

        # Initialize with set_mode
        wrapper.set_mode_allow_discharge()
        wrapper.get_SOC()
        status = wrapper.get_outage_status()
        assert status['in_backoff_period'] is False

        # Fail
        mock_inverter.should_fail = True
        wrapper.get_SOC()

        status = wrapper.get_outage_status()
        assert status['in_backoff_period'] is True
        assert status['retry_backoff_seconds'] == 1.0
        assert status['time_until_retry_seconds'] > 0


class TestResilientWrapperDefaultValue:
    """Tests for default value handling when no cache is available."""

    def test_soc_default_value_on_first_failure_after_success(self):
        """SOC should have a safe default if no cache available."""
        mock_inverter = MockInverter(should_fail=False)
        wrapper = ResilientInverterWrapper(mock_inverter, outage_tolerance_seconds=60)

        # Initialize with set_mode and cache values
        wrapper.set_mode_allow_discharge()
        wrapper.get_SOC()

        # Clear the cache manually (simulate edge case)
        wrapper._cache.soc = None

        # Now fail
        mock_inverter.should_fail = True
        soc = wrapper.get_SOC()

        # Should return default safe value
        assert soc == 50.0


class TestResilientWrapperWriteOperations:
    """Tests for write operations (mode changes)."""

    def test_set_mode_passes_through(self):
        """Set mode operations should pass through to inverter."""
        mock_inverter = MockInverter(should_fail=False)
        wrapper = ResilientInverterWrapper(mock_inverter)

        # set_mode calls mark initialization complete
        wrapper.set_mode_force_charge(5000)
        assert wrapper._initialization_complete is True

        wrapper.set_mode_avoid_discharge()
        wrapper.set_mode_allow_discharge()

        assert ('force_charge', 5000) in mock_inverter.set_mode_calls
        assert ('avoid_discharge',) in mock_inverter.set_mode_calls
        assert ('allow_discharge',) in mock_inverter.set_mode_calls

    def test_set_mode_failure_during_outage(self):
        """Set mode failures during outage should raise RuntimeError (no cache)."""
        mock_inverter = MockInverter(should_fail=False)
        wrapper = ResilientInverterWrapper(mock_inverter, outage_tolerance_seconds=60)

        # Initialize with set_mode
        wrapper.set_mode_allow_discharge()

        # Now fail
        mock_inverter.should_fail = True

        # Write operations don't have cache, should raise RuntimeError
        # because there's no cached value or default for write operations
        with pytest.raises(RuntimeError) as exc_info:
            wrapper.set_mode_force_charge(5000)

        assert "No cached value or default available" in str(exc_info.value)


class TestResilientWrapperStatus:
    """Tests for status/diagnostic methods."""

    def test_get_outage_status_when_connected(self):
        """Status should show connected state."""
        mock_inverter = MockInverter(should_fail=False)
        wrapper = ResilientInverterWrapper(mock_inverter)

        wrapper.set_mode_allow_discharge()
        status = wrapper.get_outage_status()

        assert status['is_connected'] is True
        assert status['initialization_complete'] is True
        assert status['consecutive_failures'] == 0

    def test_get_outage_status_during_outage(self):
        """Status should show outage state."""
        mock_inverter = MockInverter(should_fail=False)
        wrapper = ResilientInverterWrapper(mock_inverter, outage_tolerance_seconds=60)

        wrapper.set_mode_allow_discharge()
        wrapper.get_SOC()  # Cache a value
        mock_inverter.should_fail = True
        wrapper.get_SOC()

        status = wrapper.get_outage_status()

        assert status['is_connected'] is False
        assert status['consecutive_failures'] == 1
        assert status['outage_duration_seconds'] >= 0


class TestResilientWrapperAttributeForwarding:
    """Tests for attribute forwarding to wrapped inverter."""

    def test_attributes_forwarded(self):
        """Common attributes should be forwarded from wrapped inverter."""
        mock_inverter = MockInverter()
        wrapper = ResilientInverterWrapper(mock_inverter)

        assert wrapper.min_soc == 10
        assert wrapper.max_soc == 95
        assert wrapper.max_grid_charge_rate == 5000

    def test_unknown_attribute_forwarded(self):
        """Unknown attributes should be forwarded via __getattr__."""
        mock_inverter = MockInverter()
        mock_inverter.custom_attr = "test_value"
        wrapper = ResilientInverterWrapper(mock_inverter)

        assert wrapper.custom_attr == "test_value"

    def test_wrapped_inverter_accessible(self):
        """Wrapped inverter should be accessible for advanced use."""
        mock_inverter = MockInverter()
        wrapper = ResilientInverterWrapper(mock_inverter)

        assert wrapper.wrapped_inverter is mock_inverter


class TestResilientWrapperMqtt:
    """Tests for MQTT-related functionality."""

    def test_activate_mqtt_forwards_to_inverter(self):
        """MQTT activation should be forwarded."""
        mock_inverter = MockInverter()
        wrapper = ResilientInverterWrapper(mock_inverter)

        mock_api = Mock()
        wrapper.activate_mqtt(mock_api)

        assert mock_inverter.mqtt_api is mock_api


class TestResilientWrapperIntegration:
    """Integration tests simulating real-world scenarios."""

    def test_firmware_upgrade_scenario(self):
        """Simulate a firmware upgrade with recovery."""
        mock_inverter = MockInverter(should_fail=False)
        wrapper = ResilientInverterWrapper(
            mock_inverter,
            outage_tolerance_seconds=0.5,
            retry_backoff_seconds=0.05  # Short backoff for testing
        )

        # Initialize with set_mode
        wrapper.set_mode_allow_discharge()

        # Normal operation
        soc1 = wrapper.get_SOC()
        assert soc1 == 75.0

        # Firmware upgrade starts - inverter goes offline
        mock_inverter.should_fail = True

        # Multiple calls during outage - should use cache
        for _ in range(5):
            soc = wrapper.get_SOC()
            assert soc == 75.0
            time.sleep(0.02)

        # Wait for backoff to expire
        time.sleep(0.1)

        # Inverter comes back online
        mock_inverter.should_fail = False
        soc2 = wrapper.get_SOC()

        assert soc2 == 75.0
        assert wrapper._first_failure_time is None  # Reset after recovery

    def test_permanent_outage_scenario(self):
        """Simulate a permanent outage exceeding tolerance."""
        mock_inverter = MockInverter(should_fail=False)
        wrapper = ResilientInverterWrapper(
            mock_inverter,
            outage_tolerance_seconds=0.1,
            retry_backoff_seconds=0.05  # Short backoff for testing
        )

        # Initialize with set_mode
        wrapper.set_mode_allow_discharge()

        # Normal operation
        wrapper.get_SOC()

        # Permanent outage
        mock_inverter.should_fail = True

        # First failure uses cache
        wrapper.get_SOC()

        # Wait beyond tolerance (and backoff)
        time.sleep(0.15)

        # Should raise InverterOutageError
        with pytest.raises(InverterOutageError):
            wrapper.get_SOC()
