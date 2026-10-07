"""Tests for the CSV load profile at both target resolutions (issue #446).

The provider is initialized with the target resolution and asked for a
number of slots at that resolution. The CSV profile is hourly, so the
baseclass has to translate the slot request into hours before reading the
dataframe - a 24 h request in 15-minute mode must not read 96 h of profile.
"""
import pytz

from batcontrol.forecastconsumption.forecast_csv import ForecastConsumptionCsv

TZ = pytz.timezone('Europe/Berlin')


def _write_profile(path, energy):
    lines = ['month,weekday,hour,energy']
    for month in range(1, 13):
        for weekday in range(7):
            for hour in range(24):
                lines.append(f"{month},{weekday},{hour},{energy}")
    path.write_text("\n".join(lines) + "\n")


def _profile(tmp_path, energy=1000):
    csv = tmp_path / "profile.csv"
    _write_profile(csv, energy)
    return str(csv)


def test_hourly_resolution_returns_requested_slots(tmp_path):
    """60-minute target: 48 slots are 48 hourly values"""
    fc = ForecastConsumptionCsv(
        _profile(tmp_path), TZ, target_resolution=60)

    forecast = fc.get_forecast(48)

    assert len(forecast) == 48
    assert sorted(forecast.keys()) == list(range(48))
    assert all(value == 1000 for value in forecast.values())


def test_quarterly_resolution_returns_requested_slots(tmp_path):
    """15-minute target: 96 slots are 24 h of profile, split into quarters"""
    fc = ForecastConsumptionCsv(
        _profile(tmp_path), TZ, target_resolution=15)

    forecast = fc.get_forecast(96)

    assert len(forecast) == 96
    assert sorted(forecast.keys()) == list(range(96))
    # Hourly 1000 Wh distributed over four quarters
    assert all(abs(value - 250.0) < 0.1 for value in forecast.values())


def test_resolutions_cover_the_same_energy(tmp_path):
    """Both resolutions describe the same 24 h of consumption"""
    path = _profile(tmp_path)
    hourly = ForecastConsumptionCsv(path, TZ, target_resolution=60)
    quarterly = ForecastConsumptionCsv(path, TZ, target_resolution=15)

    assert sum(quarterly.get_forecast(96).values()) == \
        sum(hourly.get_forecast(24).values())
