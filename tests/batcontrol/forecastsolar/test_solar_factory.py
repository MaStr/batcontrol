"""Tests for solar-provider factory cache wiring."""

from unittest.mock import patch

import pytz

from batcontrol.forecastsolar.solar import ForecastSolar


@patch("batcontrol.forecastsolar.solar.FCSolar")
def test_fcsolar_receives_persistent_cache_directory(mock_fcsolar, tmp_path):
    """Only forecast.solar receives the restart-cache directory."""
    installations = [{"name": "roof"}]
    timezone = pytz.timezone("Europe/Warsaw")

    ForecastSolar.create_solar_provider(
        installations,
        timezone,
        min_time_between_api_calls=900,
        requested_provider="fcsolarapi",
        persistent_cache_directory=tmp_path,
    )

    mock_fcsolar.assert_called_once_with(
        installations,
        timezone,
        900,
        0,
        60,
        persistent_cache_directory=tmp_path,
    )
