from __future__ import annotations

from batcontrol.inverter.fronius_modbus.inverter import FroniusModbusInverter
from batcontrol.inverter.fronius_modbus.types import RegisterRead, RegisterWrite


class RecordingModbusTransport:
    def __init__(
        self,
        reads: dict[tuple[int, int], RegisterRead] | None = None,
        write_error: Exception | None = None,
    ):
        self.reads = reads or {}
        self.write_error = write_error
        self.read_requests = []
        self.writes = []
        self.events = []
        self.close_count = 0

    def read_registers(self, register: int, count: int) -> RegisterRead:
        self.read_requests.append((register, count))
        return self.reads[(register, count)]

    def write_registers(self, writes: list[RegisterWrite]):
        self.events.append("write")
        if self.write_error is not None:
            raise self.write_error
        self.writes.append(writes)

    def close(self):
        self.events.append("close")
        self.close_count += 1


def test_inverter_reads_soc_via_storage_reader():
    transport = RecordingModbusTransport(
        reads={
            (40345, 24): RegisterRead(
                start_register=40345,
                values=[
                    10240,
                    0,
                    0,
                    2,
                    0,
                    1000,
                    9700,
                    0,
                    0,
                    3,
                    10000,
                    10000,
                    0,
                    0,
                    0,
                    1,
                    0,
                    0,
                    0,
                    0,
                    65534,
                    0,
                    0,
                    65534,
                ],
            )
        }
    )
    inverter = FroniusModbusInverter(
        transport,
        max_charge_rate=5000,
        min_soc=10,
        max_soc=95,
    )

    assert inverter.get_SOC() == 97.0


def test_inverter_delegates_force_charge_to_control_layer():
    transport = RecordingModbusTransport()
    inverter = FroniusModbusInverter(
        transport,
        max_charge_rate=5000,
        revert_seconds=900,
    )

    inverter.set_mode_force_charge(3000)

    assert transport.writes == [
        [
            RegisterWrite(40360, 1),
            RegisterWrite(40358, 900),
            RegisterWrite(40355, 59536),
            RegisterWrite(40356, 10000),
            RegisterWrite(40348, 2),
        ]
    ]


def test_inverter_exposes_configured_capacity_limits_for_baseclass_math():
    transport = RecordingModbusTransport()
    inverter = FroniusModbusInverter(
        transport,
        max_charge_rate=5000,
        capacity=10000,
        min_soc=10,
        max_soc=95,
    )

    assert inverter.get_capacity() == 10000
    assert inverter.min_soc == 10
    assert inverter.max_soc == 95


def test_inverter_exposes_configured_max_grid_charge_rate():
    transport = RecordingModbusTransport()
    inverter = FroniusModbusInverter(
        transport,
        max_charge_rate=5000,
    )

    assert inverter.max_grid_charge_rate == 5000


def test_inverter_defaults_to_common_soc_limits():
    transport = RecordingModbusTransport()
    inverter = FroniusModbusInverter(
        transport,
        max_charge_rate=5000,
        capacity=10000,
    )

    assert inverter.min_soc == 5
    assert inverter.max_soc == 100


def test_inverter_uses_default_soc_limits_in_baseclass_math():
    transport = RecordingModbusTransport(
        reads={
            (40345, 24): RegisterRead(
                start_register=40345,
                values=[
                    10240,
                    0,
                    0,
                    2,
                    0,
                    1000,
                    6500,
                    0,
                    0,
                    3,
                    10000,
                    10000,
                    0,
                    0,
                    0,
                    1,
                    0,
                    0,
                    0,
                    0,
                    65534,
                    0,
                    0,
                    65534,
                ],
            )
        }
    )
    inverter = FroniusModbusInverter(
        transport,
        max_charge_rate=5000,
        capacity=10000,
    )

    assert inverter.get_stored_energy() == 6500
    assert inverter.get_stored_usable_energy() == 6000
    assert inverter.get_free_capacity() == 3500


def test_inverter_exposes_decoded_storage_status():
    transport = RecordingModbusTransport(
        reads={
            (40345, 24): RegisterRead(
                start_register=40345,
                values=[
                    10240,
                    0,
                    0,
                    2,
                    0,
                    1000,
                    9700,
                    0,
                    0,
                    3,
                    10000,
                    10000,
                    0,
                    0,
                    0,
                    1,
                    0,
                    0,
                    0,
                    0,
                    65534,
                    0,
                    0,
                    65534,
                ],
            )
        }
    )
    inverter = FroniusModbusInverter(transport, max_charge_rate=5000)

    status = inverter.read_storage_status()

    assert status.soc_pct == 97.0
    assert status.max_charge_rate_w == 10240
    assert status.minimum_reserve_pct == 10.0


def test_inverter_reads_max_charge_rate_from_storage_status():
    transport = RecordingModbusTransport(
        reads={
            (40345, 24): RegisterRead(
                start_register=40345,
                values=[
                    10240,
                    0,
                    0,
                    2,
                    0,
                    1000,
                    9700,
                    0,
                    0,
                    3,
                    10000,
                    10000,
                    0,
                    0,
                    0,
                    1,
                    0,
                    0,
                    0,
                    0,
                    65534,
                    0,
                    0,
                    65534,
                ],
            )
        }
    )
    inverter = FroniusModbusInverter(transport, max_charge_rate=5000)

    assert inverter.get_max_charge_rate() == 10240


def test_inverter_reports_grid_charging_enabled_from_storage_status():
    transport = RecordingModbusTransport(
        reads={
            (40345, 24): RegisterRead(
                start_register=40345,
                values=[
                    10240,
                    0,
                    0,
                    2,
                    0,
                    1000,
                    9700,
                    0,
                    0,
                    3,
                    10000,
                    10000,
                    0,
                    0,
                    0,
                    1,
                    0,
                    0,
                    0,
                    0,
                    65534,
                    0,
                    0,
                    65534,
                ],
            )
        }
    )
    inverter = FroniusModbusInverter(transport, max_charge_rate=5000)

    assert inverter.is_grid_charging_enabled() is True


def test_inverter_reads_min_reserve_soc_from_storage_status():
    transport = RecordingModbusTransport(
        reads={
            (40345, 24): RegisterRead(
                start_register=40345,
                values=[
                    10240,
                    0,
                    0,
                    2,
                    0,
                    1000,
                    9700,
                    0,
                    0,
                    3,
                    10000,
                    10000,
                    0,
                    0,
                    0,
                    1,
                    0,
                    0,
                    0,
                    0,
                    65534,
                    0,
                    0,
                    65534,
                ],
            )
        }
    )
    inverter = FroniusModbusInverter(transport, max_charge_rate=5000)

    assert inverter.get_min_reserve_soc() == 10.0


def test_inverter_reads_charge_status_from_storage_status():
    transport = RecordingModbusTransport(
        reads={
            (40345, 24): RegisterRead(
                start_register=40345,
                values=[
                    10240,
                    0,
                    0,
                    2,
                    0,
                    1000,
                    9700,
                    0,
                    0,
                    3,
                    10000,
                    10000,
                    0,
                    0,
                    0,
                    1,
                    0,
                    0,
                    0,
                    0,
                    65534,
                    0,
                    0,
                    65534,
                ],
            )
        }
    )
    inverter = FroniusModbusInverter(transport, max_charge_rate=5000)

    assert inverter.get_charge_status() == 3



def test_shutdown_restores_automatic_mode_before_closing_transport():
    transport = RecordingModbusTransport()
    inverter = FroniusModbusInverter(transport, max_charge_rate=5000)

    inverter.shutdown()

    assert transport.writes == [[
        RegisterWrite(40348, 0),
        RegisterWrite(40355, 10000),
        RegisterWrite(40356, 10000),
        RegisterWrite(40358, 0),
    ]]
    assert transport.events == ["write", "close"]
    assert transport.close_count == 1



def test_shutdown_closes_transport_even_if_reset_to_auto_fails():
    transport = RecordingModbusTransport(write_error=RuntimeError("write failed"))
    inverter = FroniusModbusInverter(transport, max_charge_rate=5000)

    inverter.shutdown()

    assert transport.events == ["write", "close"]
    assert transport.close_count == 1


def test_inverter_passes_grid_status_reader_to_control_layer():
    transport = RecordingModbusTransport()
    grid_status_reader = object()

    inverter = FroniusModbusInverter(
        transport,
        max_charge_rate=5000,
        grid_status_reader=grid_status_reader,
    )

    assert inverter.control.grid_status_reader is grid_status_reader


def test_shutdown_closes_extra_transports():
    transport = RecordingModbusTransport()
    meter_transport = RecordingModbusTransport()
    inverter = FroniusModbusInverter(
        transport,
        max_charge_rate=5000,
        extra_transports=[meter_transport],
    )

    inverter.shutdown()

    assert transport.close_count == 1
    assert meter_transport.close_count == 1
