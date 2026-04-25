from unittest.mock import MagicMock, call, patch

import pytest

import struct

from batcontrol.inverter.fronius_modbus.tcp_transport import (
    FroniusModbusTcpTransport,
    ModbusTCPClient,
)
from batcontrol.inverter.fronius_modbus.types import RegisterRead, RegisterWrite


def test_transport_reads_holding_registers_via_modbus_client():
    mock_client = MagicMock()
    mock_client.read_holding_registers.return_value = [10240, 0, 0]

    with patch(
        "batcontrol.inverter.fronius_modbus.tcp_transport.ModbusTCPClient",
        return_value=mock_client,
    ):
        transport = FroniusModbusTcpTransport("192.168.1.100", port=502, unit_id=1)

    result = transport.read_registers(40345, 3)

    assert result == RegisterRead(start_register=40345, values=[10240, 0, 0])
    mock_client.read_holding_registers.assert_called_once_with(40345, 3)


def test_transport_writes_registers_in_order_via_modbus_client():
    mock_client = MagicMock()

    with patch(
        "batcontrol.inverter.fronius_modbus.tcp_transport.ModbusTCPClient",
        return_value=mock_client,
    ):
        transport = FroniusModbusTcpTransport("192.168.1.100", port=502, unit_id=1)

    transport.write_registers(
        [
            RegisterWrite(40360, 1),
            RegisterWrite(40358, 900),
            RegisterWrite(40355, 59536),
        ]
    )

    assert mock_client.write_register.call_args_list == [
        call(40360, 1),
        call(40358, 900),
        call(40355, 59536),
    ]


def test_transport_connects_client_on_initialization():
    mock_client = MagicMock()

    with patch(
        "batcontrol.inverter.fronius_modbus.tcp_transport.ModbusTCPClient",
        return_value=mock_client,
    ):
        FroniusModbusTcpTransport("192.168.1.100", port=1502, unit_id=3)

    mock_client.connect.assert_called_once_with()


def test_transport_passes_host_port_and_unit_id_to_modbus_client():
    with patch("batcontrol.inverter.fronius_modbus.tcp_transport.ModbusTCPClient") as mock_cls:
        FroniusModbusTcpTransport("192.168.1.100", port=1502, unit_id=3)

    mock_cls.assert_called_once_with("192.168.1.100", port=1502, slave_id=3)


def test_transport_close_closes_modbus_client():
    mock_client = MagicMock()

    with patch(
        "batcontrol.inverter.fronius_modbus.tcp_transport.ModbusTCPClient",
        return_value=mock_client,
    ):
        transport = FroniusModbusTcpTransport("192.168.1.100", port=502, unit_id=1)

    transport.close()

    mock_client.close.assert_called_once_with()


def test_transport_reconnects_once_and_retries_read_after_connection_loss():
    mock_client = MagicMock()
    mock_client.read_holding_registers.side_effect = [
        ConnectionError("Connection closed by remote"),
        [10240, 0, 0],
    ]

    with patch(
        "batcontrol.inverter.fronius_modbus.tcp_transport.ModbusTCPClient",
        return_value=mock_client,
    ):
        transport = FroniusModbusTcpTransport("192.168.1.100", port=502, unit_id=1)

    result = transport.read_registers(40345, 3)

    assert result == RegisterRead(start_register=40345, values=[10240, 0, 0])
    assert mock_client.read_holding_registers.call_args_list == [call(40345, 3), call(40345, 3)]
    assert mock_client.close.call_count == 1
    assert mock_client.connect.call_count == 2


def test_transport_reconnects_once_and_retries_write_after_connection_loss():
    mock_client = MagicMock()
    mock_client.write_register.side_effect = [
        ConnectionError("Connection closed by remote"),
        None,
    ]

    with patch(
        "batcontrol.inverter.fronius_modbus.tcp_transport.ModbusTCPClient",
        return_value=mock_client,
    ):
        transport = FroniusModbusTcpTransport("192.168.1.100", port=502, unit_id=1)

    transport.write_registers([RegisterWrite(40360, 1)])

    assert mock_client.write_register.call_args_list == [call(40360, 1), call(40360, 1)]
    assert mock_client.close.call_count == 1
    assert mock_client.connect.call_count == 2


def test_transport_rejects_empty_write_batch():
    mock_client = MagicMock()

    with patch(
        "batcontrol.inverter.fronius_modbus.tcp_transport.ModbusTCPClient",
        return_value=mock_client,
    ):
        transport = FroniusModbusTcpTransport("192.168.1.100", port=502, unit_id=1)

    with pytest.raises(ValueError, match="writes must not be empty"):
        transport.write_registers([])


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
        client.read_holding_registers(40345, 1)


def test_client_rejects_too_short_write_response_before_unpacking():
    client = ModbusTCPClient("192.168.1.100")
    client._send_and_receive = MagicMock(return_value=b"\x06\x9d")

    with pytest.raises(
        RuntimeError,
        match="Write response too short: expected at least 5 bytes, got 2",
    ):
        client.write_register(40345, 1234)


def test_client_rejects_mismatched_value_in_write_echo():
    client = ModbusTCPClient("192.168.1.100")
    client._send_and_receive = MagicMock(
        return_value=struct.pack(">BHH", 0x06, 40345, 4321)
    )

    with pytest.raises(
        RuntimeError,
        match="Value mismatch in write echo: sent 1234, got 4321",
    ):
        client.write_register(40345, 1234)
