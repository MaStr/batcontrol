"""Integration tests for load_config with Pydantic validation."""
import os
import pytest
import yaml
from batcontrol.setup import load_config


class TestLoadConfigIntegration:
    """Test load_config() with Pydantic validation integrated."""

    def _write_config(self, config_dict, tmpdir):
        """Write config dict to a YAML file and return the path."""
        path = os.path.join(tmpdir, 'test_config.yaml')
        with open(path, 'w', encoding='UTF-8') as f:
            yaml.safe_dump(config_dict, f)
        return path

    def _minimal_config(self):
        """Return a minimal valid config."""
        return {
            'timezone': 'Europe/Berlin',
            'utility': {'type': 'awattar_de'},
            'pvinstallations': [{'name': 'Test', 'kWp': 10.0}],
        }

    def test_load_config_returns_validated_dict(self, tmp_path):
        """Test that load_config returns a validated dict."""
        path = self._write_config(self._minimal_config(), str(tmp_path))
        result = load_config(path)
        assert isinstance(result, dict)
        assert result['timezone'] == 'Europe/Berlin'
        # Pydantic defaults should be filled in
        assert result['time_resolution_minutes'] == 60

    def test_load_config_coerces_string_port(self, tmp_path):
        """Test that string ports are coerced to int via load_config."""
        config = self._minimal_config()
        config['mqtt'] = {
            'enabled': False,
            'broker': 'localhost',
            'port': '1883',
            'topic': 'test/topic',
            'tls': False,
        }
        path = self._write_config(config, str(tmp_path))
        result = load_config(path)
        assert result['mqtt']['port'] == 1883
        assert isinstance(result['mqtt']['port'], int)

    def test_load_config_missing_file(self):
        """Test that missing file raises RuntimeError."""
        with pytest.raises(RuntimeError, match='not found'):
            load_config('/nonexistent/path/config.yaml')

    def test_load_config_no_pvinstallations(self, tmp_path):
        """Test that empty pvinstallations raises RuntimeError."""
        config = self._minimal_config()
        config['pvinstallations'] = []
        path = self._write_config(config, str(tmp_path))
        with pytest.raises(RuntimeError, match='No PV Installation'):
            load_config(path)

    def test_load_config_missing_pvinstallations_key(self, tmp_path):
        """Test that missing pvinstallations key fails Pydantic validation."""
        config = {
            'timezone': 'Europe/Berlin',
            'utility': {'type': 'awattar_de'},
        }
        path = self._write_config(config, str(tmp_path))
        with pytest.raises(RuntimeError, match='pvinstallations'):
            load_config(path)

    def test_load_config_with_dummy_config_file(self):
        """Test loading the actual dummy config file."""
        dummy_path = os.path.join(
            os.path.dirname(__file__),
            '..', '..', 'config', 'batcontrol_config_dummy.yaml'
        )
        if not os.path.exists(dummy_path):
            pytest.skip('Dummy config file not found')
        result = load_config(dummy_path)
        assert isinstance(result, dict)
        assert result['timezone'] == 'Europe/Berlin'
        assert isinstance(result['time_resolution_minutes'], int)
        assert isinstance(result['mqtt']['port'], int)
        assert isinstance(result['evcc']['port'], int)
