"""Tests for EvccApi TLS configuration."""
from unittest.mock import patch

import pytest

from batcontrol.evcc_api import EvccApi


BASE_CONFIG = {
    'broker': 'localhost',
    'port': 8883,
    'status_topic': 'evcc/status',
    'loadpoint_topic': ['evcc/loadpoints/1/charging'],
    'block_battery_while_charging': True,
}


def _make_config(**overrides):
    return {**BASE_CONFIG, **overrides}


class TestTlsSetup:
    """EvccApi.__init__ must configure TLS correctly and reject bad configs."""

    def test_tls_disabled_does_not_call_tls_set(self):
        with patch('batcontrol.evcc_api.mqtt.Client') as MockClient:
            client = MockClient.return_value
            cfg = _make_config(tls=False)
            EvccApi(cfg)
            client.tls_set.assert_not_called()

    def test_tls_true_calls_tls_set_with_cafile(self):
        with patch('batcontrol.evcc_api.mqtt.Client') as MockClient:
            client = MockClient.return_value
            cfg = _make_config(tls=True, cafile='/etc/ssl/ca.crt')
            EvccApi(cfg)
            client.tls_set.assert_called_once_with(
                ca_certs='/etc/ssl/ca.crt',
                certfile=None,
                keyfile=None,
                ciphers=None,
            )

    def test_tls_true_passes_mutual_tls_params(self):
        with patch('batcontrol.evcc_api.mqtt.Client') as MockClient:
            client = MockClient.return_value
            cfg = _make_config(
                tls=True,
                cafile='/etc/ssl/ca.crt',
                certfile='/etc/ssl/client.crt',
                keyfile='/etc/ssl/client.key',
            )
            EvccApi(cfg)
            client.tls_set.assert_called_once_with(
                ca_certs='/etc/ssl/ca.crt',
                certfile='/etc/ssl/client.crt',
                keyfile='/etc/ssl/client.key',
                ciphers=None,
            )

    def test_tls_missing_cafile_raises_valueerror(self):
        with patch('batcontrol.evcc_api.mqtt.Client'):
            cfg = _make_config(tls=True)
            with pytest.raises(ValueError, match='cafile'):
                EvccApi(cfg)

    def test_tls_certfile_without_keyfile_raises_valueerror(self):
        with patch('batcontrol.evcc_api.mqtt.Client'):
            cfg = _make_config(
                tls=True,
                cafile='/etc/ssl/ca.crt',
                certfile='/etc/ssl/client.crt',
            )
            with pytest.raises(ValueError, match='certfile and keyfile'):
                EvccApi(cfg)

    def test_tls_keyfile_without_certfile_raises_valueerror(self):
        with patch('batcontrol.evcc_api.mqtt.Client'):
            cfg = _make_config(
                tls=True,
                cafile='/etc/ssl/ca.crt',
                keyfile='/etc/ssl/client.key',
            )
            with pytest.raises(ValueError, match='certfile and keyfile'):
                EvccApi(cfg)
