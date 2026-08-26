from batcontrol.inverter.deye.types import RegisterRead, RegisterWrite


def test_register_write_is_frozen_dataclass():
    write = RegisterWrite(108, 42)

    assert write.register == 108
    assert write.value == 42
    assert write == RegisterWrite(108, 42)


def test_register_read_is_frozen_dataclass():
    read = RegisterRead(start_register=586, values=[1, 2, 3])

    assert read.start_register == 586
    assert read.values == [1, 2, 3]
