"""Tests for the simplified ResilientInverterWrapper.

Behaviour under test:
1. Before the first successful set_mode_*: errors propagate unchanged (fail-fast).
2. After initialization: any failure raises InverterCommunicationError so the
   caller can skip the cycle. No caching - nothing is returned on failure.
3. After the outage tolerance expires: InverterOutageError is raised.
4. Successful calls reset the outage tracking (automatic recovery).
"""

import pytest
import time
from unittest.mock import Mock

from batcontrol.inverter.resilient_wrapper import ResilientInverterWrapper
from batcontrol.inverter.exceptions import (
    InverterCommunicationError,
    InverterOutageError,
)


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
        self.refresh_calls = 0

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
        self.refresh_calls += 1
        self._maybe_fail()

    def shutdown(self):
        pass


# ---------------------------------------------------------------------------
# Fail-fast before initialization
# ---------------------------------------------------------------------------

class TestPreInitFailFast:
    def test_read_failure_before_init_propagates(self):
        w = ResilientInverterWrapper(MockInverter(should_fail=True))
        with pytest.raises(ConnectionError):
            w.get_SOC()

    def test_command_failure_before_init_propagates(self):
        w = ResilientInverterWrapper(MockInverter(should_fail=True))
        with pytest.raises(ConnectionError):
            w.set_mode_allow_discharge()

    def test_successful_set_mode_marks_initialized(self):
        w = ResilientInverterWrapper(MockInverter())
        assert w._initialized is False
        w.set_mode_allow_discharge()
        assert w._initialized is True

    def test_read_success_does_not_mark_initialized(self):
        w = ResilientInverterWrapper(MockInverter())
        w.get_SOC()
        assert w._initialized is False


# ---------------------------------------------------------------------------
# After init: failures raise InverterCommunicationError
# ---------------------------------------------------------------------------

class TestCommunicationError:
    def _init(self, **kw):
        inv = MockInverter()
        w = ResilientInverterWrapper(inv, **kw)
        w.set_mode_allow_discharge()  # initialize
        return inv, w

    def test_read_failure_raises_communication_error(self):
        inv, w = self._init(outage_tolerance_seconds=60)
        inv.should_fail = True
        with pytest.raises(InverterCommunicationError):
            w.get_SOC()

    def test_command_failure_raises_communication_error(self):
        inv, w = self._init(outage_tolerance_seconds=60)
        inv.should_fail = True
        with pytest.raises(InverterCommunicationError):
            w.set_mode_force_charge(5000)
        # The command was actually attempted on the inverter
        assert ('force_charge', 5000) in inv.set_mode_calls

    def test_outage_start_recorded_on_first_failure(self):
        inv, w = self._init(outage_tolerance_seconds=60)
        assert w._outage_start is None
        inv.should_fail = True
        with pytest.raises(InverterCommunicationError):
            w.get_SOC()
        assert w._outage_start is not None

    def test_recovery_resets_outage_tracking(self):
        inv, w = self._init(outage_tolerance_seconds=60)
        inv.should_fail = True
        with pytest.raises(InverterCommunicationError):
            w.get_SOC()
        assert w._outage_start is not None

        inv.should_fail = False
        assert w.get_SOC() == 75.0
        assert w._outage_start is None


# ---------------------------------------------------------------------------
# Outage tolerance -> InverterOutageError
# ---------------------------------------------------------------------------

class TestOutageError:
    def _init(self, **kw):
        inv = MockInverter()
        w = ResilientInverterWrapper(inv, **kw)
        w.set_mode_allow_discharge()
        return inv, w

    def test_read_raises_outage_error_after_tolerance(self):
        inv, w = self._init(outage_tolerance_seconds=0.1)
        inv.should_fail = True
        with pytest.raises(InverterCommunicationError):
            w.get_SOC()  # first failure, within tolerance
        time.sleep(0.15)
        with pytest.raises(InverterOutageError):
            w.get_SOC()  # tolerance exceeded

    def test_command_raises_outage_error_after_tolerance(self):
        inv, w = self._init(outage_tolerance_seconds=0.1)
        inv.should_fail = True
        with pytest.raises(InverterCommunicationError):
            w.set_mode_allow_discharge()
        time.sleep(0.15)
        with pytest.raises(InverterOutageError):
            w.set_mode_allow_discharge()

    def test_outage_error_is_communication_error_subclass(self):
        # core.run catches InverterCommunicationError; InverterOutageError must
        # NOT be swallowed there, so it must not be a subclass of it.
        assert not issubclass(InverterOutageError, InverterCommunicationError)


# ---------------------------------------------------------------------------
# refresh_api_values is best-effort
# ---------------------------------------------------------------------------

class TestRefreshApiValues:
    def test_refresh_never_raises(self):
        inv = MockInverter()
        w = ResilientInverterWrapper(inv)
        w.set_mode_allow_discharge()
        inv.should_fail = True
        # Must not raise even though the inverter is unreachable
        w.refresh_api_values()
        assert inv.refresh_calls == 1

    def test_refresh_does_not_start_outage_tracking(self):
        inv = MockInverter()
        w = ResilientInverterWrapper(inv)
        w.set_mode_allow_discharge()
        inv.should_fail = True
        w.refresh_api_values()
        assert w._outage_start is None


# ---------------------------------------------------------------------------
# All set_mode variants
# ---------------------------------------------------------------------------

class TestSetModeVariants:
    def test_all_set_mode_variants_pass_through(self):
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
# Attribute forwarding / passthrough
# ---------------------------------------------------------------------------

class TestForwarding:
    def test_common_attributes_forwarded(self):
        w = ResilientInverterWrapper(MockInverter())
        assert w.min_soc == 10
        assert w.max_soc == 95
        assert w.max_grid_charge_rate == 5000

    def test_unknown_attribute_forwarded_via_getattr(self):
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
    def test_outage_then_recovery(self):
        """A few failed cycles, then the inverter comes back."""
        inv = MockInverter()
        w = ResilientInverterWrapper(inv, outage_tolerance_seconds=60)
        w.set_mode_allow_discharge()
        assert w.get_SOC() == 75.0

        # Inverter goes offline - each cycle raises a comm error
        inv.should_fail = True
        for _ in range(3):
            with pytest.raises(InverterCommunicationError):
                w.get_SOC()

        # Inverter recovers
        inv.should_fail = False
        assert w.get_SOC() == 75.0
        assert w._outage_start is None

    def test_permanent_outage_terminates(self):
        inv = MockInverter()
        w = ResilientInverterWrapper(inv, outage_tolerance_seconds=0.1)
        w.set_mode_allow_discharge()
        w.get_SOC()

        inv.should_fail = True
        with pytest.raises(InverterCommunicationError):
            w.get_SOC()

        time.sleep(0.15)
        with pytest.raises(InverterOutageError):
            w.get_SOC()
