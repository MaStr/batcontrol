import pytest

from batcontrol.inverter.deye.inverter import DeyeInverter
from batcontrol.inverter.inverter import Inverter


@pytest.fixture(autouse=True)
def reset_inverter_counter():
    original_value = Inverter.num_inverters
    Inverter.num_inverters = 0

    yield

    Inverter.num_inverters = original_value


def test_factory_creates_deye_sun_inverter_with_expected_defaults(mocker):
    mock_transport = mocker.MagicMock()
    mock_transport_cls = mocker.patch(
        "batcontrol.inverter.inverter.DeyeModbusTcpTransport",
        autospec=True,
        return_value=mock_transport,
    )

    config = {
        "type": "deye_sun",
        "address": "192.168.1.50",
        "capacity": 10000,
        "max_grid_charge_rate": 5000,
        "enable_resilient_wrapper": False,
    }

    inverter = Inverter.create_inverter(config)

    mock_transport_cls.assert_called_once_with("192.168.1.50", port=8899, unit_id=1)
    assert isinstance(inverter, DeyeInverter)
    assert inverter.transport is mock_transport
    assert inverter.get_capacity() == 10000
    assert inverter.min_soc == 5
    assert inverter.max_soc == 100
    assert inverter.control.nominal_battery_voltage == 48


def test_factory_passes_explicit_deye_sun_config_values(mocker):
    mock_transport = mocker.MagicMock()
    mock_transport_cls = mocker.patch(
        "batcontrol.inverter.inverter.DeyeModbusTcpTransport",
        autospec=True,
        return_value=mock_transport,
    )

    config = {
        "type": "deye_sun",
        "address": "192.168.1.50",
        "port": 1502,
        "unit_id": 3,
        "capacity": 12000,
        "nominal_battery_voltage": 51.2,
        "min_soc": 10,
        "max_soc": 90,
        "max_grid_charge_rate": 6000,
        "enable_resilient_wrapper": False,
    }

    inverter = Inverter.create_inverter(config)

    mock_transport_cls.assert_called_once_with("192.168.1.50", port=1502, unit_id=3)
    assert isinstance(inverter, DeyeInverter)
    assert inverter.get_capacity() == 12000
    assert inverter.min_soc == 10
    assert inverter.max_soc == 90
    assert inverter.control.nominal_battery_voltage == 51.2


def test_factory_accepts_deye_sun_type_case_insensitively(mocker):
    mocker.patch(
        "batcontrol.inverter.inverter.DeyeModbusTcpTransport",
        autospec=True,
        return_value=mocker.MagicMock(),
    )

    config = {
        "type": "DEYE_SUN",
        "address": "192.168.1.50",
        "capacity": 10000,
        "max_grid_charge_rate": 5000,
        "enable_resilient_wrapper": False,
    }

    inverter = Inverter.create_inverter(config)

    assert isinstance(inverter, DeyeInverter)


@pytest.mark.parametrize("missing_key", ["address", "capacity"])
def test_factory_requires_minimal_deye_sun_config(mocker, missing_key):
    mocker.patch(
        "batcontrol.inverter.inverter.DeyeModbusTcpTransport",
        autospec=True,
        return_value=mocker.MagicMock(),
    )

    config = {
        "type": "deye_sun",
        "address": "192.168.1.50",
        "capacity": 10000,
        "max_grid_charge_rate": 5000,
    }
    del config[missing_key]

    with pytest.raises(KeyError, match=missing_key):
        Inverter.create_inverter(config)


def test_factory_closes_transport_when_construction_fails_after_connect(mocker):
    mock_transport = mocker.MagicMock()
    mocker.patch(
        "batcontrol.inverter.inverter.DeyeModbusTcpTransport",
        autospec=True,
        return_value=mock_transport,
    )
    mocker.patch(
        "batcontrol.inverter.inverter.DeyeInverter",
        side_effect=RuntimeError("bad config"),
    )

    config = {
        "type": "deye_sun",
        "address": "192.168.1.50",
        "capacity": 10000,
        "max_grid_charge_rate": 5000,
    }

    with pytest.raises(RuntimeError, match="bad config"):
        Inverter.create_inverter(config)

    mock_transport.close.assert_called_once_with()
