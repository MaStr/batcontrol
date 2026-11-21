import pytest

from batcontrol.inverter.group import InverterGroup
from batcontrol.inverter.inverter import Inverter
from batcontrol.inverter.mqtt_inverter import MqttInverter
from batcontrol.inverter.resilient_wrapper import ResilientInverterWrapper


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
        "enable_resilient_wrapper": False,
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
        "enable_resilient_wrapper": False,
    }

    inverter = Inverter.create_inverter(config)

    assert isinstance(inverter, MqttInverter)
    assert inverter.max_grid_charge_rate == 4200


def test_factory_prefers_max_grid_charge_rate_over_alias():
    """Factory should prefer max_grid_charge_rate when both keys are present."""
    config = {
        "type": "mqtt",
        "capacity": 10000,
        "max_grid_charge_rate": 5000,
        "max_charge_rate": 4200,
        "enable_resilient_wrapper": False,
    }

    inverter = Inverter.create_inverter(config)

    assert isinstance(inverter, MqttInverter)
    assert inverter.max_grid_charge_rate == 5000


def test_factory_accepts_mqtt_type_case_insensitively():
    """Factory should resolve MQTT type names case-insensitively."""
    config = {
        "type": "MQTT",
        "capacity": 10000,
        "max_grid_charge_rate": 5000,
        "enable_resilient_wrapper": False,
    }

    inverter = Inverter.create_inverter(config)

    assert isinstance(inverter, MqttInverter)


def test_factory_applies_mqtt_defaults():
    """Factory should apply default MQTT configuration values."""
    config = {
        "type": "mqtt",
        "capacity": 10000,
        "max_grid_charge_rate": 5000,
        "enable_resilient_wrapper": False,
    }

    inverter = Inverter.create_inverter(config)

    assert isinstance(inverter, MqttInverter)
    assert inverter.min_soc == 5
    assert inverter.max_soc == 100


def test_factory_forwards_mqtt_cache_ttl():
    """Factory should forward a configured cache_ttl to MqttInverter (regression for #425)."""
    config = {
        "type": "mqtt",
        "capacity": 10000,
        "max_grid_charge_rate": 5000,
        "cache_ttl": 30,
        "enable_resilient_wrapper": False,
    }

    inverter = Inverter.create_inverter(config)

    assert isinstance(inverter, MqttInverter)
    assert inverter.cache_ttl == 30
    assert inverter.soc_value.ttl == 30


def test_factory_defaults_mqtt_cache_ttl():
    """Factory should default cache_ttl to 120 seconds when not configured."""
    config = {
        "type": "mqtt",
        "capacity": 10000,
        "max_grid_charge_rate": 5000,
        "enable_resilient_wrapper": False,
    }

    inverter = Inverter.create_inverter(config)

    assert isinstance(inverter, MqttInverter)
    assert inverter.cache_ttl == 120


def test_factory_falls_back_to_default_when_cache_ttl_is_null():
    """Factory should not forward an explicit `cache_ttl: null` as None to MqttInverter."""
    config = {
        "type": "mqtt",
        "capacity": 10000,
        "max_grid_charge_rate": 5000,
        "cache_ttl": None,
        "enable_resilient_wrapper": False,
    }

    inverter = Inverter.create_inverter(config)

    assert isinstance(inverter, MqttInverter)
    assert inverter.cache_ttl == 120
    assert inverter.soc_value.ttl == 120


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
        "enable_resilient_wrapper": False,
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
            "capacity": -1,
            "backup_directory": None,
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
            "capacity": -1,
            "backup_directory": None,
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
            "capacity": -1,
            "backup_directory": None,
        }
    )


def test_factory_forwards_fronius_capacity_override(mocker):
    """Factory should forward an explicit 'capacity' override to FroniusWR."""
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
        "capacity": 9600,
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
            "capacity": 9600,
            "backup_directory": None,
        }
    )


class TestCreateInverters:
    """create_inverters() builds a single inverter or an InverterGroup."""

    @staticmethod
    def _dummy(max_grid_charge_rate=5000):
        return {
            "type": "dummy",
            "max_grid_charge_rate": max_grid_charge_rate,
            "enable_resilient_wrapper": False,
        }

    def test_mapping_creates_a_single_inverter(self):
        """A mapping keeps the pre-multi-inverter behaviour."""
        inverter = Inverter.create_inverters(self._dummy())

        assert not isinstance(inverter, InverterGroup)
        assert inverter.max_grid_charge_rate == 5000

    def test_single_entry_list_creates_a_single_inverter(self):
        """One list entry needs no group - and keeps inverter_num 0."""
        inverter = Inverter.create_inverters([self._dummy()])

        assert not isinstance(inverter, InverterGroup)
        assert inverter.inverter_num == 0

    def test_two_entries_create_a_group(self):
        inverter = Inverter.create_inverters([
            self._dummy(5000),
            self._dummy(3000),
        ])

        assert isinstance(inverter, InverterGroup)
        assert len(inverter) == 2
        assert inverter.max_grid_charge_rate == 8000

    def test_group_members_are_numbered_by_list_position(self):
        """inverter_num drives the MQTT topic, so it must follow the config."""
        inverter = Inverter.create_inverters([self._dummy(), self._dummy()])

        assert [member.inverter_num for member in inverter.inverters] == [0, 1]

    def test_numbering_is_independent_of_the_process_wide_counter(self):
        """A second create_inverters() call restarts at 0."""
        Inverter.num_inverters = 7

        inverter = Inverter.create_inverters([self._dummy(), self._dummy()])

        assert [member.inverter_num for member in inverter.inverters] == [0, 1]

    def test_group_members_are_wrapped_resiliently_when_enabled(self):
        """The resilient wrapper stays per inverter, not around the group."""
        config = [
            {"type": "dummy", "max_grid_charge_rate": 5000},
            {"type": "dummy", "max_grid_charge_rate": 3000},
        ]

        inverter = Inverter.create_inverters(config)

        assert isinstance(inverter, InverterGroup)
        for member in inverter.inverters:
            assert isinstance(member, ResilientInverterWrapper)

    def test_mqtt_inverters_get_distinct_default_topics(self):
        config = [
            {
                "type": "mqtt",
                "capacity": 10000,
                "max_grid_charge_rate": 5000,
                "enable_resilient_wrapper": False,
            },
            {
                "type": "mqtt",
                "capacity": 5000,
                "max_grid_charge_rate": 3000,
                "enable_resilient_wrapper": False,
            },
        ]

        inverter = Inverter.create_inverters(config)

        topics = [
            member.get_mqtt_inverter_topic() for member in inverter.inverters
        ]
        assert topics == ["inverters/0/", "inverters/1/"]

    def test_duplicate_mqtt_base_topic_is_rejected(self):
        """Sharing a topic would make both inverters read and write the same data."""
        config = [
            {
                "type": "mqtt",
                "capacity": 10000,
                "max_grid_charge_rate": 5000,
                "base_topic": "house/battery",
            },
            {
                "type": "mqtt",
                "capacity": 5000,
                "max_grid_charge_rate": 3000,
                "base_topic": "house/battery/",
            },
        ]

        with pytest.raises(RuntimeError, match="share the base_topic"):
            Inverter.create_inverters(config)

    def test_distinct_mqtt_base_topics_are_accepted(self):
        config = [
            {
                "type": "mqtt",
                "capacity": 10000,
                "max_grid_charge_rate": 5000,
                "base_topic": "house/battery_a",
                "enable_resilient_wrapper": False,
            },
            {
                "type": "mqtt",
                "capacity": 5000,
                "max_grid_charge_rate": 3000,
                "base_topic": "house/battery_b",
                "enable_resilient_wrapper": False,
            },
        ]

        inverter = Inverter.create_inverters(config)

        assert isinstance(inverter, InverterGroup)

    def test_empty_list_is_rejected(self):
        with pytest.raises(RuntimeError, match="list is empty"):
            Inverter.create_inverters([])

    def test_non_mapping_config_is_rejected(self):
        with pytest.raises(RuntimeError, match="mapping or a"):
            Inverter.create_inverters("dummy")

    def test_non_mapping_entry_is_rejected(self):
        with pytest.raises(RuntimeError, match="entry 1 must be a mapping"):
            Inverter.create_inverters([self._dummy(), "dummy"])

    def test_already_created_inverters_are_shut_down_on_failure(self, mocker):
        """A broken second entry must not leak the first inverter."""
        shutdown = mocker.patch(
            "batcontrol.inverter.dummy.Dummy.shutdown", autospec=True)

        with pytest.raises(RuntimeError, match="Unknown inverter type"):
            Inverter.create_inverters([
                self._dummy(),
                {"type": "does_not_exist", "max_grid_charge_rate": 5000},
            ])

        assert shutdown.call_count == 1


class TestMaxGridChargeRateRequired:
    """max_grid_charge_rate has no default and must be a positive number."""

    @staticmethod
    def _dummy(**extra):
        config = {"type": "dummy", "enable_resilient_wrapper": False}
        config.update(extra)
        return config

    def test_missing_key_fails_with_readable_error(self):
        with pytest.raises(
                RuntimeError,
                match=r"Inverter 0 \(type dummy\): max_grid_charge_rate "
                      r"is required"):
            Inverter.create_inverter(self._dummy())

    def test_missing_key_is_no_key_error(self):
        """The old code crashed with KeyError: 'max_charge_rate'."""
        with pytest.raises(RuntimeError):
            Inverter.create_inverter(self._dummy())

    def test_missing_key_in_list_names_the_entry(self):
        with pytest.raises(
                RuntimeError,
                match=r"Inverter 1 \(type dummy\): max_grid_charge_rate "
                      r"is required"):
            Inverter.create_inverters([
                self._dummy(max_grid_charge_rate=5000),
                self._dummy(),
            ])

    def test_missing_key_for_mqtt_names_the_type(self):
        with pytest.raises(RuntimeError, match=r"\(type mqtt\)"):
            Inverter.create_inverter(
                {"type": "mqtt", "capacity": 10000,
                 "enable_resilient_wrapper": False},
                inverter_num=0)

    def test_legacy_alias_is_still_accepted(self):
        inverter = Inverter.create_inverter(self._dummy(max_charge_rate=3000))
        assert inverter.max_grid_charge_rate == 3000

    @pytest.mark.parametrize("value", [0, -1, -5000.5])
    def test_zero_or_negative_is_rejected(self, value):
        with pytest.raises(RuntimeError, match="finite number greater than 0 W"):
            Inverter.create_inverter(self._dummy(max_grid_charge_rate=value))

    @pytest.mark.parametrize(
        "value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_is_rejected(self, value):
        """YAML .nan / .inf parse as float; NaN would pass a '<= 0' check."""
        with pytest.raises(RuntimeError, match="finite number greater than"):
            Inverter.create_inverter(self._dummy(max_grid_charge_rate=value))

    def test_zero_via_legacy_alias_is_rejected(self):
        with pytest.raises(RuntimeError, match="finite number greater than 0 W"):
            Inverter.create_inverter(self._dummy(max_charge_rate=0))

    @pytest.mark.parametrize("value", ["5000", True, None])
    def test_non_number_is_rejected(self, value):
        with pytest.raises(RuntimeError, match="max_grid_charge_rate"):
            Inverter.create_inverter(self._dummy(max_grid_charge_rate=value))

    def test_float_value_is_accepted(self):
        inverter = Inverter.create_inverter(
            self._dummy(max_grid_charge_rate=4600.5))
        assert inverter.max_grid_charge_rate == 4600.5


def test_factory_puts_fronius_backups_below_data_directory(mocker, tmp_path):
    """With a data directory, Fronius backups go to <data_dir>/fronius."""
    mock_fronius = mocker.patch(
        "batcontrol.inverter.fronius.FroniusWR",
        autospec=True,
        return_value=mocker.MagicMock(),
    )
    config = {
        "type": "fronius_gen24",
        "address": "192.168.1.100",
        "user": "customer",
        "password": "secret",
        "max_grid_charge_rate": 5000,
        "enable_resilient_wrapper": False,
    }

    Inverter.create_inverters(config, data_directory=tmp_path)

    iv_config = mock_fronius.call_args.args[0]
    assert iv_config["backup_directory"] == str(tmp_path / "fronius")


def test_factory_rejects_fronius_inverters_with_same_address(mocker):
    """Two entries for one device would overwrite each other's backups."""
    mock_fronius = mocker.patch(
        "batcontrol.inverter.fronius.FroniusWR", autospec=True)
    entry = {
        "type": "fronius_gen24",
        "address": "192.168.1.100",
        "user": "customer",
        "password": "secret",
        "max_grid_charge_rate": 5000,
    }

    with pytest.raises(RuntimeError, match="share the address"):
        Inverter.create_inverters(
            [entry, dict(entry, address=" 192.168.1.100 ")])
    mock_fronius.assert_not_called()
