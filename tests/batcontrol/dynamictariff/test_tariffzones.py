import datetime
import pytest
import pytz

from batcontrol.dynamictariff.tariffzones import TariffZones
from batcontrol.dynamictariff.dynamictariff import DynamicTariff


def make_tz():
    return pytz.timezone('Europe/Berlin')


def make_tariff(**kwargs):
    defaults = dict(tariff_zone_1=0.27, tariff_zone_2=0.17)
    defaults.update(kwargs)
    return TariffZones(make_tz(), **defaults)


# ---------------------------------------------------------------------------
# _validate_hour
# ---------------------------------------------------------------------------

def test_validate_hour_accepts_integer():
    assert TariffZones._validate_hour(0, 'zone_1_start') == 0
    assert TariffZones._validate_hour(23, 'zone_1_end') == 23


def test_validate_hour_rejects_out_of_range():
    with pytest.raises(ValueError):
        TariffZones._validate_hour(-1, 'zone_1_start')
    with pytest.raises(ValueError):
        TariffZones._validate_hour(24, 'zone_1_end')


def test_validate_hour_accepts_float_by_int_conversion():
    # int() truncates floats, so 7.9 -> 7
    assert TariffZones._validate_hour(7.9, 'zone_1_start') == 7


def test_validate_hour_rejects_string_decimal():
    with pytest.raises(ValueError):
        TariffZones._validate_hour('7.5', 'zone_1_start')


def test_validate_hour_rejects_none():
    with pytest.raises(ValueError):
        TariffZones._validate_hour(None, 'zone_1_start')


# ---------------------------------------------------------------------------
# _validate_price
# ---------------------------------------------------------------------------

def test_validate_price_accepts_positive():
    assert TariffZones._validate_price(0.27, 'tariff_zone_1') == pytest.approx(0.27)


def test_validate_price_rejects_zero():
    with pytest.raises(ValueError):
        TariffZones._validate_price(0, 'tariff_zone_1')


def test_validate_price_rejects_negative():
    with pytest.raises(ValueError):
        TariffZones._validate_price(-0.1, 'tariff_zone_1')


def test_validate_price_rejects_non_numeric():
    with pytest.raises(ValueError):
        TariffZones._validate_price('abc', 'tariff_zone_1')


# ---------------------------------------------------------------------------
# Constructor and property setters
# ---------------------------------------------------------------------------

def test_constructor_sets_prices_and_boundaries():
    t = make_tariff(zone_1_start=6, zone_1_end=21)
    assert t.tariff_zone_1 == pytest.approx(0.27)
    assert t.tariff_zone_2 == pytest.approx(0.17)
    assert t.zone_1_start == 6
    assert t.zone_1_end == 21


def test_constructor_defaults_boundaries():
    t = make_tariff()
    assert t.zone_1_start == 7
    assert t.zone_1_end == 22


def test_prices_unset_raises_on_generate():
    t = TariffZones(make_tz())
    with pytest.raises(RuntimeError, match='tariff_zone_1 and tariff_zone_2'):
        t._get_prices_native()


def test_property_setters_and_getters():
    t = make_tariff()
    t.zone_1_start = 5
    t.zone_1_end = 23
    assert t.zone_1_start == 5
    assert t.zone_1_end == 23

    with pytest.raises(ValueError):
        t.zone_1_start = -2
    with pytest.raises(ValueError):
        t.zone_1_end = 100


# ---------------------------------------------------------------------------
# _get_prices_native — normal schedule
# ---------------------------------------------------------------------------

def test_get_prices_native_returns_48_hours():
    t = make_tariff()
    prices = t._get_prices_native()
    assert len(prices) == 48


def test_get_prices_native_correct_prices():
    t = make_tariff(zone_1_start=7, zone_1_end=22)
    prices = t._get_prices_native()

    now = datetime.datetime.now().astimezone(t.timezone)
    current_hour_start = now.replace(minute=0, second=0, microsecond=0)

    for rel_hour, price in prices.items():
        h = (current_hour_start + datetime.timedelta(hours=rel_hour)).hour
        is_zone_1 = 7 <= h < 22
        expected = t.tariff_zone_1 if is_zone_1 else t.tariff_zone_2
        assert price == pytest.approx(expected)


# ---------------------------------------------------------------------------
# _get_prices_native — wrap-around schedule
# ---------------------------------------------------------------------------

def test_get_prices_native_wraparound():
    """zone_1_start=22, zone_1_end=6 covers hours 22-23 and 0-5."""
    t = make_tariff(zone_1_start=22, zone_1_end=6)
    prices = t._get_prices_native()
    assert len(prices) == 48

    now = datetime.datetime.now().astimezone(t.timezone)
    current_hour_start = now.replace(minute=0, second=0, microsecond=0)

    for rel_hour, price in prices.items():
        h = (current_hour_start + datetime.timedelta(hours=rel_hour)).hour
        is_zone_1 = h >= 22 or h < 6
        expected = t.tariff_zone_1 if is_zone_1 else t.tariff_zone_2
        assert price == pytest.approx(expected)


def test_get_prices_native_equal_start_end_all_zone_2(caplog):
    """When start == end, all hours should be zone 2 and a warning is logged."""
    import logging
    t = make_tariff(zone_1_start=10, zone_1_end=10)
    with caplog.at_level(logging.WARNING):
        prices = t._get_prices_native()
    assert all(p == pytest.approx(t.tariff_zone_2) for p in prices.values())
    assert any('0 hours' in msg for msg in caplog.messages)


# ---------------------------------------------------------------------------
# Factory integration (DynamicTariff.create_tarif_provider)
# ---------------------------------------------------------------------------

def test_factory_creates_tariff_zones():
    config = {
        'type': 'tariff_zones',
        'tariff_zone_1': 0.2733,
        'tariff_zone_2': 0.1734,
        'zone_1_start': 5,
        'zone_1_end': 0,
    }
    provider = DynamicTariff.create_tarif_provider(config, make_tz(), 0, 0)
    assert isinstance(provider, TariffZones)
    assert provider.tariff_zone_1 == pytest.approx(0.2733)
    assert provider.tariff_zone_2 == pytest.approx(0.1734)
    assert provider.zone_1_start == 5
    assert provider.zone_1_end == 0


def test_factory_missing_required_field_raises():
    config = {'type': 'tariff_zones', 'tariff_zone_1': 0.27}
    with pytest.raises(RuntimeError, match='tariff_zone_2'):
        DynamicTariff.create_tarif_provider(config, make_tz(), 0, 0)
