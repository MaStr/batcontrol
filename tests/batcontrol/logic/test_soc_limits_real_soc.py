"""The SoC values of the configuration are real states of charge.

always_allow_discharge_limit, max_charging_from_grid_limit and
min_grid_charge_soc: 0.9 means 90 % SoC, whatever MAX_SOC is set to. A
configured value must not depend on a third parameter.

The logic used to take them as ratios of ``max_capacity``, which is reduced
by MAX_SOC. With MAX_SOC 95 % the battery counted as "always allow
discharge" from 85.5 % on - while the external discharge block (evcc), which
checks the SoC, switched at 90 % - and a grid charge limit of 0.80 stopped
at 76 %.
"""
import datetime

import numpy as np
import pytest

from batcontrol.logic.common import CommonLogic
from batcontrol.logic.decision_trace import Reason
from batcontrol.logic.default import DefaultLogic
from batcontrol.logic.logic_interface import (
    CalculationInput,
    CalculationParameters,
)

GROSS_CAPACITY = 10000.0   # Wh at 100 % SoC
MAX_SOC = 95.0             # percent
MAX_CAPACITY = GROSS_CAPACITY * MAX_SOC / 100
TS = datetime.datetime(2025, 6, 20, 12, 0, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture(name='common')
def fixture_common():
    CommonLogic._instance = None  # pylint: disable=protected-access
    common = CommonLogic.get_instance(
        charge_rate_multiplier=1.1,
        always_allow_discharge_limit=0.9,
        max_capacity=MAX_CAPACITY)
    yield common
    CommonLogic._instance = None  # pylint: disable=protected-access


def _stored_energy(soc: float) -> float:
    """Stored energy as the inverter reports it: SoC of the full capacity."""
    return soc / 100 * GROSS_CAPACITY


def test_limit_is_the_real_soc_with_a_reduced_max_soc(common):
    common.gross_capacity = GROSS_CAPACITY

    assert common.get_always_allow_discharge_energy() == pytest.approx(9000)
    assert not common.is_discharge_always_allowed_capacity(_stored_energy(89.9))
    assert common.is_discharge_always_allowed_capacity(_stored_energy(90))


@pytest.mark.parametrize('soc', [80, 85.5, 86, 89.9, 90, 90.1, 95])
def test_energy_and_soc_check_agree(common, soc):
    """Both definitions of "always allow discharge" decide the same."""
    common.gross_capacity = GROSS_CAPACITY

    assert (common.is_discharge_always_allowed_capacity(_stored_energy(soc))
            == common.is_discharge_always_allowed_soc(soc))


def test_unknown_gross_capacity_falls_back_to_max_capacity(common):
    for unknown in (None, 0, -1):
        common.gross_capacity = unknown
        assert common.get_always_allow_discharge_energy() == pytest.approx(
            MAX_CAPACITY * 0.9)


def _logic(gross_capacity, max_charging_from_grid_limit=0.79,
           min_grid_charge_soc=None):
    logic = DefaultLogic(timezone=datetime.timezone.utc, interval_minutes=60)
    logic.set_calculation_parameters(CalculationParameters(
        max_charging_from_grid_limit=max_charging_from_grid_limit,
        min_price_difference=0.05,
        min_price_difference_rel=0.2,
        max_capacity=MAX_CAPACITY,
        gross_capacity=gross_capacity,
        min_grid_charge_soc=min_grid_charge_soc,
    ))
    return logic


def _calc_input(soc, consumption=3000.0):
    """Expensive hours ahead, no PV: the battery is held back unless it is
    in the always-allow-discharge region."""
    stored = _stored_energy(soc)
    return CalculationInput(
        production=np.zeros(4),
        consumption=np.array([consumption] * 4),
        prices=np.array([0.20, 0.40, 0.40, 0.40]),
        stored_energy=stored,
        stored_usable_energy=stored,
        free_capacity=MAX_CAPACITY - stored,
    )


def _discharge_reason(logic):
    return [record.reason for record in logic.get_decision_trace().records
            if record.decisive][0]


def test_logic_holds_the_battery_below_the_real_limit(common):
    """88 % SoC with MAX_SOC 95 %: above 85.5 % (limit of max_capacity),
    below the configured 90 %. Regression: discharge was always allowed."""
    del common
    logic = _logic(GROSS_CAPACITY)

    assert logic.calculate(_calc_input(88), TS)

    assert logic.get_inverter_control_settings().allow_discharge is False
    assert _discharge_reason(logic) != Reason.ALWAYS_ALLOW_DISCHARGE_LIMIT


def test_logic_always_allows_discharge_from_the_real_limit(common):
    del common
    logic = _logic(GROSS_CAPACITY)

    assert logic.calculate(_calc_input(90), TS)

    assert logic.get_inverter_control_settings().allow_discharge is True
    assert _discharge_reason(logic) == Reason.ALWAYS_ALLOW_DISCHARGE_LIMIT
    record = [r for r in logic.get_decision_trace().records if r.decisive][0]
    assert record.inputs['always_allow_discharge_energy'] == pytest.approx(9000)


# ---- max_charging_from_grid_limit --------------------------------------- #

def test_grid_charge_limit_is_the_real_soc(common):
    del common
    logic = _logic(GROSS_CAPACITY, max_charging_from_grid_limit=0.8)

    assert logic._grid_charge_limit_energy() == pytest.approx(8000)
    assert logic._grid_charge_limit_ratio() == pytest.approx(0.8)


def test_grid_charging_continues_up_to_the_real_limit(common):
    """78 % SoC with MAX_SOC 95 % and a limit of 0.80: above 76 % (limit of
    max_capacity), below the configured 80 %. Regression: grid charging had
    already stopped."""
    del common
    logic = _logic(GROSS_CAPACITY, max_charging_from_grid_limit=0.8)

    assert logic.calculate(_calc_input(78), TS)

    assert logic.get_inverter_control_settings().charge_from_grid is True


def test_grid_charging_stops_at_the_real_limit(common):
    del common
    logic = _logic(GROSS_CAPACITY, max_charging_from_grid_limit=0.8)

    assert logic.calculate(_calc_input(80), TS)

    assert logic.get_inverter_control_settings().charge_from_grid is False


def test_grid_charge_limit_is_bounded_by_max_soc(common):
    """A limit above MAX_SOC cannot be reached: the battery is charged up
    to MAX_SOC at most."""
    del common
    logic = _logic(GROSS_CAPACITY, max_charging_from_grid_limit=0.97)

    assert logic._grid_charge_limit_energy() == pytest.approx(MAX_CAPACITY)
    assert logic._grid_charge_limit_ratio() == pytest.approx(0.95)


def test_grid_charge_limit_without_gross_capacity_uses_max_capacity(common):
    del common
    logic = _logic(None, max_charging_from_grid_limit=0.8)

    assert logic._grid_charge_limit_energy() == pytest.approx(
        MAX_CAPACITY * 0.8)


# ---- min_grid_charge_soc ------------------------------------------------ #

def test_min_grid_charge_soc_is_the_real_soc(common):
    """40 % SoC, min_grid_charge_soc 0.50 (fixed): the target is 50 % SoC =
    5000 Wh, so 1000 Wh are missing (plus min_charge_energy). As a ratio of
    max_capacity the target was 4750 Wh."""
    logic = _logic(GROSS_CAPACITY, max_charging_from_grid_limit=0.8,
                   min_grid_charge_soc=0.5)

    assert logic.calculate(_calc_input(40, consumption=1500.0), TS)

    output = logic.get_calculation_output()
    assert output.effective_min_grid_charge_soc == 0.5
    assert output.required_recharge_energy == pytest.approx(
        5000 - 4000 + common.min_charge_energy)
