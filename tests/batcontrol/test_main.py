"""Tests for command-line runtime paths."""

from batcontrol.__main__ import get_forecast_cache_directory


def test_forecast_cache_directory_is_beside_config_file(tmp_path):
    """Persistent provider state follows the selected configuration file."""
    config_file = tmp_path / "deployment" / "live.yaml"

    result = get_forecast_cache_directory(config_file)

    assert result == config_file.parent.resolve() / "forecast_cache"
