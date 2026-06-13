"""Tests for ResilientInverterWrapper.

Behaviour under test:
1. Before first successful set_mode_*: errors propagate immediately (fail-fast).
2. After initialization: read failures return cached values; commands are discarded.
3. After the outage tolerance expires: InverterOutageError is raised.
4. Automatic recovery when the connection is restored.
"""

import pytest
import time
from unittest.mock import Mock

from batcontrol.inverter.resilient_wrapper import ResilientInverterWrapper
from batcontrol.inverter.exceptions import InverterOutageError


class MockInverter:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.min_soc = 10
        self.max_soc = 95
        self.mqtt_api = None
        self.capacity = 10000
        self.inverter_num = 0
        self.max_grid_charge_rate = 5000
        self.max_pv_charge_rate = 0
        self.soc_calls = 0
        self.set_mode_calls = []

    def _maybe_fail(self):
        if self.should_fail:
            raise ConnectionError("Inverter unreachable")

    def get_SOC(self):
        self.soc_calls += 1
        self._maybe_fail()
        return 75.0

    def get_stored_energy(self):
        self._maybe_fail()
        return 7500.0

    def get_stored_usable_energy(self):
        self._maybe_fail()
        return 6500.0

    def get_capacity(self):
        self._maybe_fail()
        return 10000.0

    def get_free_capacity(self):
        self._maybe_fail()
        return 2500.0

    def get_max_capacity(self):
        self._maybe_fail()
        return 9500.0

    def set_mode_force_charge(self, chargerate):
        self.set_mode_calls.append(('force_charge', chargerate))
        self._maybe_fail()

    def set_mode_avoid_discharge(self):
        self.set_mode_calls.append(('avoid_discharge',))
        self._maybe_fail()

    def set_mode_allow_discharge(self):
        self.set_mode_calls.append(('allow_discharge',))
        self._maybe_fail()

    def set_mode_limit_battery_charge(self, rate):
        self.set_mode_calls.append(('limit_battery_charge', rate))
        self._maybe_fail()

    def activate_mqtt(self, api):
        self.mqtt_api = api

    def refresh_api_values(self):
        self._maybe_fail()

    def shutdown(self):
        pass


# ---------------------------------------------------------------------------
# Fail-fast before initialization
# ---------------------------------------------------------------------------

class TestPreInitFailFast:
    def test_read_failure_before_init_propagates(self):
        inv = MockInverter(should_fail=True)
        w = ResilientInverterWrapper(inv)
        with pytest.raises(ConnectionError):
            w.get_SOC()

    def test_command_failure_before_init_propagates(self):
        inv = MockInverter(should_fail=True)
        w = ResilientInverterWrapper(inv)
        with pytest.raises(ConnectionError):
            w.set_mode_allow_discharge()

    def test_successful_set_mode_marks_initialized(self):
        inv = MockInverter()
        w = ResilientInverterWrapper(inv)
        assert w._initialized is False
        w.set_mode_allow_discharge()
        assert w._initialized is True

    def test_read_success_does_not_mark_initialized(self):
        inv = MockInverter()
        w = ResilientInverterWrapper(inv)
        w.get_SOC()
        assert w._initialized is False


# ---------------------------------------------------------------------------
# Cache behaviour during outages
# ---------------------------------------------------------------------------

class TestCacheDuringOutage:
    def _init(self, **kw):
        inv = MockInverter()
        w = ResilientInverterWrapper(inv, **kw)
        w.set_mode_allow_discharge()
        w.get_SOC()  # prime cache
        return inv, w

    def test_read_returns_cached_value_after_failure(self):
        inv, w = self._init(outage_tolerance_seconds=60)
        inv.should_fail = True
        assert w.get_SOC() == 75.0

    def test_read_returns_cached_value_during_backoff(self):
        inv, w = self._init(outage_tolerance_seconds=60, retry_backoff_seconds=60)
        inv.should_fail = True
        w.get_SOC()  # triggers failure, starts backoff
        inv.should_fail = False
        # still in backoff - must use cache, not call inverter
        calls_before = inv.soc_calls
        assert w.get_SOC() == 75.0
        assert inv.soc_calls == calls_before  # no new call

    def test_soc_default_used_when_cache_empty(self):
        inv, w = self._init(outage_tolerance_seconds=60)
        del w._cache['soc']  # clear soc from cache
        inv.should_fail = True
        assert w.get_SOC() == 50.0  # default

    def test_outage_start_set_on_first_failure(self):
        inv, w = self._init(outage_tolerance_seconds=60)
        assert w._outage_start is None
        inv.should_fail = True
        w.get_SOC()
        assert w._outage_start is not None

    def test_recovery_resets_outage_state(self):
        inv, w = self._init(outage_tolerance_seconds=60, retry_backoff_seconds=0.05)
        inv.should_fail = True
        w.get_SOC()
        assert w._outage_start is not None

        time.sleep(0.1)
        inv.should_fail = False
        w.get_SOC()

        assert w._outage_start is None
        assert not w._in_backoff()

    def test_outage_error_after_tolerance_exceeded(self):
        inv, w = self._init(outage_tolerance_seconds=0.1, retry_backoff_seconds=0.05)
        inv.should_fail = True
        w.get_SOC()          # first failure, starts backoff
        time.sleep(0.2)      # exceed both backoff and tolerance
        with pytest.raises(InverterOutageError):
            w.get_SOC()


# ---------------------------------------------------------------------------
# Command behaviour during outages
# ---------------------------------------------------------------------------

class TestCommandsDuringOutage:
    def _init(self, **kw):
        inv = MockInverter()
        w = ResilientInverterWrapper(inv, **kw)
        w.set_mode_allow_discharge()
        return inv, w

    def test_command_discarded_during_backoff(self):
        inv, w = self._init(outage_tolerance_seconds=60, retry_backoff_seconds=60)
        inv.should_fail = True
        w.get_SOC()  # triggers failure, starts backoff
        inv.should_fail = False

        calls_before = len(inv.set_mode_calls)
        result = w.set_mode_avoid_discharge()

        assert result is None
        assert len(inv.set_mode_calls) == calls_before  # not sent

    def test_command_failure_returns_none_not_crash(self):
        inv, w = self._init(outage_tolerance_seconds=60)
        inv.should_fail = True
        result = w.set_mode_force_charge(5000)
        assert result is None
        # The call was attempted
        assert ('force_charge', 5000) in inv.set_mode_calls

    def test_command_raises_outage_error_after_tolerance(self):
        inv, w = self._init(outage_tolerance_seconds=0.1, retry_backoff_seconds=0.05)
        inv.should_fail = True
        w.set_mode_allow_discharge()  # first failure, returns None
        time.sleep(0.2)
        with pytest.raises(InverterOutageError):
            w.set_mode_allow_discharge()

    def test_all_set_mode_variants_work(self):
        inv = MockInverter()
        w = ResilientInverterWrapper(inv)
        w.set_mode_force_charge(5000)
        w.set_mode_avoid_discharge()
        w.set_mode_allow_discharge()
        w.set_mode_limit_battery_charge(3000)
        assert ('force_charge', 5000) in inv.set_mode_calls
        assert ('avoid_discharge',) in inv.set_mode_calls
        assert ('allow_discharge',) in inv.set_mode_calls
        assert ('limit_battery_charge', 3000) in inv.set_mode_calls
        assert w._initialized is True


# ---------------------------------------------------------------------------
# Backoff mechanics
# ---------------------------------------------------------------------------

class TestBackoff:
    def test_backoff_active_after_failure(self):
        inv = MockInverter()
        w = ResilientInverterWrapper(inv, outage_tolerance_seconds=60, retry_backoff_seconds=60)
        w.set_mode_allow_discharge()
        w.get_SOC()
        inv.should_fail = True
        w.get_SOC()
        assert w._in_backoff() is True

    def test_backoff_expires_and_retries(self):
        inv = MockInverter()
        w = ResilientInverterWrapper(inv, outage_tolerance_seconds=60, retry_backoff_seconds=0.1)
        w.set_mode_allow_discharge()
        w.get_SOC()
        inv.should_fail = True
        w.get_SOC()
        calls_after_fail = inv.soc_calls

        time.sleep(0.15)
        inv.should_fail = False
        w.get_SOC()
        assert inv.soc_calls > calls_after_fail

    def test_recovery_clears_backoff(self):
        inv = MockInverter()
        w = ResilientInverterWrapper(inv, outage_tolerance_seconds=60, retry_backoff_seconds=0.1)
        w.set_mode_allow_discharge()
        w.get_SOC()
        inv.should_fail = True
        w.get_SOC()
        assert w._in_backoff() is True

        time.sleep(0.15)
        inv.should_fail = False
        w.get_SOC()
        assert w._in_backoff() is False


# ---------------------------------------------------------------------------
# Outage status / diagnostics
# ---------------------------------------------------------------------------

class TestOutageStatus:
    def test_connected_state(self):
        inv = MockInverter()
        w = ResilientInverterWrapper(inv)
        w.set_mode_allow_discharge()
        s = w.get_outage_status()
        assert s['is_connected'] is True
        assert s['initialization_complete'] is True
        assert s['in_backoff_period'] is False

    def test_outage_state(self):
        inv = MockInverter()
        w = ResilientInverterWrapper(inv, outage_tolerance_seconds=60)
        w.set_mode_allow_discharge()
        w.get_SOC()
        inv.should_fail = True
        w.get_SOC()

        s = w.get_outage_status()
        assert s['is_connected'] is False
        assert s['in_backoff_period'] is True
        assert s['time_until_retry_seconds'] > 0

    def test_backoff_info_in_status(self):
        inv = MockInverter()
        w = ResilientInverterWrapper(inv, outage_tolerance_seconds=60, retry_backoff_seconds=1.0)
        w.set_mode_allow_discharge()
        w.get_SOC()
        inv.should_fail = True
        w.get_SOC()

        s = w.get_outage_status()
        assert s['retry_backoff_seconds'] == 1.0
        assert s['time_until_retry_seconds'] > 0


# ---------------------------------------------------------------------------
# Attribute forwarding
# ---------------------------------------------------------------------------

class TestAttributeForwarding:
    def test_common_attributes_forwarded(self):
        inv = MockInverter()
        w = ResilientInverterWrapper(inv)
        assert w.min_soc == 10
        assert w.max_soc == 95
        assert w.max_grid_charge_rate == 5000

    def test_unknown_attributes_forwarded_via_getattr(self):
        inv = MockInverter()
        inv.custom_attr = "hello"
        w = ResilientInverterWrapper(inv)
        assert w.custom_attr == "hello"

    def test_wrapped_inverter_accessible(self):
        inv = MockInverter()
        w = ResilientInverterWrapper(inv)
        assert w.wrapped_inverter is inv

    def test_activate_mqtt_forwarded(self):
        inv = MockInverter()
        w = ResilientInverterWrapper(inv)
        api = Mock()
        w.activate_mqtt(api)
        assert inv.mqtt_api is api


# ---------------------------------------------------------------------------
# Integration scenarios
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_firmware_upgrade_and_recovery(self):
        """Simulate: outage -> cached reads -> recovery."""
        inv = MockInverter()
        w = ResilientInverterWrapper(inv, outage_tolerance_seconds=1.0, retry_backoff_seconds=0.05)
        w.set_mode_allow_discharge()
        assert w.get_SOC() == 75.0

        inv.should_fail = True
        for _ in range(3):
            assert w.get_SOC() == 75.0  # cache
            time.sleep(0.02)

        time.sleep(0.1)  # backoff expires
        inv.should_fail = False
        assert w.get_SOC() == 75.0
        assert w._outage_start is None

    def test_permanent_outage_raises(self):
        """Simulate: outage exceeding tolerance -> InverterOutageError."""
        inv = MockInverter()
        w = ResilientInverterWrapper(inv, outage_tolerance_seconds=0.1, retry_backoff_seconds=0.05)
        w.set_mode_allow_discharge()
        w.get_SOC()

        inv.should_fail = True
        w.get_SOC()  # first failure

        time.sleep(0.15)
        with pytest.raises(InverterOutageError):
            w.get_SOC()
