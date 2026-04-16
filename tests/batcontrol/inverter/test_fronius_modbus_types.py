from dataclasses import FrozenInstanceError, fields

import pytest

from batcontrol.inverter.fronius_modbus_types import RegisterRead, RegisterWrite


def test_register_write_is_a_frozen_dataclass_with_expected_fields():
    write = RegisterWrite(40348, 2)

    assert [field.name for field in fields(RegisterWrite)] == [
        "register",
        "value",
    ]

    with pytest.raises(FrozenInstanceError):
        write.register = 40349


def test_register_read_is_a_frozen_dataclass_with_expected_fields():
    read = RegisterRead(start_register=40345, values=[10240])

    assert [field.name for field in fields(RegisterRead)] == [
        "start_register",
        "values",
    ]

    with pytest.raises(FrozenInstanceError):
        read.start_register = 40346
