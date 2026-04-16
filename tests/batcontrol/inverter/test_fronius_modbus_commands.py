import pytest

from batcontrol.inverter.fronius_modbus_commands import (
    FULL_RATE_PERCENT,
    REG_CHAGRISET,
    REG_INWRTE,
    REG_OUTWRTE,
    REG_RVRT_TMS,
    REG_STORCTL_MOD,
    STORCTL_CHARGE_LIMIT,
    STORCTL_DISCHARGE_LIMIT,
    build_allow_discharge_register_writes,
    build_avoid_discharge_register_writes,
    build_force_charge_register_writes,
    build_limit_battery_charge_register_writes,
    signed_to_unsigned_16,
    watts_to_pct_register_value,
)


def as_write_map(writes):
    write_map = {write.register: write.value for write in writes}

    assert len(write_map) == len(writes)

    return write_map


def test_watts_to_pct_register_value_scales_partial_rate():
    assert watts_to_pct_register_value(1250, 5000) == 2500


def test_watts_to_pct_register_value_supports_custom_scale_factor():
    assert watts_to_pct_register_value(1250, 5000, scale_factor=-1) == 250


def test_watts_to_pct_register_value_rejects_non_positive_max_charge_rate():
    try:
        watts_to_pct_register_value(1250, 0)
    except ValueError as exc:
        assert str(exc) == "max_charge_rate must be greater than 0, got 0"
    else:
        raise AssertionError("Expected watts_to_pct_register_value to reject max_charge_rate <= 0")


def test_signed_to_unsigned_16_masks_large_negative_values():
    assert signed_to_unsigned_16(-70000) == (-70000 & 0xFFFF)


def test_force_charge_uses_negative_outwrte_value():
    write_map = as_write_map(
        build_force_charge_register_writes(3000, 5000, revert_seconds=900)
    )

    assert list(write_map.keys()) == [
        REG_CHAGRISET,
        REG_RVRT_TMS,
        REG_OUTWRTE,
        REG_INWRTE,
        REG_STORCTL_MOD,
    ]
    assert write_map[REG_CHAGRISET] == 1
    assert write_map[REG_RVRT_TMS] == 900
    assert write_map[REG_OUTWRTE] == signed_to_unsigned_16(-6000)
    assert write_map[REG_INWRTE] == FULL_RATE_PERCENT
    assert write_map[REG_STORCTL_MOD] == STORCTL_DISCHARGE_LIMIT


def test_avoid_discharge_sets_zero_outwrte_and_preserves_revert_timer():
    write_map = as_write_map(build_avoid_discharge_register_writes(revert_seconds=900))

    assert list(write_map.keys()) == [
        REG_RVRT_TMS,
        REG_OUTWRTE,
        REG_INWRTE,
        REG_STORCTL_MOD,
    ]
    assert write_map[REG_RVRT_TMS] == 900
    assert write_map[REG_OUTWRTE] == 0
    assert write_map[REG_INWRTE] == FULL_RATE_PERCENT
    assert write_map[REG_STORCTL_MOD] == STORCTL_DISCHARGE_LIMIT


def test_allow_discharge_restores_auto_defaults_and_clears_revert_timer():
    write_map = as_write_map(build_allow_discharge_register_writes())

    assert list(write_map.keys()) == [
        REG_STORCTL_MOD,
        REG_OUTWRTE,
        REG_INWRTE,
        REG_RVRT_TMS,
    ]
    assert write_map[REG_STORCTL_MOD] == 0
    assert write_map[REG_OUTWRTE] == FULL_RATE_PERCENT
    assert write_map[REG_INWRTE] == FULL_RATE_PERCENT
    assert write_map[REG_RVRT_TMS] == 0


def test_limit_battery_charge_uses_positive_inwrte_and_preserves_discharge():
    write_map = as_write_map(
        build_limit_battery_charge_register_writes(
            2000,
            5000,
            revert_seconds=900,
        )
    )

    assert list(write_map.keys()) == [
        REG_RVRT_TMS,
        REG_OUTWRTE,
        REG_INWRTE,
        REG_STORCTL_MOD,
    ]
    assert write_map[REG_RVRT_TMS] == 900
    assert write_map[REG_OUTWRTE] == FULL_RATE_PERCENT
    assert write_map[REG_INWRTE] == 4000
    assert write_map[REG_STORCTL_MOD] == STORCTL_CHARGE_LIMIT


def test_force_charge_clamps_above_max_charge_rate():
    write_map = as_write_map(build_force_charge_register_writes(6000, 5000))

    assert write_map[REG_OUTWRTE] == signed_to_unsigned_16(-10000)


def test_limit_battery_charge_clamps_above_max_charge_rate():
    write_map = as_write_map(build_limit_battery_charge_register_writes(6000, 5000))

    assert write_map[REG_INWRTE] == FULL_RATE_PERCENT


def test_limit_battery_charge_handles_zero_rate():
    write_map = as_write_map(build_limit_battery_charge_register_writes(0, 5000))

    assert write_map[REG_INWRTE] == 0


def test_force_charge_allows_zero_revert_timer_when_requested():
    write_map = as_write_map(
        build_force_charge_register_writes(3000, 5000, revert_seconds=0)
    )

    assert write_map[REG_RVRT_TMS] == 0


def test_limit_battery_charge_allows_zero_revert_timer_when_requested():
    write_map = as_write_map(
        build_limit_battery_charge_register_writes(2000, 5000, revert_seconds=0)
    )

    assert write_map[REG_RVRT_TMS] == 0


@pytest.mark.parametrize(
    "builder,args",
    [
        (build_force_charge_register_writes, (3000, 5000)),
        (build_avoid_discharge_register_writes, ()),
        (build_limit_battery_charge_register_writes, (2000, 5000)),
    ],
)
def test_builders_reject_negative_revert_seconds(builder, args):
    with pytest.raises(ValueError, match="revert_seconds must be between 0 and 65535"):
        builder(*args, revert_seconds=-1)


@pytest.mark.parametrize(
    "builder,args",
    [
        (build_force_charge_register_writes, (3000, 5000)),
        (build_avoid_discharge_register_writes, ()),
        (build_limit_battery_charge_register_writes, (2000, 5000)),
    ],
)
def test_builders_reject_too_large_revert_seconds(builder, args):
    with pytest.raises(ValueError, match="revert_seconds must be between 0 and 65535"):
        builder(*args, revert_seconds=65536)
