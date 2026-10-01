"""Tests for reloading the CSV load profile (SIGHUP)."""
import pytest
import pytz

from batcontrol.forecastconsumption.forecast_csv import ForecastConsumptionCsv

TZ = pytz.timezone('Europe/Berlin')


def _write_profile(path, energy, header='month,weekday,hour,energy'):
    lines = [header]
    for month in range(1, 13):
        for weekday in range(7):
            for hour in range(24):
                lines.append(f"{month},{weekday},{hour},{energy}")
    path.write_text("\n".join(lines) + "\n")


def test_reload_picks_up_changed_file(tmp_path):
    csv = tmp_path / "profile.csv"
    _write_profile(csv, 100)
    fc = ForecastConsumptionCsv(str(csv), TZ)
    assert fc.dataframe['energy'].iloc[0] == 100

    _write_profile(csv, 200)
    fc.refresh_data()
    assert fc.dataframe['energy'].iloc[0] == 200


def test_reload_recomputes_scaling_factor(tmp_path):
    csv = tmp_path / "profile.csv"
    _write_profile(csv, 100)
    fc = ForecastConsumptionCsv(str(csv), TZ, annual_consumption=1000)
    old_factor = fc.scaling_factor

    _write_profile(csv, 200)
    fc.refresh_data()
    assert fc.scaling_factor == pytest.approx(old_factor / 2)


def test_reload_keeps_old_profile_on_invalid_file(tmp_path):
    csv = tmp_path / "profile.csv"
    _write_profile(csv, 100)
    fc = ForecastConsumptionCsv(str(csv), TZ)

    _write_profile(csv, 200, header='a,b,c,d')
    fc.refresh_data()
    assert fc.dataframe['energy'].iloc[0] == 100


def test_reload_keeps_old_profile_if_file_missing(tmp_path):
    csv = tmp_path / "profile.csv"
    _write_profile(csv, 100)
    fc = ForecastConsumptionCsv(str(csv), TZ)

    csv.unlink()
    fc.refresh_data()
    assert fc.dataframe['energy'].iloc[0] == 100
