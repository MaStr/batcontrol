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
