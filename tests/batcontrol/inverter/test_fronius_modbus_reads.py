from batcontrol.inverter.fronius_modbus_reads import (
    FroniusStorageStatus,
    decode_scaled_percent,
    decode_storage_status,
    unsigned_to_signed_16,
)


def test_unsigned_to_signed_16_preserves_positive_values():
    assert unsigned_to_signed_16(12345) == 12345


def test_unsigned_to_signed_16_decodes_twos_complement_negative_values():
    assert unsigned_to_signed_16(65535) == -1


def test_decode_scaled_percent_supports_negative_scale_factors():
    assert decode_scaled_percent(9700, -2) == 97.0


def test_decode_scaled_percent_supports_zero_scale_factor():
    assert decode_scaled_percent(42, 0) == 42.0


def test_decode_storage_status_decodes_known_storage_register_block():
    registers = {
        40345: 10240,
        40348: 2,
        40350: 1000,
        40351: 9700,
        40354: 3,
        40355: 10000,
        40356: 10000,
        40358: 0,
        40360: 1,
        40365: 65534,
        40368: 65534,
    }

    status = decode_storage_status(registers)

    assert status == FroniusStorageStatus(
        max_charge_rate_w=10240,
        storage_control_mode=2,
        minimum_reserve_pct=10.0,
        soc_pct=97.0,
        charge_status=3,
        discharge_rate_pct=100.0,
        charge_rate_pct=100.0,
        revert_seconds=0,
        grid_charging_enabled=True,
        soc_scale_factor=-2,
        rate_scale_factor=-2,
    )


def test_decode_storage_status_decodes_negative_discharge_rate():
    registers = {
        40345: 5000,
        40348: 2,
        40350: 500,
        40351: 5000,
        40354: 4,
        40355: 59536,
        40356: 10000,
        40358: 900,
        40360: 1,
        40365: 65534,
        40368: 65534,
    }

    status = decode_storage_status(registers)

    assert status.discharge_rate_pct == -60.0
    assert status.charge_rate_pct == 100.0
    assert status.revert_seconds == 900
    assert status.grid_charging_enabled is True


def test_decode_storage_status_raises_for_missing_required_register():
    registers = {
        40345: 10240,
        40348: 2,
    }

    try:
        decode_storage_status(registers)
    except KeyError as exc:
        assert str(exc) == "'Missing required register 40350'"
    else:
        raise AssertionError('Expected decode_storage_status to raise for missing register')
