from .reads import (
    TELEMETRY_REGISTER_COUNT,
    TELEMETRY_START_REGISTER,
    decode_storage_status,
)
from .types import DeyeModbusTransport


class DeyeStorageReader:
    def __init__(self, transport: DeyeModbusTransport):
        self.transport = transport

    def read_storage_status(self):
        register_read = self.transport.read_registers(
            TELEMETRY_START_REGISTER,
            TELEMETRY_REGISTER_COUNT,
        )
        registers = {
            register_read.start_register + offset: value
            for offset, value in enumerate(register_read.values)
        }
        return decode_storage_status(registers)
