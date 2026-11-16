"""always_allow_discharge_limit is a real state of charge.

0.9 means 90 % SoC, whatever MAX_SOC is set to. The logic used to compare
the stored energy with ``max_capacity * limit``; max_capacity is reduced by
MAX_SOC, so with MAX_SOC 95 % the battery counted as "always allow
discharge" from 85.5 % on, while the external discharge block (evcc), which
checks the SoC, switched at 90 %.
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


def _logic(gross_capacity):
    logic = DefaultLogic(timezone=datetime.timezone.utc, interval_minutes=60)
    logic.set_calculation_parameters(CalculationParameters(
        max_charging_from_grid_limit=0.79,
        min_price_difference=0.05,
        min_price_difference_rel=0.2,
        max_capacity=MAX_CAPACITY,
        gross_capacity=gross_capacity,
    ))
    return logic


def _calc_input(soc):
    """Expensive hours ahead, no PV: the battery is held back unless it is
    in the always-allow-discharge region."""
    stored = _stored_energy(soc)
    return CalculationInput(
        production=np.zeros(4),
        consumption=np.array([3000.0, 3000.0, 3000.0, 3000.0]),
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
