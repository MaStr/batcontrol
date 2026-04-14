import pytest

from batcontrol.inverter.inverter import Inverter
from batcontrol.inverter.mqtt_inverter import MqttInverter


@pytest.fixture(autouse=True)
def reset_inverter_counter():
    original_value = Inverter.num_inverters
    Inverter.num_inverters = 0

    yield

    Inverter.num_inverters = original_value


def test_factory_creates_mqtt_inverter():
    """Factory should create an MQTT inverter for type mqtt."""
    config = {
        "type": "mqtt",
        "capacity": 10000,
        "max_grid_charge_rate": 5000,
    }

    inverter = Inverter.create_inverter(config)

    assert isinstance(inverter, MqttInverter)
    assert inverter.capacity == 10000
    assert inverter.max_grid_charge_rate == 5000


def test_factory_uses_max_charge_rate_alias_for_mqtt():
    """Factory should map max_charge_rate to max_grid_charge_rate."""
    config = {
        "type": "mqtt",
        "capacity": 10000,
        "max_charge_rate": 4200,
    }

    inverter = Inverter.create_inverter(config)

    assert isinstance(inverter, MqttInverter)
    assert inverter.max_grid_charge_rate == 4200


def test_factory_rejects_unknown_type():
    """Factory should reject unknown inverter types."""
    config = {
        "type": "does_not_exist",
        "max_grid_charge_rate": 5000,
    }

    with pytest.raises(RuntimeError, match="inverter type"):
        Inverter.create_inverter(config)


def test_factory_builds_fronius_with_expected_config(mocker):
    """Factory should pass the expected mapped config to FroniusWR."""
    mock_instance = mocker.MagicMock()
    mock_fronius = mocker.patch(
        "batcontrol.inverter.fronius.FroniusWR",
        autospec=True,
        return_value=mock_instance,
    )

    config = {
        "type": "fronius_gen24",
        "address": "192.168.1.100",
        "user": "customer",
        "password": "secret",
        "max_grid_charge_rate": 5000,
        "max_pv_charge_rate": 1700,
        "fronius_inverter_id": 3,
        "fronius_controller_id": 4,
    }

    inverter = Inverter.create_inverter(config)

    mock_fronius.assert_called_once_with(
        {
            "address": "192.168.1.100",
            "user": "customer",
            "password": "secret",
            "max_grid_charge_rate": 5000,
            "max_pv_charge_rate": 1700,
            "fronius_inverter_id": 3,
            "fronius_controller_id": 4,
        }
    )
    assert inverter is mock_instance


def test_factory_defaults_max_pv_charge_rate_for_fronius(mocker):
    """Factory should default max_pv_charge_rate to 0 for Fronius."""
    mock_instance = mocker.MagicMock()
    mock_fronius = mocker.patch(
        "batcontrol.inverter.fronius.FroniusWR",
        autospec=True,
        return_value=mock_instance,
    )

    config = {
        "type": "fronius_gen24",
        "address": "192.168.1.100",
        "user": "customer",
        "password": "secret",
        "max_grid_charge_rate": 5000,
    }

    Inverter.create_inverter(config)

    mock_fronius.assert_called_once_with(
        {
            "address": "192.168.1.100",
            "user": "customer",
            "password": "secret",
            "max_grid_charge_rate": 5000,
            "max_pv_charge_rate": 0,
            "fronius_inverter_id": 1,
            "fronius_controller_id": 0,
        }
    )


def test_factory_applies_fronius_id_defaults(mocker):
    """Factory should apply default Fronius inverter/controller IDs."""
    mock_instance = mocker.MagicMock()
    mock_fronius = mocker.patch(
        "batcontrol.inverter.fronius.FroniusWR",
        autospec=True,
        return_value=mock_instance,
    )

    config = {
        "type": "fronius_gen24",
        "address": "192.168.1.100",
        "user": "customer",
        "password": "secret",
        "max_grid_charge_rate": 5000,
        "max_pv_charge_rate": 1200,
    }

    Inverter.create_inverter(config)

    mock_fronius.assert_called_once_with(
        {
            "address": "192.168.1.100",
            "user": "customer",
            "password": "secret",
            "max_grid_charge_rate": 5000,
            "max_pv_charge_rate": 1200,
            "fronius_inverter_id": 1,
            "fronius_controller_id": 0,
        }
    )
