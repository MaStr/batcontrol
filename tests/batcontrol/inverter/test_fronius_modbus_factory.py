import pytest

from batcontrol.inverter.fronius_modbus.inverter import FroniusModbusInverter
from batcontrol.inverter.inverter import Inverter


@pytest.fixture(autouse=True)
def reset_inverter_counter():
    original_value = Inverter.num_inverters
    Inverter.num_inverters = 0

    yield

    Inverter.num_inverters = original_value


def test_factory_creates_fronius_modbus_inverter_with_expected_defaults(mocker):
    mock_transport = mocker.MagicMock()
    mock_transport_cls = mocker.patch(
        "batcontrol.inverter.inverter.FroniusModbusTcpTransport",
        autospec=True,
        return_value=mock_transport,
    )

    config = {
        "type": "fronius-modbus",
        "address": "192.168.1.100",
        "capacity": 10000,
        "max_grid_charge_rate": 5000,
    }

    inverter = Inverter.create_inverter(config)

    mock_transport_cls.assert_called_once_with("192.168.1.100", port=502, unit_id=1)
    assert isinstance(inverter, FroniusModbusInverter)
    assert inverter.transport is mock_transport
    assert inverter.get_capacity() == 10000
    assert inverter.min_soc == 5
    assert inverter.max_soc == 100


def test_factory_passes_explicit_fronius_modbus_config_values(mocker):
    mock_transport = mocker.MagicMock()
    mock_transport_cls = mocker.patch(
        "batcontrol.inverter.inverter.FroniusModbusTcpTransport",
        autospec=True,
        return_value=mock_transport,
    )

    config = {
        "type": "fronius-modbus",
        "address": "192.168.1.100",
        "port": 1502,
        "unit_id": 3,
        "capacity": 12000,
        "min_soc": 10,
        "max_soc": 95,
        "max_grid_charge_rate": 6000,
        "revert_seconds": 900,
    }

    inverter = Inverter.create_inverter(config)

    mock_transport_cls.assert_called_once_with("192.168.1.100", port=1502, unit_id=3)
    assert isinstance(inverter, FroniusModbusInverter)
    assert inverter.transport is mock_transport
    assert inverter.get_capacity() == 12000
    assert inverter.min_soc == 10
    assert inverter.max_soc == 95
    assert inverter.control.revert_seconds == 900


def test_factory_accepts_fronius_modbus_type_case_insensitively(mocker):
    mocker.patch(
        "batcontrol.inverter.inverter.FroniusModbusTcpTransport",
        autospec=True,
        return_value=mocker.MagicMock(),
    )

    config = {
        "type": "FRONIUS-MODBUS",
        "address": "192.168.1.100",
        "capacity": 10000,
        "max_grid_charge_rate": 5000,
    }

    inverter = Inverter.create_inverter(config)

    assert isinstance(inverter, FroniusModbusInverter)


@pytest.mark.parametrize(
    "missing_key",
    ["address", "capacity"],
)
def test_factory_requires_minimal_fronius_modbus_config(mocker, missing_key):
    mocker.patch(
        "batcontrol.inverter.inverter.FroniusModbusTcpTransport",
        autospec=True,
        return_value=mocker.MagicMock(),
    )

    config = {
        "type": "fronius-modbus",
        "address": "192.168.1.100",
        "capacity": 10000,
        "max_grid_charge_rate": 5000,
    }
    del config[missing_key]

    with pytest.raises(KeyError, match=missing_key):
        Inverter.create_inverter(config)



def test_factory_accepts_legacy_max_charge_rate_alias_for_fronius_modbus(mocker):
    mock_transport = mocker.MagicMock()
    mock_transport_cls = mocker.patch(
        "batcontrol.inverter.inverter.FroniusModbusTcpTransport",
        autospec=True,
        return_value=mock_transport,
    )

    config = {
        "type": "fronius-modbus",
        "address": "192.168.1.100",
        "capacity": 10000,
        "max_charge_rate": 4200,
    }

    inverter = Inverter.create_inverter(config)

    mock_transport_cls.assert_called_once_with("192.168.1.100", port=502, unit_id=1)
    assert isinstance(inverter, FroniusModbusInverter)
    assert inverter.control.max_charge_rate == 4200


def test_factory_wires_optional_fronius_modbus_backup_mode_safety(mocker):
    inverter_transport = mocker.MagicMock()
    meter_transport = mocker.MagicMock()
    mock_transport_cls = mocker.patch(
        "batcontrol.inverter.inverter.FroniusModbusTcpTransport",
        autospec=True,
        side_effect=[inverter_transport, meter_transport],
    )
    grid_status_reader = mocker.MagicMock()
    mock_grid_status_reader_cls = mocker.patch(
        "batcontrol.inverter.inverter.FroniusModbusGridStatusReader",
        autospec=True,
        return_value=grid_status_reader,
    )

    config = {
        "type": "fronius-modbus",
        "address": "192.168.1.100",
        "port": 1502,
        "unit_id": 3,
        "meter_unit_id": 203,
        "capacity": 10000,
        "max_grid_charge_rate": 5000,
        "backup_mode_safety_enabled": True,
    }

    inverter = Inverter.create_inverter(config)

    assert mock_transport_cls.call_args_list == [
        mocker.call("192.168.1.100", port=1502, unit_id=3),
        mocker.call("192.168.1.100", port=1502, unit_id=203),
    ]
    mock_grid_status_reader_cls.assert_called_once_with(
        inverter_transport,
        meter_transport,
    )
    assert inverter.control.grid_status_reader is grid_status_reader
    assert inverter.extra_transports == [meter_transport]


def test_factory_defaults_fronius_modbus_meter_unit_id_for_backup_mode_safety(mocker):
    mock_transport_cls = mocker.patch(
        "batcontrol.inverter.inverter.FroniusModbusTcpTransport",
        autospec=True,
        side_effect=[mocker.MagicMock(), mocker.MagicMock()],
    )
    mocker.patch(
        "batcontrol.inverter.inverter.FroniusModbusGridStatusReader",
        autospec=True,
        return_value=mocker.MagicMock(),
    )

    config = {
        "type": "fronius-modbus",
        "address": "192.168.1.100",
        "capacity": 10000,
        "max_grid_charge_rate": 5000,
        "backup_mode_safety_enabled": True,
    }

    Inverter.create_inverter(config)

    assert mock_transport_cls.call_args_list == [
        mocker.call("192.168.1.100", port=502, unit_id=1),
        mocker.call("192.168.1.100", port=502, unit_id=200),
    ]


def test_factory_closes_primary_transport_when_backup_meter_transport_fails(mocker):
    inverter_transport = mocker.MagicMock()
    mocker.patch(
        "batcontrol.inverter.inverter.FroniusModbusTcpTransport",
        autospec=True,
        side_effect=[inverter_transport, RuntimeError("meter unavailable")],
    )

    config = {
        "type": "fronius-modbus",
        "address": "192.168.1.100",
        "capacity": 10000,
        "max_grid_charge_rate": 5000,
        "backup_mode_safety_enabled": True,
    }

    with pytest.raises(RuntimeError, match="meter unavailable"):
        Inverter.create_inverter(config)

    inverter_transport.close.assert_called_once_with()
