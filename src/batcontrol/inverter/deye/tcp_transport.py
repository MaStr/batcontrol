from ..modbus_tcp_client import ModbusTCPClient
from .types import RegisterRead, RegisterWrite

DEFAULT_PORT = 8899


class DeyeModbusTcpTransport:
    """Modbus TCP transport for a Deye logger stick (default port 8899)."""

    def __init__(self, host: str, port: int = DEFAULT_PORT, unit_id: int = 1):
        self.client = ModbusTCPClient(host, port=port, slave_id=unit_id)
        self.client.connect()

    def _retry_after_reconnect(self, operation):
        try:
            return operation()
        except (ConnectionError, OSError):
            self.client.close()
            self.client.connect()
            return operation()

    def read_registers(self, register: int, count: int) -> RegisterRead:
        values = self._retry_after_reconnect(
            lambda: self.client.read_holding_registers(register, count)
        )
        return RegisterRead(start_register=register, values=values)

    def write_registers(self, writes: list[RegisterWrite]):
        if not writes:
            raise ValueError("writes must not be empty")

        for write in writes:
            self._retry_after_reconnect(
                lambda write=write: self.client.write_register(
                    write.register,
                    write.value,
                )
            )

    def close(self):
        self.client.close()
