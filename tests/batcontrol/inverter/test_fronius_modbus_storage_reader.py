from batcontrol.inverter.fronius_modbus_reads import FroniusStorageStatus
from batcontrol.inverter.fronius_modbus_storage_reader import FroniusModbusStorageReader
from batcontrol.inverter.fronius_modbus_types import RegisterRead


class RecordingModbusTransport:
    def __init__(self, reads: dict[tuple[int, int], RegisterRead]):
        self.reads = reads
        self.read_requests = []

    def read_registers(self, register: int, count: int) -> RegisterRead:
        self.read_requests.append((register, count))
        return self.reads[(register, count)]


def test_reader_reads_storage_status_from_known_block():
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
    reader = FroniusModbusStorageReader(transport)

    status = reader.read_storage_status()

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


def test_reader_requests_expected_storage_register_block():
    transport = RecordingModbusTransport(
        reads={
            (40345, 24): RegisterRead(
                start_register=40345,
                values=[0] * 24,
            )
        }
    )
    reader = FroniusModbusStorageReader(transport)

    reader.read_storage_status()

    assert transport.read_requests == [(40345, 24)]


def test_reader_maps_register_block_by_absolute_register_number():
    transport = RecordingModbusTransport(
        reads={
            (40345, 24): RegisterRead(
                start_register=40345,
                values=[
                    5000,
                    0,
                    0,
                    2,
                    0,
                    500,
                    2500,
                    0,
                    0,
                    4,
                    59536,
                    10000,
                    0,
                    900,
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
    reader = FroniusModbusStorageReader(transport)

    status = reader.read_storage_status()

    assert status.max_charge_rate_w == 5000
    assert status.minimum_reserve_pct == 5.0
    assert status.soc_pct == 25.0
    assert status.charge_status == 4
    assert status.discharge_rate_pct == -60.0
    assert status.charge_rate_pct == 100.0
    assert status.revert_seconds == 900
