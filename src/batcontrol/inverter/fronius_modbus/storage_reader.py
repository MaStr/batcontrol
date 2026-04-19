from .reads import decode_storage_status
from .types import FroniusModbusTransport


REG_STORAGE_START = 40345
REG_STORAGE_COUNT = 24


class FroniusModbusStorageReader:
    def __init__(self, transport: FroniusModbusTransport):
        self.transport = transport

    def read_storage_status(self):
        register_read = self.transport.read_registers(
            REG_STORAGE_START,
            REG_STORAGE_COUNT,
        )
        registers = {
            register_read.start_register + offset: value
            for offset, value in enumerate(register_read.values)
        }
        return decode_storage_status(registers)
