from batcontrol.inverter.baseclass import InverterBaseclass


class FakeInverter(InverterBaseclass):
    def __init__(self, soc, capacity=10000, min_soc=10, max_soc=95):
        super().__init__({})
        self._soc = soc
        self.capacity = capacity
        self.min_soc = min_soc
        self.max_soc = max_soc

    def set_mode_force_charge(self, chargerate: float):
        pass

    def set_mode_avoid_discharge(self):
        pass

    def set_mode_allow_discharge(self):
        pass

    def set_mode_limit_battery_charge(self, limit_charge_rate: int):
        pass

    def get_capacity(self) -> float:
        return self.capacity

    def get_SOC(self) -> float:
        return self._soc

    def activate_mqtt(self, api_mqtt_api: object):
        pass


def test_get_stored_energy_uses_soc_percentage():
    inverter = FakeInverter(soc=65)

    assert inverter.get_stored_energy() == 6500


def test_get_stored_energy_floors_negative_result_at_zero():
    inverter = FakeInverter(soc=-5)

    assert inverter.get_stored_energy() == 0


def test_get_stored_usable_energy_subtracts_min_soc():
    inverter = FakeInverter(soc=65, min_soc=10)

    assert inverter.get_stored_usable_energy() == 5500


def test_get_stored_usable_energy_floors_negative_result_at_zero():
    inverter = FakeInverter(soc=5, min_soc=10)

    assert inverter.get_stored_usable_energy() == 0


def test_get_max_capacity_uses_max_soc_limit():
    inverter = FakeInverter(soc=65, max_soc=95)

    assert inverter.get_max_capacity() == 9500


def test_get_usable_capacity_uses_min_and_max_soc_limits():
    inverter = FakeInverter(soc=65, min_soc=10, max_soc=95)

    assert inverter.get_usable_capacity() == 8500


def test_get_free_capacity_can_be_negative_above_max_soc():
    inverter = FakeInverter(soc=100, max_soc=95)

    assert inverter.get_free_capacity() == -500


# -- per-inverter values for MQTT and the web API ---------------------------

class _CountingInverter(FakeInverter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.soc_reads = 0

    def get_SOC(self) -> float:
        self.soc_reads += 1
        return super().get_SOC()


def test_refresh_api_values_caches_the_values_without_mqtt():
    inverter = FakeInverter(soc=65)
    inverter.inverter_num = 3
    inverter.inverter_type = 'dummy'
    assert inverter.api_values is None

    inverter.refresh_api_values()

    values = inverter.api_values
    assert values.inverter_id == 3
    assert values.inverter_type == 'dummy'
    assert values.soc == 65
    assert values.stored_energy == inverter.get_stored_energy()
    assert values.stored_usable_energy == inverter.get_stored_usable_energy()
    assert values.free_capacity == inverter.get_free_capacity()
    assert values.max_capacity == inverter.get_max_capacity()
    assert values.usable_capacity == inverter.get_usable_capacity()
    assert values.read_at > 0


def test_refresh_api_values_reads_the_soc_once():
    """A backend without a cache would otherwise see four reads per cycle."""
    inverter = _CountingInverter(soc=65)
    inverter.refresh_api_values()
    assert inverter.soc_reads == 1


def test_refresh_api_values_publishes_the_same_topics(mocker):
    inverter = FakeInverter(soc=65)
    inverter.mqtt_api = mocker.MagicMock()
    inverter.refresh_api_values()
    published = {
        call.args[0]: call.args[1]
        for call in inverter.mqtt_api.generic_publish.call_args_list
    }
    assert published == {
        'inverters/0/SOC': 65,
        'inverters/0/stored_energy': 6500,
        'inverters/0/stored_usable_energy': 5500,
        'inverters/0/free_capacity': 3000,
        'inverters/0/max_capacity': 9500,
    }


def test_fronius_publishes_its_extra_values(mocker):
    from batcontrol.inverter.fronius import FroniusWR  # pylint: disable=import-outside-toplevel

    inverter = FroniusWR.__new__(FroniusWR)
    InverterBaseclass.__init__(inverter, {'type': 'fronius_gen24'})
    inverter.min_soc = 5
    inverter.max_soc = 100
    inverter.capacity = 10000
    inverter.max_grid_charge_rate = 5000
    inverter.em_mode = 1
    inverter.em_power = -200
    mocker.patch.object(FroniusWR, 'get_SOC', return_value=50)
    inverter.mqtt_api = mocker.MagicMock()

    inverter.refresh_api_values()

    assert inverter.api_values.extra == {'em_mode': 1, 'em_power': -200}
    published = {
        call.args[0]: call.args[1]
        for call in inverter.mqtt_api.generic_publish.call_args_list
    }
    for topic, value in (('capacity', 10000), ('min_soc', 5), ('max_soc', 100),
                         ('max_grid_charge_rate', 5000), ('em_mode', 1),
                         ('em_power', -200), ('SOC', 50)):
        assert published[f'inverters/0/{topic}'] == value
