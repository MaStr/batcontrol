"""Tests for the http -> https redirect handling of the Fronius inverter."""
import unittest
from unittest.mock import Mock, patch

import requests

from batcontrol.inverter.fronius import FroniusWR


def _response(status_code, location=None):
    response = Mock()
    response.status_code = status_code
    response.is_redirect = status_code in (301, 302, 303, 307, 308)
    response.headers = {'Location': location} if location else {}
    return response


def _inverter():
    inverter = object.__new__(FroniusWR)
    inverter.address = '192.168.1.10'
    inverter.scheme = 'http'
    inverter.nonce = 0
    return inverter


class TestFroniusHttpsRedirect(unittest.TestCase):
    """A redirect to https is followed manually, without cert verification."""

    @patch('batcontrol.inverter.fronius.requests.request')
    def test_plain_http_request(self, mock_request):
        mock_request.return_value = _response(200)
        inverter = _inverter()

        response = inverter.send_request('/api/status/version')

        self.assertEqual(response.status_code, 200)
        kwargs = mock_request.call_args.kwargs
        self.assertEqual(kwargs['url'], 'http://192.168.1.10/api/status/version')
        self.assertFalse(kwargs['allow_redirects'])
        self.assertTrue(kwargs['verify'])
        self.assertEqual(inverter.scheme, 'http')

    @patch('batcontrol.inverter.fronius.requests.request')
    def test_redirect_to_https_switches_scheme(self, mock_request):
        mock_request.side_effect = [
            _response(301, 'https://192.168.1.10/api/status/version'),
            _response(200),
        ]
        inverter = _inverter()

        response = inverter.send_request('/api/status/version')

        self.assertEqual(response.status_code, 200)
        second = mock_request.call_args_list[1].kwargs
        self.assertEqual(second['url'], 'https://192.168.1.10/api/status/version')
        self.assertFalse(second['verify'])
        self.assertEqual(inverter.scheme, 'https')

        # Subsequent requests go straight to https
        mock_request.side_effect = None
        mock_request.return_value = _response(200)
        inverter.send_request('/api/status/version')
        self.assertTrue(mock_request.call_args.kwargs['url'].startswith('https://'))

    @patch('batcontrol.inverter.fronius.requests.request')
    def test_redirect_keeps_method_and_payload(self, mock_request):
        mock_request.side_effect = [
            _response(302, 'https://192.168.1.10/config/batteries'),
            _response(200),
        ]
        inverter = _inverter()

        inverter.send_request('/config/batteries', method='POST', payload='{"a":1}')

        second = mock_request.call_args_list[1].kwargs
        self.assertEqual(second['method'], 'POST')
        self.assertEqual(second['data'], '{"a":1}')

    @patch('batcontrol.inverter.fronius.requests.request')
    def test_redirect_to_http_is_not_followed(self, mock_request):
        mock_request.return_value = _response(302, 'http://192.168.1.10/other')
        inverter = _inverter()

        with self.assertRaises(RuntimeError):
            inverter.send_request('/api/status/version')
        self.assertEqual(mock_request.call_count, 1)
        self.assertEqual(inverter.scheme, 'http')

    @patch('batcontrol.inverter.fronius.time.sleep')
    @patch('batcontrol.inverter.fronius.requests.request')
    def test_connection_error_is_retried_three_times(self, mock_request, _sleep):
        mock_request.side_effect = requests.exceptions.ConnectionError('down')
        inverter = _inverter()

        with self.assertRaises(RuntimeError):
            inverter.send_request('/api/status/version')
        self.assertEqual(mock_request.call_count, 3)


if __name__ == '__main__':
    unittest.main()
