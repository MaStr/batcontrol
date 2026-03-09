import datetime
import pytest
import pytz

from batcontrol.dynamictariff.tariffzones import TariffZones
from batcontrol.dynamictariff.dynamictariff import DynamicTariff

HOURS_ALL = list(range(24))
HOURS_PEAK = list(range(7, 23))     # 7-22 (16 hours)
HOURS_OFFPEAK = [0, 1, 2, 3, 4, 5, 6, 23]  # 8 hours


def make_tz():
    return pytz.timezone('Europe/Berlin')


def make_tariff(**kwargs):
    """Create a fully configured 2-zone TariffZones instance."""
    defaults = dict(
        tariff_zone_1=0.27,
        zone_1_hours=HOURS_PEAK,
        tariff_zone_2=0.17,
        zone_2_hours=HOURS_OFFPEAK,
    )
    defaults.update(kwargs)
    return TariffZones(make_tz(), **defaults)


# ---------------------------------------------------------------------------
# _parse_hours
# ---------------------------------------------------------------------------

def test_parse_hours_csv_string():
    result = TariffZones._parse_hours('0,1,2,3', 'zone_1_hours')
    assert result == [0, 1, 2, 3]


def test_parse_hours_list_of_ints():
    result = TariffZones._parse_hours([7, 8, 9], 'zone_1_hours')
    assert result == [7, 8, 9]


def test_parse_hours_single_int():
    result = TariffZones._parse_hours(5, 'zone_1_hours')
    assert result == [5]


def test_parse_hours_range_string():
    result = TariffZones._parse_hours('0-5', 'zone_1_hours')
    assert result == [0, 1, 2, 3, 4, 5]


def test_parse_hours_range_full_day():
    result = TariffZones._parse_hours('7-22', 'zone_1_hours')
    assert result == list(range(7, 23))


def test_parse_hours_mixed_range_and_singles():
    result = TariffZones._parse_hours('0-5,6,7', 'zone_1_hours')
    assert result == [0, 1, 2, 3, 4, 5, 6, 7]


def test_parse_hours_range_single_element():
    # "5-5" is a valid range that yields just [5]
    result = TariffZones._parse_hours('5-5', 'zone_1_hours')
    assert result == [5]


def test_parse_hours_list_with_range_strings():
    result = TariffZones._parse_hours(['0-3', '4', '5-6'], 'zone_1_hours')
    assert result == [0, 1, 2, 3, 4, 5, 6]


def test_parse_hours_rejects_inverted_range():
    with pytest.raises(ValueError, match='start must be <= end'):
        TariffZones._parse_hours('5-3', 'zone_1_hours')


def test_parse_hours_rejects_out_of_range():
    with pytest.raises(ValueError, match='out of range'):
        TariffZones._parse_hours('0,24', 'zone_1_hours')
    with pytest.raises(ValueError, match='out of range'):
        TariffZones._parse_hours([-1], 'zone_1_hours')


def test_parse_hours_rejects_range_out_of_bounds():
    with pytest.raises(ValueError, match='out of range'):
        TariffZones._parse_hours('20-25', 'zone_1_hours')


def test_parse_hours_rejects_duplicate_within_zone():
    with pytest.raises(ValueError, match='more than once'):
        TariffZones._parse_hours('7,8,7', 'zone_1_hours')


def test_parse_hours_rejects_non_numeric():
    with pytest.raises(ValueError, match='invalid hour value'):
        TariffZones._parse_hours('7,abc', 'zone_1_hours')


def test_parse_hours_rejects_invalid_type():
    with pytest.raises(ValueError):
        TariffZones._parse_hours({'set'}, 'zone_1_hours')


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


# ---------------------------------------------------------------------------
# Constructor and _validate_configuration
# ---------------------------------------------------------------------------

def test_constructor_sets_all_fields():
    t = make_tariff()
    assert t.tariff_zone_1 == pytest.approx(0.27)
    assert t.tariff_zone_2 == pytest.approx(0.17)
    assert t.zone_1_hours == HOURS_PEAK
    assert t.zone_2_hours == HOURS_OFFPEAK


def test_missing_prices_raises():
    t = TariffZones(make_tz(), zone_1_hours=HOURS_PEAK, zone_2_hours=HOURS_OFFPEAK)
    with pytest.raises(RuntimeError, match='tariff_zone_1'):
        t._get_prices_native()


def test_missing_hours_raises():
    t = TariffZones(make_tz(), tariff_zone_1=0.27, tariff_zone_2=0.17)
    with pytest.raises(RuntimeError, match='zone_1_hours'):
        t._get_prices_native()


def test_cross_zone_duplicate_raises():
    """Hour 7 in both zone_1 and zone_2 must raise ValueError."""
    with pytest.raises(ValueError, match='Hour 7'):
        make_tariff(
            zone_1_hours=list(range(7, 23)),
            zone_2_hours=[0, 1, 2, 3, 4, 5, 6, 7, 23],  # 7 duplicated
        )._get_prices_native()


def test_missing_hour_coverage_raises():
    """If hour 23 is not in any zone, ValueError must be raised."""
    with pytest.raises(ValueError, match='not assigned'):
        make_tariff(
            zone_1_hours=list(range(7, 23)),
            zone_2_hours=list(range(0, 7)),  # missing hour 23
        )._get_prices_native()


def test_zone3_hours_without_price_raises():
    t = TariffZones(
        make_tz(),
        tariff_zone_1=0.27, zone_1_hours=list(range(7, 17)),
        tariff_zone_2=0.17, zone_2_hours=list(range(0, 7)) + [23],
        zone_3_hours=list(range(17, 23)),
        # tariff_zone_3 intentionally omitted
    )
    with pytest.raises(RuntimeError, match='zone_3_hours and tariff_zone_3'):
        t._get_prices_native()


def test_zone3_price_without_hours_raises():
    t = TariffZones(
        make_tz(),
        tariff_zone_1=0.27, zone_1_hours=HOURS_PEAK,
        tariff_zone_2=0.17, zone_2_hours=HOURS_OFFPEAK,
        tariff_zone_3=0.35,
        # zone_3_hours intentionally omitted
    )
    with pytest.raises(RuntimeError, match='zone_3_hours and tariff_zone_3'):
        t._get_prices_native()


# ---------------------------------------------------------------------------
# _get_prices_native — 2-zone
# ---------------------------------------------------------------------------

def test_get_prices_native_returns_48_hours():
    prices = make_tariff()._get_prices_native()
    assert len(prices) == 48


def test_get_prices_native_correct_zone_assignment():
    t = make_tariff(zone_1_hours=HOURS_PEAK, zone_2_hours=HOURS_OFFPEAK)
    prices = t._get_prices_native()

    now = datetime.datetime.now().astimezone(t.timezone)
    base = now.replace(minute=0, second=0, microsecond=0)

    for rel_hour, price in prices.items():
        h = (base + datetime.timedelta(hours=rel_hour)).hour
        expected = t.tariff_zone_1 if h in HOURS_PEAK else t.tariff_zone_2
        assert price == pytest.approx(expected)


# ---------------------------------------------------------------------------
# _get_prices_native — 3-zone
# ---------------------------------------------------------------------------

def test_get_prices_native_three_zones():
    peak = list(range(9, 17))    # 8 hours
    shoulder = list(range(17, 23)) + list(range(7, 9))  # 8 hours
    offpeak = list(range(0, 7)) + [23]  # 8 hours

    t = TariffZones(
        make_tz(),
        tariff_zone_1=0.30, zone_1_hours=peak,
        tariff_zone_2=0.15, zone_2_hours=offpeak,
        tariff_zone_3=0.22, zone_3_hours=shoulder,
    )
    prices = t._get_prices_native()
    assert len(prices) == 48

    now = datetime.datetime.now().astimezone(t.timezone)
    base = now.replace(minute=0, second=0, microsecond=0)

    zone_map = {h: 0.30 for h in peak}
    zone_map.update({h: 0.15 for h in offpeak})
    zone_map.update({h: 0.22 for h in shoulder})

    for rel_hour, price in prices.items():
        h = (base + datetime.timedelta(hours=rel_hour)).hour
        assert price == pytest.approx(zone_map[h])


# ---------------------------------------------------------------------------
# CSV string input (as YAML would provide)
# ---------------------------------------------------------------------------

def test_csv_string_hours_accepted():
    t = TariffZones(
        make_tz(),
        tariff_zone_1=0.27,
        zone_1_hours='7-22',
        tariff_zone_2=0.17,
        zone_2_hours='0-6,23',
    )
    prices = t._get_prices_native()
    assert len(prices) == 48


# ---------------------------------------------------------------------------
# Factory integration (DynamicTariff.create_tarif_provider)
# ---------------------------------------------------------------------------

def test_factory_creates_tariff_zones():
    config = {
        'type': 'tariff_zones',
        'tariff_zone_1': 0.27,
        'zone_1_hours': '7-22',
        'tariff_zone_2': 0.17,
        'zone_2_hours': '0-6,23',
    }
    provider = DynamicTariff.create_tarif_provider(config, make_tz(), 0, 0)
    assert isinstance(provider, TariffZones)
    assert provider.tariff_zone_1 == pytest.approx(0.27)
    assert provider.tariff_zone_2 == pytest.approx(0.17)
    assert provider.zone_1_hours == list(range(7, 23))
    assert provider.zone_2_hours == list(range(0, 7)) + [23]


def test_factory_three_zones():
    config = {
        'type': 'tariff_zones',
        'tariff_zone_1': 0.30,
        'zone_1_hours': '9,10,11,12,13,14,15,16',
        'tariff_zone_2': 0.15,
        'zone_2_hours': '0,1,2,3,4,5,6,23',
        'tariff_zone_3': 0.22,
        'zone_3_hours': '7,8,17,18,19,20,21,22',
    }
    provider = DynamicTariff.create_tarif_provider(config, make_tz(), 0, 0)
    assert isinstance(provider, TariffZones)
    assert provider.tariff_zone_3 == pytest.approx(0.22)
    assert provider.zone_3_hours == [7, 8, 17, 18, 19, 20, 21, 22]


def test_factory_missing_required_field_raises():
    config = {
        'type': 'tariff_zones',
        'tariff_zone_1': 0.27,
        'zone_1_hours': '7,8,9',
        # zone_2_hours missing
        'tariff_zone_2': 0.17,
    }
    with pytest.raises(RuntimeError, match='zone_2_hours'):
        DynamicTariff.create_tarif_provider(config, make_tz(), 0, 0)
