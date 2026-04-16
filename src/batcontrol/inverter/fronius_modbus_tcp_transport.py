import socket
import struct

from .fronius_modbus_types import RegisterRead, RegisterWrite


class ModbusTCPClient:
    def __init__(self, host, port=502, slave_id=1, timeout=5):
        self.host = host
        self.port = port
        self.slave_id = slave_id
        self.timeout = timeout
        self._sock = None
        self._transaction_id = 0

    def connect(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(self.timeout)
        self._sock.connect((self.host, self.port))

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def _next_transaction_id(self):
        self._transaction_id = (self._transaction_id + 1) & 0xFFFF
        return self._transaction_id

    def _build_mbap_header(self, length):
        tid = self._next_transaction_id()
        return struct.pack(">HHHB", tid, 0, length, self.slave_id), tid

    def _recv_exact(self, n):
        data = b""
        while len(data) < n:
            chunk = self._sock.recv(n - len(data))
            if not chunk:
                raise RuntimeError("Connection closed by remote")
            data += chunk
        return data

    def _send_and_receive(self, pdu):
        header, tid = self._build_mbap_header(len(pdu) + 1)
        self._sock.sendall(header + pdu)

        resp_header = self._recv_exact(7)
        resp_tid, _resp_proto, resp_len, _resp_unit = struct.unpack(">HHHB", resp_header)
        if resp_tid != tid:
            raise RuntimeError(
                f"Transaction ID mismatch: sent {tid}, got {resp_tid}"
            )
        if resp_len < 2:
            raise RuntimeError(
                f"Malformed Modbus response length: expected at least 2, got {resp_len}"
            )

        resp_pdu = self._recv_exact(resp_len - 1)
        if not resp_pdu:
            raise RuntimeError("Malformed Modbus response: empty PDU")
        if resp_pdu[0] & 0x80:
            if len(resp_pdu) < 2:
                raise RuntimeError(
                    "Malformed Modbus exception response: missing exception code"
                )
            exception_code = resp_pdu[1]
            raise RuntimeError(f"Modbus exception: {exception_code}")
        return resp_pdu

    def read_holding_registers(self, address, count):
        pdu = struct.pack(">BHH", 0x03, address, count)
        resp = self._send_and_receive(pdu)

        if len(resp) < 2:
            raise RuntimeError(
                f"Read response too short: expected at least 2 bytes, got {len(resp)}"
            )
        if resp[0] != 0x03:
            raise RuntimeError(
                f"Unexpected function code in read response: expected 3, got {resp[0]}"
            )

        byte_count = resp[1]
        if byte_count != count * 2:
            raise RuntimeError(
                f"Expected {count * 2} data bytes, got {byte_count}"
            )
        if len(resp) != byte_count + 2:
            raise RuntimeError(
                f"Read response data length mismatch: expected {byte_count + 2} bytes, got {len(resp)}"
            )

        return [
            struct.unpack(">H", resp[2 + i * 2 : 4 + i * 2])[0]
            for i in range(count)
        ]

    def write_register(self, address, value):
        write_value = value & 0xFFFF
        pdu = struct.pack(">BHH", 0x06, address, write_value)
        resp = self._send_and_receive(pdu)

        if len(resp) < 5:
            raise RuntimeError(
                f"Write response too short: expected at least 5 bytes, got {len(resp)}"
            )
        if resp[0] != 0x06:
            raise RuntimeError(
                f"Unexpected function code in write response: expected 6, got {resp[0]}"
            )

        resp_addr, resp_val = struct.unpack(">HH", resp[1:5])
        if resp_addr != address:
            raise RuntimeError(
                f"Address mismatch in write echo: sent {address}, got {resp_addr}"
            )
        if resp_val != write_value:
            raise RuntimeError(
                f"Value mismatch in write echo: sent {write_value}, got {resp_val}"
            )


class FroniusModbusTcpTransport:
    def __init__(self, host: str, port: int = 502, unit_id: int = 1):
        self.client = ModbusTCPClient(host, port=port, slave_id=unit_id)
        self.client.connect()

    def read_registers(self, register: int, count: int) -> RegisterRead:
        values = self.client.read_holding_registers(register, count)
        return RegisterRead(start_register=register, values=values)

    def write_registers(self, writes: list[RegisterWrite]):
        if not writes:
            raise ValueError("writes must not be empty")

        for write in writes:
            self.client.write_register(write.register, write.value)

    def close(self):
        self.client.close()
