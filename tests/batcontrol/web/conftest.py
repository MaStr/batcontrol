"""Fixtures for the read-only web API tests.

The tests run against a stand-in for the Batcontrol core instead of a real
one: building a real core needs an inverter, a tariff provider and two
forecast providers, while the service layer only ever reads a handful of
cached attributes. The stand-in documents exactly which attributes the API
depends on - if the service starts reading something else, these tests fail
with an AttributeError instead of silently talking to an inverter.
"""
import datetime
import time

import numpy as np
import pytest

from batcontrol.decision_journal import DecisionJournal
from batcontrol.inverter.api_values import InverterValues
from batcontrol.logic.decision_trace import (
    Decision, DecisionRecord, DecisionTrace, Outcome, Reason,
)
from batcontrol.modes import MODE_ALLOW_DISCHARGING, MODE_NAMES
from batcontrol.runtime_state import (
    BatteryState, CalculationResult, ConfiguredParameters, PeakShavingState,
    SolarMetrics,
)
from batcontrol.web.snapshot import ForecastSnapshot

def recent_run_time() -> float:
    """A fresh run time, as the control loop would record it.

    Deliberately relative to now: the API derives "online" from the age of
    the last cycle, so a hardcoded timestamp would make every test go stale
    the moment it is written. The snapshot aligns this to its slot grid
    itself, exactly like the real loop does.
    """
    return time.time()


class FakeInverter:
    """The SoC limits and the cached per-inverter values are read by the API."""

    def __init__(self, min_soc=7.0, max_soc=100.0, inverter_num=0,
                 with_values=True):
        self.min_soc = min_soc
        self.max_soc = max_soc
        self.inverter_num = inverter_num
        self.inverter_type = 'dummy'
        self.api_values = None
        if with_values:
            self.api_values = InverterValues.from_reading(
                soc=87.4, capacity=11000.0, min_soc=min_soc, max_soc=max_soc,
                inverter_id=inverter_num, inverter_type='dummy',
                read_at=recent_run_time(), max_grid_charge_rate=5000,
                max_pv_charge_rate=0, min_pv_charge_rate=0,
                min_charge_rate=500)


class FakeGeneralLogic:
    def __init__(self, always_allow_discharge_limit=0.9):
        self._limit = always_allow_discharge_limit

    def get_always_allow_discharge_limit(self):
        return self._limit


class FakePeakShavingConfig:
    enabled = False
    time_active = True
    price_active = True
    solar_cap_active = False
    allow_full_battery_after = 14
    price_limit = 0.05
    feed_in_limit_w = 0.0


class FakeBatcontrol:  # pylint: disable=too-many-instance-attributes
    """Stand-in exposing exactly the attributes the service layer reads."""

    def __init__(self, *, with_data=True, run_time=None):
        run_time = recent_run_time() if run_time is None else run_time
        self.time_resolution = 60
        self.timezone = 'Europe/Berlin'
        self.evaluation_interval_seconds = 180
        self.last_run_time = run_time if with_data else 0

        self.last_mode = MODE_ALLOW_DISCHARGING
        self.last_control_source = 'optimizer'
        self.last_charge_rate = 0
        self._limit_battery_charge_rate = -1
        self.api_overwrite = False
        self.discharge_blocked = False
        self._grid_charge_locked = False


        self.min_price_difference = 0.05
        self.min_price_difference_rel = 0.10
        self.max_charging_from_grid_limit = 0.89
        self.min_grid_charge_soc = None
        self.preserve_min_grid_charge_soc = False
        self.production_offset_percent = 1.0
        self.batconfig = {'type': 'default'}

        self.inverter = FakeInverter()
        self.general_logic = FakeGeneralLogic()
        self.peak_shaving_config = FakePeakShavingConfig()
        self.decision_journal = DecisionJournal()

        self.battery_state = BatteryState(
            soc=87.4,
            max_capacity=11000.0,
            stored_energy=9614.0,
            stored_usable_energy=8844.0,
            free_capacity=1386.0,
            min_soc=self.inverter.min_soc,
            max_soc=self.inverter.max_soc,
            always_allow_discharge_limit_capacity=9900.0,
        )
        self.configured_parameters = ConfiguredParameters(
            min_price_difference=0.05,
            min_price_difference_rel=0.10,
            always_allow_discharge_limit=0.9,
            max_charging_from_grid_limit=0.89,
            production_offset_percent=1.0,
            peak_shaving_enabled=False,
            peak_shaving_allow_full_battery_after=14,
            peak_shaving_price_limit=0.05,
        )
        self.calculation_result = None
        self.solar_metrics = None
        self.peak_shaving_state = None
        if with_data:
            self.calculation_result = CalculationResult(
                reserved_energy=1500.0,
                reserved_energy_effective=1500.0,
                min_dynamic_price_difference=0.0612,
            )
            self.solar_metrics = SolarMetrics(
                solar_active=False, solar_surplus_wh=1210.0,
                pv_start_battery_wh=7944.0, forecast_min_battery_wh=7944.0)
            self.peak_shaving_state = PeakShavingState(
                enabled=False, active=False, suspended_by=None,
                charge_limit_w=-1)
        self.forecast_snapshot = None
        if with_data:
            self.forecast_snapshot = ForecastSnapshot.build(
                run_time=run_time,
                interval_minutes=self.time_resolution,
                prices=np.array([0.308, 0.254, 0.201, 0.350]),
                consumption=np.array([450.0, 420.0, 400.0, 500.0]),
                production=np.array([0.0, 120.0, 900.0, 1500.0]),
                net_consumption=np.array([450.0, 300.0, -500.0, -1000.0]),
            )

    def record_decision(self, mode=MODE_ALLOW_DISCHARGING):
        """Put one committed trace into the journal.

        Mirrors what core.__commit_decision does: a decisive step plus the
        closing MODE record, because status_text() needs that record.
        """
        # With a timestamp, like core.__new_trace() - without one the API
        # reports decided_at: null, which is not a state the real loop
        # produces.
        trace = DecisionTrace(timestamp=datetime.datetime.now())
        trace.step(Decision.DISCHARGE, Outcome.ALLOWED,
                   Reason.ALWAYS_ALLOW_DISCHARGE_LIMIT, decisive=True,
                   stored_energy=9614.0, reserved_energy=1500.0)
        decided_by = trace.decisive_record()
        inputs = dict(decided_by.inputs)
        inputs.update(mode=mode, control_source='optimizer',
                      decided_by=decided_by.decision, value=None)
        trace.add(DecisionRecord(
            decision=Decision.MODE,
            outcome=MODE_NAMES.get(mode, str(mode)),
            reason=decided_by.reason,
            inputs=inputs,
        ))
        self.decision_journal.commit(trace, mode, 'optimizer', None)
        return trace


@pytest.fixture(name='fake_bc')
def fixture_fake_bc():
    """A core stand-in with one completed control cycle."""
    return FakeBatcontrol()


@pytest.fixture(name='empty_bc')
def fixture_empty_bc():
    """A core stand-in before the first control cycle."""
    return FakeBatcontrol(with_data=False)


@pytest.fixture(name='client')
def fixture_client(fake_bc):
    """TestClient against the real application."""
    from fastapi.testclient import TestClient

    from batcontrol.web.app import create_app
    from batcontrol.web.config import ApiConfig
    # docs_enabled: several tests read the contract from /openapi.json.
    return TestClient(create_app(fake_bc, ApiConfig(docs_enabled=True)),
                      base_url='http://127.0.0.1')
