import pytest

from batcontrol.inverter.deye.commands import (
    REG_BATTERY_MAX_CHARGE_CURRENT,
    REG_GRID_CHARGE_CURRENT,
    REG_GRID_CHARGE_ENABLE,
    REG_TOU_CHARGE_SOURCES,
    REG_TOU_SLOT0_TIME,
    REG_TOU_SOC_TARGETS,
    TOU_CHARGE_SOURCE_GRID,
    TOU_CHARGE_SOURCE_OFF,
    build_allow_discharge_register_writes,
    build_avoid_discharge_register_writes,
    build_force_charge_register_writes,
    build_limit_battery_charge_register_writes,
    watts_to_amps_register_value,
)
from batcontrol.inverter.deye.types import RegisterWrite


def test_watts_to_amps_converts_and_rounds_down():
    assert watts_to_amps_register_value(960, 48) == 20


def test_watts_to_amps_clamps_negative_watts_to_zero():
    assert watts_to_amps_register_value(-500, 48) == 0


def test_watts_to_amps_clamps_to_max_current():
    assert watts_to_amps_register_value(100_000, 48, max_current_a=185) == 185


def test_watts_to_amps_rejects_non_positive_voltage():
    with pytest.raises(ValueError, match="nominal_battery_voltage must be greater than 0"):
        watts_to_amps_register_value(1000, 0)


def test_force_charge_writes_current_limits_and_all_tou_slots():
    writes = build_force_charge_register_writes(960, 48, max_soc=95)

    assert writes[:4] == [
        RegisterWrite(REG_BATTERY_MAX_CHARGE_CURRENT, 20),
        RegisterWrite(REG_GRID_CHARGE_CURRENT, 20),
        RegisterWrite(REG_GRID_CHARGE_ENABLE, 1),
        RegisterWrite(REG_TOU_SLOT0_TIME, 0),
    ]
    assert [w for w in writes if w.register in REG_TOU_SOC_TARGETS] == [
        RegisterWrite(reg, 95) for reg in REG_TOU_SOC_TARGETS
    ]
    assert [w for w in writes if w.register in REG_TOU_CHARGE_SOURCES] == [
        RegisterWrite(reg, TOU_CHARGE_SOURCE_GRID) for reg in REG_TOU_CHARGE_SOURCES
    ]


def test_avoid_discharge_holds_current_soc_across_all_tou_slots():
    writes = build_avoid_discharge_register_writes(current_soc=57)

    assert writes[:2] == [
        RegisterWrite(REG_GRID_CHARGE_ENABLE, 1),
        RegisterWrite(REG_TOU_SLOT0_TIME, 0),
    ]
    assert [w for w in writes if w.register in REG_TOU_SOC_TARGETS] == [
        RegisterWrite(reg, 57) for reg in REG_TOU_SOC_TARGETS
    ]
    assert [w for w in writes if w.register in REG_TOU_CHARGE_SOURCES] == [
        RegisterWrite(reg, TOU_CHARGE_SOURCE_GRID) for reg in REG_TOU_CHARGE_SOURCES
    ]


def test_allow_discharge_disables_grid_charging_and_tou_override():
    writes = build_allow_discharge_register_writes(min_soc=5)

    assert [w for w in writes if w.register in REG_TOU_CHARGE_SOURCES] == [
        RegisterWrite(reg, TOU_CHARGE_SOURCE_OFF) for reg in REG_TOU_CHARGE_SOURCES
    ]
    assert [w for w in writes if w.register in REG_TOU_SOC_TARGETS] == [
        RegisterWrite(reg, 5) for reg in REG_TOU_SOC_TARGETS
    ]
    assert writes[-1] == RegisterWrite(REG_GRID_CHARGE_ENABLE, 0)


def test_limit_battery_charge_caps_current_and_disables_grid_charging():
    writes = build_limit_battery_charge_register_writes(480, 48, min_soc=5)

    assert writes[0] == RegisterWrite(REG_BATTERY_MAX_CHARGE_CURRENT, 10)
    assert writes[1] == RegisterWrite(REG_GRID_CHARGE_ENABLE, 0)
    assert [w for w in writes if w.register in REG_TOU_CHARGE_SOURCES] == [
        RegisterWrite(reg, TOU_CHARGE_SOURCE_OFF) for reg in REG_TOU_CHARGE_SOURCES
    ]


def test_limit_battery_charge_zero_blocks_charging_entirely():
    writes = build_limit_battery_charge_register_writes(0, 48, min_soc=5)

    assert writes[0] == RegisterWrite(REG_BATTERY_MAX_CHARGE_CURRENT, 0)
