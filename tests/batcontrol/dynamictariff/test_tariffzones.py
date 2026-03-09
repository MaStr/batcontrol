import datetime
import pytest
import pytz

from batcontrol.dynamictariff.tariffzones import TariffZones


class DummyTariffZones(TariffZones):
    """Concrete test subclass implementing the abstract provider method."""
    def __init__(self, timezone):
        super().__init__(timezone)
        # provide default zone prices for tests
        self.tariff_zone_1 = 1.0
        self.tariff_zone_2 = 2.0

    def get_raw_data_from_provider(self) -> dict:
        return {}


def make_tz():
    return pytz.timezone('Europe/Berlin')


def test_validate_hour_accepts_integer():
    tz = make_tz()
    t = DummyTariffZones(tz)
    assert t._validate_hour(0, 'zone_1_start') == 0
    assert t._validate_hour(23, 'zone_1_end') == 23


def test_validate_hour_rejects_out_of_range():
    tz = make_tz()
    t = DummyTariffZones(tz)
    with pytest.raises(ValueError):
        t._validate_hour(-1, 'zone_1_start')
    with pytest.raises(ValueError):
        t._validate_hour(24, 'zone_1_end')


def test_validate_hour_accepts_float_by_int_conversion():
    tz = make_tz()
    t = DummyTariffZones(tz)
    # Current implementation converts floats via int(), so 7.9 -> 7
    assert t._validate_hour(7.9, 'zone_1_start') == 7


def test_validate_hour_rejects_string_decimal():
    tz = make_tz()
    t = DummyTariffZones(tz)
    # Strings with decimal point cannot be int()-cast -> ValueError
    with pytest.raises(ValueError):
        t._validate_hour('7.5', 'zone_1_start')


def test_property_setters_and_getters():
    tz = make_tz()
    t = DummyTariffZones(tz)
    t.zone_1_start = 5
    t.zone_1_end = 22
    assert t.zone_1_start == 5
    assert t.zone_1_end == 22

    with pytest.raises(ValueError):
        t.zone_1_start = -2
    with pytest.raises(ValueError):
        t.zone_1_end = 100


def test_get_prices_native_uses_raw_data_boundaries():
    tz = make_tz()
    t = DummyTariffZones(tz)

    # prepare raw data and store it in the provider cache
    raw = {
        'tariff_zone_1': 10.0,
        'tariff_zone_2': 20.0,
        'zone_1_start': 7,
        'zone_1_end': 22,
    }
    t.store_raw_data(raw)

    prices = t._get_prices_native()
    assert len(prices) == 48

    # Compute current hour start same way as provider
    now = datetime.datetime.now().astimezone(t.timezone)
    current_hour_start = now.replace(minute=0, second=0, microsecond=0)

    for rel_hour, price in prices.items():
        ts = current_hour_start + datetime.timedelta(hours=rel_hour)
        h = ts.hour
        if 7 <= 22:
            is_day = (h >= 7 and h < 22)
        else:
            is_day = not (h >= 22 and h < 7)

        expected = raw['tariff_zone_1'] if is_day else raw['tariff_zone_2']
        assert price == expected
