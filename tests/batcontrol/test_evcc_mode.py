"""Tests for evcc mode and connected topic handling for peak shaving.

Tests cover:
- Topic derivation from loadpoint /charging topics
- handle_mode_message parsing
- handle_connected_message parsing
- evcc_ev_expects_pv_surplus property logic
- Multi-loadpoint scenarios
"""
import logging
import unittest
from unittest.mock import MagicMock, patch

logging.basicConfig(level=logging.DEBUG)


class MockMessage:
    """Minimal MQTT message mock."""

    def __init__(self, topic: str, payload: bytes):
        self.topic = topic
        self.payload = payload


class TestEvccModeConnected(unittest.TestCase):
    """Tests for mode/connected evcc topic handling."""

    def _create_evcc_api(self, loadpoint_topics=None):
        """Create an EvccApi instance with mocked MQTT client."""
        if loadpoint_topics is None:
            loadpoint_topics = ['evcc/loadpoints/1/charging']

        config = {
            'broker': 'localhost',
            'port': 1883,
            'status_topic': 'evcc/status',
            'loadpoint_topic': loadpoint_topics,
            'block_battery_while_charging': True,
            'tls': False,
        }

        with patch('batcontrol.evcc_api.mqtt.Client') as mock_mqtt:
            mock_client = MagicMock()
            mock_mqtt.return_value = mock_client
            from batcontrol.evcc_api import EvccApi
            api = EvccApi(config)

        return api

    # ---- Topic derivation ----

    def test_topic_derivation_single(self):
        """charging topic -> mode and connected topics derived."""
        api = self._create_evcc_api(['evcc/loadpoints/1/charging'])
        self.assertIn('evcc/loadpoints/1/mode', api.list_topics_mode)
        self.assertIn('evcc/loadpoints/1/connected', api.list_topics_connected)

    def test_topic_derivation_multiple(self):
        """Multiple loadpoints -> all mode/connected topics derived."""
        api = self._create_evcc_api([
            'evcc/loadpoints/1/charging',
            'evcc/loadpoints/2/charging',
        ])
        self.assertEqual(len(api.list_topics_mode), 2)
        self.assertEqual(len(api.list_topics_connected), 2)
        self.assertIn('evcc/loadpoints/2/mode', api.list_topics_mode)
        self.assertIn('evcc/loadpoints/2/connected',
                      api.list_topics_connected)

    def test_non_standard_topic_warning(self):
        """Topic not ending in /charging -> warning, no mode/connected sub."""
        with self.assertLogs('batcontrol.evcc_api', level='WARNING') as cm:
            api = self._create_evcc_api(['evcc/loadpoints/1/status'])
        self.assertEqual(len(api.list_topics_mode), 0)
        self.assertEqual(len(api.list_topics_connected), 0)
        self.assertTrue(any('does not end in /charging' in msg
                            for msg in cm.output))

    # ---- handle_mode_message ----

    def test_handle_mode_message_pv(self):
        """Parse mode 'pv' correctly."""
        api = self._create_evcc_api()
        msg = MockMessage('evcc/loadpoints/1/mode', b'pv')
        api.handle_mode_message(msg)
        self.assertEqual(
            api.evcc_loadpoint_mode['evcc/loadpoints/1'], 'pv')

    def test_handle_mode_message_now(self):
        """Parse mode 'now' correctly."""
        api = self._create_evcc_api()
        msg = MockMessage('evcc/loadpoints/1/mode', b'now')
        api.handle_mode_message(msg)
        self.assertEqual(
            api.evcc_loadpoint_mode['evcc/loadpoints/1'], 'now')

    def test_handle_mode_message_case_insensitive(self):
        """Mode is converted to lowercase."""
        api = self._create_evcc_api()
        msg = MockMessage('evcc/loadpoints/1/mode', b'PV')
        api.handle_mode_message(msg)
        self.assertEqual(
            api.evcc_loadpoint_mode['evcc/loadpoints/1'], 'pv')

    # ---- handle_connected_message ----

    def test_handle_connected_true(self):
        """Parse connected=true correctly."""
        api = self._create_evcc_api()
        msg = MockMessage('evcc/loadpoints/1/connected', b'true')
        api.handle_connected_message(msg)
        self.assertTrue(
            api.evcc_loadpoint_connected['evcc/loadpoints/1'])

    def test_handle_connected_false(self):
        """Parse connected=false correctly."""
        api = self._create_evcc_api()
        msg = MockMessage('evcc/loadpoints/1/connected', b'false')
        api.handle_connected_message(msg)
        self.assertFalse(
            api.evcc_loadpoint_connected['evcc/loadpoints/1'])

    def test_handle_connected_case_insensitive(self):
        """Connected parsing is case-insensitive."""
        api = self._create_evcc_api()
        msg = MockMessage('evcc/loadpoints/1/connected', b'True')
        api.handle_connected_message(msg)
        self.assertTrue(
            api.evcc_loadpoint_connected['evcc/loadpoints/1'])

    # ---- evcc_ev_expects_pv_surplus ----

    def test_expects_pv_surplus_connected_pv_mode(self):
        """connected=true + mode=pv -> True."""
        api = self._create_evcc_api()
        api.evcc_loadpoint_connected['evcc/loadpoints/1'] = True
        api.evcc_loadpoint_mode['evcc/loadpoints/1'] = 'pv'
        self.assertTrue(api.evcc_ev_expects_pv_surplus)

    def test_expects_pv_surplus_connected_now_mode(self):
        """connected=true + mode=now -> False."""
        api = self._create_evcc_api()
        api.evcc_loadpoint_connected['evcc/loadpoints/1'] = True
        api.evcc_loadpoint_mode['evcc/loadpoints/1'] = 'now'
        self.assertFalse(api.evcc_ev_expects_pv_surplus)

    def test_expects_pv_surplus_disconnected_pv_mode(self):
        """connected=false + mode=pv -> False."""
        api = self._create_evcc_api()
        api.evcc_loadpoint_connected['evcc/loadpoints/1'] = False
        api.evcc_loadpoint_mode['evcc/loadpoints/1'] = 'pv'
        self.assertFalse(api.evcc_ev_expects_pv_surplus)

    def test_expects_pv_surplus_no_data(self):
        """No data received -> False."""
        api = self._create_evcc_api()
        self.assertFalse(api.evcc_ev_expects_pv_surplus)

    def test_multi_loadpoint_one_pv(self):
        """Multi-loadpoint: one connected+pv is enough -> True."""
        api = self._create_evcc_api([
            'evcc/loadpoints/1/charging',
            'evcc/loadpoints/2/charging',
        ])
        api.evcc_loadpoint_connected['evcc/loadpoints/1'] = False
        api.evcc_loadpoint_mode['evcc/loadpoints/1'] = 'off'
        api.evcc_loadpoint_connected['evcc/loadpoints/2'] = True
        api.evcc_loadpoint_mode['evcc/loadpoints/2'] = 'pv'
        self.assertTrue(api.evcc_ev_expects_pv_surplus)

    def test_mode_change_pv_to_now(self):
        """Mode change from pv to now -> evcc_ev_expects_pv_surplus changes to False."""
        api = self._create_evcc_api()
        api.evcc_loadpoint_connected['evcc/loadpoints/1'] = True
        api.evcc_loadpoint_mode['evcc/loadpoints/1'] = 'pv'
        self.assertTrue(api.evcc_ev_expects_pv_surplus)

        # Mode changes
        msg = MockMessage('evcc/loadpoints/1/mode', b'now')
        api.handle_mode_message(msg)
        self.assertFalse(api.evcc_ev_expects_pv_surplus)

    # ---- Message dispatching ----

    def test_dispatch_mode_message(self):
        """_handle_message dispatches mode topic correctly."""
        api = self._create_evcc_api()
        msg = MockMessage('evcc/loadpoints/1/mode', b'pv')
        api._handle_message(None, None, msg)
        self.assertEqual(
            api.evcc_loadpoint_mode['evcc/loadpoints/1'], 'pv')

    def test_dispatch_connected_message(self):
        """_handle_message dispatches connected topic correctly."""
        api = self._create_evcc_api()
        msg = MockMessage('evcc/loadpoints/1/connected', b'true')
        api._handle_message(None, None, msg)
        self.assertTrue(
            api.evcc_loadpoint_connected['evcc/loadpoints/1'])


if __name__ == '__main__':
    unittest.main()
