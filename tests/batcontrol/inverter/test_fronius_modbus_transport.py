from __future__ import annotations

from batcontrol.inverter.fronius_modbus.types import (
    FroniusModbusTransport,
    RegisterRead,
    RegisterWrite,
)


class RecordingModbusTransport:
    def __init__(self, reads: dict[tuple[int, int], RegisterRead] | None = None):
        self.reads = reads or {}
        self.read_requests = []
        self.writes = []

    def read_registers(self, register: int, count: int) -> RegisterRead:
        self.read_requests.append((register, count))

        key = (register, count)
        if key not in self.reads:
            raise RuntimeError(
                f"No configured read for register {register} count {count}"
            )

        return self.reads[key]

    def write_registers(self, writes: list[RegisterWrite]):
        self.writes.append(writes)


def test_recording_transport_satisfies_modbus_transport_protocol():
    transport: FroniusModbusTransport = RecordingModbusTransport()

    assert isinstance(transport, RecordingModbusTransport)


def test_transport_records_single_register_write_batch():
    transport = RecordingModbusTransport()

    transport.write_registers([RegisterWrite(40348, 2)])

    assert transport.writes == [[RegisterWrite(40348, 2)]]


def test_transport_records_multiple_register_writes_in_order():
    transport = RecordingModbusTransport()

    transport.write_registers(
        [
            RegisterWrite(40358, 900),
            RegisterWrite(40355, 0),
            RegisterWrite(40356, 10000),
            RegisterWrite(40348, 2),
        ]
    )

    assert transport.writes == [
        [
            RegisterWrite(40358, 900),
            RegisterWrite(40355, 0),
            RegisterWrite(40356, 10000),
            RegisterWrite(40348, 2),
        ]
    ]


def test_transport_returns_configured_register_read():
    transport = RecordingModbusTransport(
        reads={
            (40345, 1): RegisterRead(start_register=40345, values=[10240]),
        }
    )

    result = transport.read_registers(40345, 1)

    assert result == RegisterRead(start_register=40345, values=[10240])


def test_transport_records_read_requests():
    transport = RecordingModbusTransport(
        reads={
            (40345, 1): RegisterRead(start_register=40345, values=[10240]),
        }
    )

    transport.read_registers(40345, 1)

    assert transport.read_requests == [(40345, 1)]


def test_transport_raises_for_unconfigured_read():
    transport = RecordingModbusTransport()

    try:
        transport.read_registers(40345, 1)
    except RuntimeError as exc:
        assert str(exc) == "No configured read for register 40345 count 1"
    else:
        raise AssertionError("Expected read_registers to raise for unknown reads")
