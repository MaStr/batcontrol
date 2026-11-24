"""Tests for EvccApi TLS configuration and broker connection."""
import logging
import warnings
from unittest.mock import Mock, call, patch

import paho.mqtt.client as mqtt
import pytest
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

from batcontrol.evcc_api import EvccApi
from batcontrol.mqtt_api import MqttConnectionError


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

    @pytest.fixture(autouse=True)
    def _cert_files(self, tmp_path, monkeypatch):
        """Configured TLS files must exist; create them in a temp cwd."""
        monkeypatch.chdir(tmp_path)
        for name in ('ca.crt', 'client.crt', 'client.key'):
            (tmp_path / name).write_text('dummy', encoding='ascii')

    def test_tls_disabled_does_not_call_tls_set(self):
        with patch('batcontrol.evcc_api.mqtt.Client') as MockClient:
            client = MockClient.return_value
            cfg = _make_config(tls=False)
            EvccApi(cfg)
            client.tls_set.assert_not_called()

    def test_tls_true_calls_tls_set_with_cafile(self):
        with patch('batcontrol.evcc_api.mqtt.Client') as MockClient:
            client = MockClient.return_value
            cfg = _make_config(tls=True, cafile='ca.crt')
            EvccApi(cfg)
            client.tls_set.assert_called_once_with(
                ca_certs='ca.crt',
                certfile=None,
                keyfile=None,
                ciphers=None,
            )

    def test_tls_true_passes_mutual_tls_params(self):
        with patch('batcontrol.evcc_api.mqtt.Client') as MockClient:
            client = MockClient.return_value
            cfg = _make_config(
                tls=True,
                cafile='ca.crt',
                certfile='client.crt',
                keyfile='client.key',
            )
            EvccApi(cfg)
            client.tls_set.assert_called_once_with(
                ca_certs='ca.crt',
                certfile='client.crt',
                keyfile='client.key',
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
                cafile='ca.crt',
                certfile='client.crt',
            )
            with pytest.raises(ValueError, match='certfile and keyfile'):
                EvccApi(cfg)

    def test_tls_keyfile_without_certfile_raises_valueerror(self):
        with patch('batcontrol.evcc_api.mqtt.Client'):
            cfg = _make_config(
                tls=True,
                cafile='ca.crt',
                keyfile='client.key',
            )
            with pytest.raises(ValueError, match='certfile and keyfile'):
                EvccApi(cfg)

    @pytest.mark.parametrize('key, overrides', [
        ('cafile', {'cafile': 'missing-ca.crt'}),
        ('certfile', {'certfile': 'missing.crt', 'keyfile': 'client.key'}),
        ('keyfile', {'certfile': 'client.crt', 'keyfile': 'missing.key'}),
    ])
    def test_tls_missing_file_raises_with_path(self, key, overrides):
        """R-K02: a wrong path is reported by name before tls_set()."""
        with patch('batcontrol.evcc_api.mqtt.Client') as MockClient:
            cfg = _make_config(tls=True, **{'cafile': 'ca.crt', **overrides})
            with pytest.raises(ValueError, match=f'{key} not found or not a file: missing'):
                EvccApi(cfg)
            MockClient.return_value.tls_set.assert_not_called()

    def test_tls_passes_ciphers(self):
        with patch('batcontrol.evcc_api.mqtt.Client') as MockClient:
            client = MockClient.return_value
            client.is_connected.return_value = True
            cfg = _make_config(tls=True, cafile='ca.crt', ciphers='ECDHE+AESGCM')
            EvccApi(cfg)
            assert client.tls_set.call_args.kwargs['ciphers'] == 'ECDHE+AESGCM'

    def test_tls_disabled_ignores_missing_files(self):
        """Template paths must not matter while tls is false."""
        with patch('batcontrol.evcc_api.mqtt.Client') as MockClient:
            client = MockClient.return_value
            client.is_connected.return_value = True
            cfg = _make_config(
                tls=False, cafile='/nope/ca.crt',
                certfile='/nope/c.crt', keyfile='/nope/c.key')
            EvccApi(cfg)
            client.tls_set.assert_not_called()


class TestBrokerUnreachable:
    """An unreachable evcc broker is retried (5 x 10 s), then start() raises
    MqttConnectionError instead of crashing with a raw OSError."""

    def test_oserror_is_retried_until_success(self):
        with patch('batcontrol.evcc_api.mqtt.Client') as MockClient, \
                patch('batcontrol.mqtt_api.time.sleep') as sleep:
            client = MockClient.return_value
            client.connect.side_effect = [
                ConnectionRefusedError(111, 'Connection refused'), None]
            client.is_connected.return_value = True
            EvccApi(_make_config()).start()

        assert client.connect.call_count == 2
        assert sleep.call_args_list == [call(10)]
        client.loop_start.assert_called_once()

    def test_raises_after_five_attempts(self):
        with patch('batcontrol.evcc_api.mqtt.Client') as MockClient, \
                patch('batcontrol.mqtt_api.time.sleep'):
            client = MockClient.return_value
            client.connect.side_effect = OSError('timed out')
            api = EvccApi(_make_config())
            with pytest.raises(MqttConnectionError,
                               match=r'evcc MQTT broker localhost:8883 '
                                     r'after 5 attempt'):
                api.start()

        assert client.connect.call_count == 5
        client.loop_start.assert_not_called()

    def test_not_ready_raises(self):
        with patch('batcontrol.evcc_api.mqtt.Client') as MockClient, \
                patch('batcontrol.evcc_api.time.sleep'):
            client = MockClient.return_value
            client.is_connected.return_value = False
            api = EvccApi(_make_config())
            with pytest.raises(MqttConnectionError, match='did not accept'):
                api.start()

        # The loop runs, so shutdown() must stop it - core calls it.
        client.loop_start.assert_called_once()

    def test_port_as_numeric_string_is_accepted(self):
        with patch('batcontrol.evcc_api.mqtt.Client') as MockClient:
            client = MockClient.return_value
            client.is_connected.return_value = True
            EvccApi(_make_config(port='1883')).start()

        client.connect.assert_called_once_with('localhost', 1883, 60)

    def test_invalid_port_names_the_key(self):
        with patch('batcontrol.evcc_api.mqtt.Client') as MockClient:
            api = EvccApi(_make_config(port='abc'))
            with pytest.raises(ValueError, match='evcc: port'):
                api.start()
            MockClient.return_value.connect.assert_not_called()


class TestOnConnect:
    """paho callback API v2: on_connect gets a ReasonCode."""

    def test_client_created_with_callback_api_version2(self):
        with patch('batcontrol.evcc_api.mqtt.Client') as MockClient:
            EvccApi(_make_config())

        MockClient.assert_called_once_with(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            clean_session=True)

    def test_real_client_emits_no_deprecation_warning(self):
        with warnings.catch_warnings():
            warnings.simplefilter('error', DeprecationWarning)
            api = EvccApi(_make_config())

        assert api.client.callback_api_version == \
            mqtt.CallbackAPIVersion.VERSION2

    def test_success_subscribes_topics(self):
        with patch('batcontrol.evcc_api.mqtt.Client') as MockClient:
            client = MockClient.return_value
            api = EvccApi(_make_config())
            api.on_connect(client, None, None,
                           ReasonCode(PacketTypes.CONNACK, 'Success'), None)

        subscribed = [c.args[0] for c in client.subscribe.call_args_list]
        assert subscribed == [
            'evcc/status',
            'evcc/loadpoints/1/charging',
            'evcc/loadpoints/1/mode',
            'evcc/loadpoints/1/connected',
        ]

    def test_refused_connection_is_handled_as_failure(self, caplog):
        with patch('batcontrol.evcc_api.mqtt.Client') as MockClient:
            client = MockClient.return_value
            api = EvccApi(_make_config())
            with caplog.at_level('ERROR', logger='batcontrol.evcc_api'):
                api.on_connect(
                    client, None, None,
                    ReasonCode(PacketTypes.CONNACK,
                               'Bad user name or password'),
                    None)

        client.subscribe.assert_not_called()
        assert 'Bad user name or password' in caplog.text


class TestBatteryHaltSoc:
    """evcc bufferSoc must not switch the discharge limit off (R-L22)."""

    @pytest.fixture(name='api')
    def fixture_api(self):
        with patch('batcontrol.evcc_api.mqtt.Client'):
            api = EvccApi(_make_config(battery_halt_topic='evcc/site/bufferSoc'))
        api.set_always_allow_discharge_limit_function = Mock()
        api.get_always_allow_discharge_limit_function = Mock(return_value=0.9)
        api.get_max_charge_limit_function = Mock(return_value=0.8)
        api.set_max_charge_limit_function = Mock()
        api.evcc_is_charging = True
        return api

    @staticmethod
    def _msg(payload):
        message = Mock()
        message.payload = payload
        return message

    def test_regular_value_is_applied(self, api):
        api.handle_battery_halt_soc(self._msg(b'50'))
        api.set_always_allow_discharge_limit_function.assert_called_once_with(0.5)

    def test_zero_becomes_one_percent_with_warning(self, api, caplog):
        with caplog.at_level(logging.WARNING, logger='batcontrol.evcc_api'):
            api.handle_battery_halt_soc(self._msg(b'0'))
        api.set_always_allow_discharge_limit_function.assert_called_once_with(0.01)
        assert api.evcc_battery_halt_soc == 1
        assert 'using 1 % instead' in caplog.text

    @pytest.mark.parametrize('payload', [b'-1', b'101', b'250'])
    def test_out_of_range_is_ignored_with_warning(self, api, caplog, payload):
        with caplog.at_level(logging.WARNING, logger='batcontrol.evcc_api'):
            api.handle_battery_halt_soc(self._msg(payload))
        api.set_always_allow_discharge_limit_function.assert_not_called()
        assert api.evcc_battery_halt_soc is None
        assert 'outside 0..100' in caplog.text

    def test_not_a_number_is_logged(self, api, caplog):
        api.handle_battery_halt_soc(self._msg(b'abc'))
        api.set_always_allow_discharge_limit_function.assert_not_called()
        assert 'Could not convert battery_halt' in caplog.text
