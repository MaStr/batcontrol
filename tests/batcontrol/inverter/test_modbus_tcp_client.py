import struct
from unittest.mock import MagicMock

import pytest

from batcontrol.inverter.modbus_tcp_client import ModbusTCPClient


def test_client_reads_holding_registers():
    client = ModbusTCPClient("192.168.1.100")
    client._send_and_receive = MagicMock(
        return_value=struct.pack(">BBHH", 0x03, 4, 100, 200)
    )

    values = client.read_holding_registers(586, 2)

    assert values == [100, 200]


def test_client_writes_register_and_verifies_echo():
    client = ModbusTCPClient("192.168.1.100")
    client._send_and_receive = MagicMock(
        return_value=struct.pack(">BHH", 0x06, 108, 20)
    )

    client.write_register(108, 20)

    client._send_and_receive.assert_called_once()


def test_client_rejects_malformed_response_length_before_decoding_pdu():
    client = ModbusTCPClient("192.168.1.100")
    client._sock = MagicMock()
    client._build_mbap_header = MagicMock(return_value=(b"header", 1))
    client._recv_exact = MagicMock(
        side_effect=[
            struct.pack(">HHHB", 1, 0, 1, 1),
        ]
    )

    with pytest.raises(
        RuntimeError,
        match="Malformed Modbus response length: expected at least 2, got 1",
    ):
        client._send_and_receive(b"\x03\x00\x00\x00\x01")


def test_client_rejects_unexpected_function_code_in_read_response():
    client = ModbusTCPClient("192.168.1.100")
    client._send_and_receive = MagicMock(return_value=b"\x06\x02\x00\x01")

    with pytest.raises(
        RuntimeError,
        match="Unexpected function code in read response: expected 3, got 6",
    ):
        client.read_holding_registers(108, 1)


def test_client_rejects_too_short_write_response_before_unpacking():
    client = ModbusTCPClient("192.168.1.100")
    client._send_and_receive = MagicMock(return_value=b"\x06\x9d")

    with pytest.raises(
        RuntimeError,
        match="Write response too short: expected at least 5 bytes, got 2",
    ):
        client.write_register(108, 1234)


def test_client_rejects_mismatched_value_in_write_echo():
    client = ModbusTCPClient("192.168.1.100")
    client._send_and_receive = MagicMock(
        return_value=struct.pack(">BHH", 0x06, 108, 4321)
    )

    with pytest.raises(
        RuntimeError,
        match="Value mismatch in write echo: sent 1234, got 4321",
    ):
        client.write_register(108, 1234)


def test_client_rejects_modbus_exception_response():
    client = ModbusTCPClient("192.168.1.100")
    client._send_and_receive = MagicMock(side_effect=RuntimeError("Modbus exception: 2"))

    with pytest.raises(RuntimeError, match="Modbus exception: 2"):
        client.read_holding_registers(108, 1)
