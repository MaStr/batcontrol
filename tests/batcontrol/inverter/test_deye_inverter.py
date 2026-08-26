from batcontrol.inverter.deye.inverter import DeyeInverter
from batcontrol.inverter.deye.reads import REG_BATTERY_TEMPERATURE
from batcontrol.inverter.deye.types import RegisterRead, RegisterWrite


class RecordingModbusTransport:
    def __init__(self, reads=None):
        self.reads = reads or {}
        self.writes = []
        self.close_count = 0

    def read_registers(self, register, count):
        return self.reads[(register, count)]

    def write_registers(self, writes: list[RegisterWrite]):
        self.writes.append(writes)

    def close(self):
        self.close_count += 1


def _telemetry_read(soc: int) -> RegisterRead:
    return RegisterRead(
        start_register=REG_BATTERY_TEMPERATURE,
        values=[1250, 5320, soc, 0, 0, 0],
    )


def test_inverter_reads_soc_via_storage_reader():
    transport = RecordingModbusTransport(
        reads={(REG_BATTERY_TEMPERATURE, 6): _telemetry_read(62)}
    )
    inverter = DeyeInverter(
        transport,
        capacity=10000,
        nominal_battery_voltage=48,
        min_soc=5,
        max_soc=95,
    )

    assert inverter.get_SOC() == 62


def test_inverter_get_capacity_returns_configured_capacity():
    transport = RecordingModbusTransport()
    inverter = DeyeInverter(
        transport,
        capacity=12000,
        nominal_battery_voltage=48,
    )

    assert inverter.get_capacity() == 12000


def test_inverter_set_mode_force_charge_delegates_to_control():
    transport = RecordingModbusTransport()
    inverter = DeyeInverter(transport, capacity=10000, nominal_battery_voltage=48)

    inverter.set_mode_force_charge(960)

    assert len(transport.writes) == 1


def test_inverter_shutdown_restores_allow_discharge_and_closes_transport():
    transport = RecordingModbusTransport()
    inverter = DeyeInverter(transport, capacity=10000, nominal_battery_voltage=48)

    inverter.shutdown()

    assert len(transport.writes) == 1
    assert transport.close_count == 1


def test_inverter_shutdown_still_closes_transport_when_mode_switch_fails():
    class FailingTransport(RecordingModbusTransport):
        def write_registers(self, writes):
            raise RuntimeError("comms failure")

    transport = FailingTransport()
    inverter = DeyeInverter(transport, capacity=10000, nominal_battery_voltage=48)

    inverter.shutdown()

    assert transport.close_count == 1
