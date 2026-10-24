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


def test_client_reports_remote_eof_as_connection_error():
    client = ModbusTCPClient("192.0.2.1")
    client._sock = MagicMock()
    client._sock.recv.side_effect = [b"par", b""]

    with pytest.raises(ConnectionError, match="Connection closed by remote"):
        client._recv_exact(7)


def test_disconnected_client_reports_connection_error():
    client = ModbusTCPClient("192.0.2.1")

    with pytest.raises(ConnectionError, match="not connected"):
        client.read_holding_registers(40345, 24)


@pytest.mark.parametrize("error", [TimeoutError(), OSError(113, "No route to host")])
def test_client_closes_socket_when_connect_fails(error):
    sock = MagicMock()
    sock.connect.side_effect = error
    client = ModbusTCPClient("192.0.2.1")

    with patch("batcontrol.inverter.fronius_modbus.tcp_transport.socket.socket",
               return_value=sock):
        with pytest.raises(type(error)):
            client.connect()

    sock.close.assert_called_once_with()
    assert client._sock is None


@pytest.mark.parametrize("operation", ["read", "write"])
def test_transport_reconnect_failure_is_bounded(operation):
    client = MagicMock()
    with patch("batcontrol.inverter.fronius_modbus.tcp_transport.ModbusTCPClient",
               return_value=client):
        transport = FroniusModbusTcpTransport("192.0.2.1")
    client.read_holding_registers.side_effect = TimeoutError()
    client.write_register.side_effect = TimeoutError()
    client.connect.side_effect = OSError(113, "No route to host")

    with pytest.raises(OSError, match="No route to host"):
        if operation == "read":
            transport.read_registers(40345, 24)
        else:
            transport.write_registers([RegisterWrite(40360, 1)])

    assert client.connect.call_count == 2  # startup plus one reconnect
    method = (client.read_holding_registers if operation == "read"
              else client.write_register)
    assert method.call_count == 1


@pytest.mark.parametrize("operation", ["read", "write"])
def test_transport_closes_connection_after_retry_also_fails(operation):
    client = MagicMock()
    with patch("batcontrol.inverter.fronius_modbus.tcp_transport.ModbusTCPClient",
               return_value=client):
        transport = FroniusModbusTcpTransport("192.0.2.1")
    client.read_holding_registers.side_effect = TimeoutError()
    client.write_register.side_effect = TimeoutError()

    with pytest.raises(TimeoutError):
        if operation == "read":
            transport.read_registers(40345, 24)
        else:
            transport.write_registers([RegisterWrite(40360, 1)])

    assert client.connect.call_count == 2
    assert client.close.call_count == 2
    method = (client.read_holding_registers if operation == "read"
              else client.write_register)
    assert method.call_count == 2


def test_real_client_reconnects_on_next_operation_after_two_timeouts():
    failed_sockets = [MagicMock(), MagicMock()]
    for sock in failed_sockets:
        sock.recv.side_effect = TimeoutError()
    recovered_socket = MagicMock()
    # Two failed requests used transaction IDs 1 and 2; the next is 3.
    recovered_socket.recv.side_effect = [
        struct.pack(">HHHB", 3, 0, 5, 1), b"\x03\x02\x00\x2a",
    ]
    with patch("batcontrol.inverter.fronius_modbus.tcp_transport.socket.socket",
               side_effect=[*failed_sockets, recovered_socket]):
        transport = FroniusModbusTcpTransport("192.0.2.1")
        with pytest.raises(TimeoutError):
            transport.read_registers(40345, 1)
        assert transport.client._sock is None

        result = transport.read_registers(40345, 1)

    assert result == RegisterRead(start_register=40345, values=[42])
    for sock in failed_sockets:
        sock.recv.assert_called_once_with(7)
        sock.close.assert_called_once_with()
    recovered_socket.connect.assert_called_once_with(("192.0.2.1", 502))


def test_transport_does_not_reconnect_for_protocol_errors():
    client = MagicMock()
    with patch("batcontrol.inverter.fronius_modbus.tcp_transport.ModbusTCPClient",
               return_value=client):
        transport = FroniusModbusTcpTransport("192.0.2.1")
    client.read_holding_registers.side_effect = RuntimeError(
        "Modbus exception: 11")

    with pytest.raises(RuntimeError, match="Modbus exception: 11"):
        transport.read_registers(40345, 24)

    client.connect.assert_called_once_with()
    client.read_holding_registers.assert_called_once_with(40345, 24)


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
