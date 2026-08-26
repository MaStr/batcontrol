from batcontrol.inverter.deye.reads import DeyeStorageStatus
from batcontrol.inverter.deye.storage_reader import DeyeStorageReader
from batcontrol.inverter.deye.types import RegisterRead


class StubTransport:
    def __init__(self, register_read: RegisterRead):
        self.register_read = register_read
        self.read_requests = []

    def read_registers(self, register, count):
        self.read_requests.append((register, count))
        return self.register_read


def test_storage_reader_reads_and_decodes_telemetry_block():
    register_read = RegisterRead(
        start_register=586,
        values=[1250, 5320, 57, 0, 65036, 65486],
    )
    transport = StubTransport(register_read)
    reader = DeyeStorageReader(transport)

    status = reader.read_storage_status()

    assert transport.read_requests == [(586, 6)]
    assert status == DeyeStorageStatus(
        battery_temperature_c=25.0,
        battery_voltage_v=53.2,
        soc_pct=57,
        battery_power_w=-500,
        battery_current_a=-0.5,
    )
