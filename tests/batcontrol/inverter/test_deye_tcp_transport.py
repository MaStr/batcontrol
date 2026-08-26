from unittest.mock import MagicMock, call, patch

import pytest

from batcontrol.inverter.deye.tcp_transport import DeyeModbusTcpTransport
from batcontrol.inverter.deye.types import RegisterRead, RegisterWrite


def test_transport_reads_holding_registers_via_modbus_client():
    mock_client = MagicMock()
    mock_client.read_holding_registers.return_value = [1000, 5000, 65]

    with patch(
        "batcontrol.inverter.deye.tcp_transport.ModbusTCPClient",
        return_value=mock_client,
    ):
        transport = DeyeModbusTcpTransport("192.168.1.100", unit_id=1)

    result = transport.read_registers(586, 3)

    assert result == RegisterRead(start_register=586, values=[1000, 5000, 65])
    mock_client.read_holding_registers.assert_called_once_with(586, 3)


def test_transport_writes_registers_in_order_via_modbus_client():
    mock_client = MagicMock()

    with patch(
        "batcontrol.inverter.deye.tcp_transport.ModbusTCPClient",
        return_value=mock_client,
    ):
        transport = DeyeModbusTcpTransport("192.168.1.100", unit_id=1)

    transport.write_registers(
        [
            RegisterWrite(130, 1),
            RegisterWrite(108, 40),
        ]
    )

    assert mock_client.write_register.call_args_list == [
        call(130, 1),
        call(108, 40),
    ]


def test_transport_uses_default_port_8899():
    with patch("batcontrol.inverter.deye.tcp_transport.ModbusTCPClient") as mock_cls:
        DeyeModbusTcpTransport("192.168.1.100", unit_id=1)

    mock_cls.assert_called_once_with("192.168.1.100", port=8899, slave_id=1)


def test_transport_connects_client_on_initialization():
    mock_client = MagicMock()

    with patch(
        "batcontrol.inverter.deye.tcp_transport.ModbusTCPClient",
        return_value=mock_client,
    ):
        DeyeModbusTcpTransport("192.168.1.100", port=1502, unit_id=3)

    mock_client.connect.assert_called_once_with()


def test_transport_close_closes_modbus_client():
    mock_client = MagicMock()

    with patch(
        "batcontrol.inverter.deye.tcp_transport.ModbusTCPClient",
        return_value=mock_client,
    ):
        transport = DeyeModbusTcpTransport("192.168.1.100", unit_id=1)

    transport.close()

    mock_client.close.assert_called_once_with()


def test_transport_reconnects_once_and_retries_read_after_connection_loss():
    mock_client = MagicMock()
    mock_client.read_holding_registers.side_effect = [
        ConnectionError("Connection closed by remote"),
        [1000, 5000, 65],
    ]

    with patch(
        "batcontrol.inverter.deye.tcp_transport.ModbusTCPClient",
        return_value=mock_client,
    ):
        transport = DeyeModbusTcpTransport("192.168.1.100", unit_id=1)

    result = transport.read_registers(586, 3)

    assert result == RegisterRead(start_register=586, values=[1000, 5000, 65])
    assert mock_client.read_holding_registers.call_args_list == [
        call(586, 3),
        call(586, 3),
    ]
    assert mock_client.close.call_count == 1
    assert mock_client.connect.call_count == 2


def test_transport_rejects_empty_write_batch():
    mock_client = MagicMock()

    with patch(
        "batcontrol.inverter.deye.tcp_transport.ModbusTCPClient",
        return_value=mock_client,
    ):
        transport = DeyeModbusTcpTransport("192.168.1.100", unit_id=1)

    with pytest.raises(ValueError, match="writes must not be empty"):
        transport.write_registers([])
