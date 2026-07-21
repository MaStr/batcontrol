import unittest
import unittest.mock
import datetime
import pytz
from unittest.mock import patch
from batcontrol.dynamictariff.energyforecast import Energyforecast, _PROVIDER_MIN_INTERVAL


class TestEnergyforecast(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures"""
        self.timezone = pytz.timezone('Europe/Berlin')
        self.token = 'demo_token'
        self.vat = 0.20
        self.fees = 0.015
        self.markup = 0.03

    def test_basic_price_extraction(self):
        """Test basic price extraction from API v2 response"""
        energyforecast = Energyforecast(self.timezone, self.token)
        energyforecast.set_price_parameters(self.vat, self.fees, self.markup)

        # Mock raw data with v2 format: price_ct_kwh in ct/kWh
        raw_data = {
            'data': [
                {
                    'start': '2024-06-20T10:00:00+02:00',
                    'end': '2024-06-20T10:15:00+02:00',
                    'price_ct_kwh': 20.0,
                    'total_ct_kwh': 25.0,
                    'price_origin': 'market'
                },
                {
                    'start': '2024-06-20T11:00:00+02:00',
                    'end': '2024-06-20T11:15:00+02:00',
                    'price_ct_kwh': 25.0,
                    'total_ct_kwh': 30.0,
                    'price_origin': 'market'
                },
                {
                    'start': '2024-06-20T12:00:00+02:00',
                    'end': '2024-06-20T12:15:00+02:00',
                    'price_ct_kwh': 22.0,
                    'total_ct_kwh': 27.0,
                    'price_origin': 'forecast'
                }
            ]
        }
        energyforecast.store_raw_data(raw_data)

        with patch('batcontrol.dynamictariff.energyforecast.datetime') as mock_datetime:
            mock_now = self.timezone.localize(datetime.datetime(2024, 6, 20, 10, 0, 0))
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.datetime.fromisoformat = datetime.datetime.fromisoformat

            prices = energyforecast._get_prices_native()

        # price_ct_kwh is divided by 100 before applying markup/fees/vat
        expected_price_0 = (0.20 * (1 + 0.03) + 0.015) * (1 + 0.20)
        expected_price_4 = (0.25 * (1 + 0.03) + 0.015) * (1 + 0.20)
        expected_price_8 = (0.22 * (1 + 0.03) + 0.015) * (1 + 0.20)

        self.assertAlmostEqual(prices[0], expected_price_0, places=5)
        self.assertAlmostEqual(prices[4], expected_price_4, places=5)
        self.assertAlmostEqual(prices[8], expected_price_8, places=5)

    def test_quarter_hourly_resolution(self):
        """Test that API v2 quarter-hourly data is parsed correctly"""
        energyforecast = Energyforecast(self.timezone, self.token)
        energyforecast.set_price_parameters(self.vat, self.fees, self.markup)

        # Create 2 hours of quarter-hourly data (8 intervals)
        data_list = []
        base_time = self.timezone.localize(datetime.datetime(2024, 6, 20, 10, 0, 0))
        for i in range(8):
            start_time = base_time + datetime.timedelta(minutes=15 * i)
            data_list.append({
                'start': start_time.isoformat(),
                'end': (start_time + datetime.timedelta(minutes=15)).isoformat(),
                'price_ct_kwh': 20.0 + i * 0.5,
                'total_ct_kwh': 35.0 + i * 0.5,
                'price_origin': 'market'
            })

        energyforecast.store_raw_data({'data': data_list})

        with patch('batcontrol.dynamictariff.energyforecast.datetime') as mock_datetime:
            mock_datetime.datetime.now.return_value = base_time
            mock_datetime.datetime.fromisoformat = datetime.datetime.fromisoformat

            prices = energyforecast._get_prices_native()

        self.assertEqual(len(prices), 8)
        for i in range(8):
            self.assertIn(i, prices)

    def test_timezone_handling_utc(self):
        """Test correct timezone handling with UTC timestamps"""
        energyforecast = Energyforecast(self.timezone, self.token)
        energyforecast.set_price_parameters(self.vat, self.fees, self.markup)

        raw_data = {
            'data': [
                {
                    'start': '2024-06-20T08:00:00Z',  # UTC = 10:00 Europe/Berlin
                    'end': '2024-06-20T08:15:00Z',
                    'price_ct_kwh': 20.0,
                    'total_ct_kwh': 35.0,
                    'price_origin': 'market'
                }
            ]
        }
        energyforecast.store_raw_data(raw_data)

        with patch('batcontrol.dynamictariff.energyforecast.datetime') as mock_datetime:
            mock_now = self.timezone.localize(datetime.datetime(2024, 6, 20, 10, 0, 0))
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.datetime.fromisoformat = datetime.datetime.fromisoformat

            prices = energyforecast._get_prices_native()

        self.assertIn(0, prices)

    def test_filter_past_prices(self):
        """Test that past prices are not included in the result"""
        energyforecast = Energyforecast(self.timezone, self.token)
        energyforecast.set_price_parameters(self.vat, self.fees, self.markup)

        raw_data = {
            'data': [
                {
                    'start': '2024-06-20T08:00:00+02:00',  # Past
                    'end': '2024-06-20T08:15:00+02:00',
                    'price_ct_kwh': 18.0,
                    'total_ct_kwh': 33.0,
                    'price_origin': 'market'
                },
                {
                    'start': '2024-06-20T09:45:00+02:00',  # Past
                    'end': '2024-06-20T10:00:00+02:00',
                    'price_ct_kwh': 19.0,
                    'total_ct_kwh': 34.0,
                    'price_origin': 'market'
                },
                {
                    'start': '2024-06-20T10:00:00+02:00',  # Current
                    'end': '2024-06-20T10:15:00+02:00',
                    'price_ct_kwh': 20.0,
                    'total_ct_kwh': 35.0,
                    'price_origin': 'market'
                },
                {
                    'start': '2024-06-20T10:15:00+02:00',  # Future
                    'end': '2024-06-20T10:30:00+02:00',
                    'price_ct_kwh': 21.0,
                    'total_ct_kwh': 36.0,
                    'price_origin': 'forecast'
                }
            ]
        }
        energyforecast.store_raw_data(raw_data)

        with patch('batcontrol.dynamictariff.energyforecast.datetime') as mock_datetime:
            mock_now = self.timezone.localize(datetime.datetime(2024, 6, 20, 10, 0, 0))
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.datetime.fromisoformat = datetime.datetime.fromisoformat

            prices = energyforecast._get_prices_native()

        # Only current and future intervals (rel_interval >= 0)
        self.assertEqual(len(prices), 2)
        self.assertIn(0, prices)
        self.assertIn(1, prices)

    def test_price_calculation_formula(self):
        """Test that the price calculation formula is correct"""
        energyforecast = Energyforecast(self.timezone, self.token)
        vat = 0.19
        fees = 0.01
        markup = 0.05
        energyforecast.set_price_parameters(vat, fees, markup)

        raw_data = {
            'data': [
                {
                    'start': '2024-06-20T10:00:00+02:00',
                    'end': '2024-06-20T10:15:00+02:00',
                    'price_ct_kwh': 30.0,  # = 0.30 EUR/kWh
                    'total_ct_kwh': 45.0,
                    'price_origin': 'market'
                }
            ]
        }
        energyforecast.store_raw_data(raw_data)

        with patch('batcontrol.dynamictariff.energyforecast.datetime') as mock_datetime:
            mock_now = self.timezone.localize(datetime.datetime(2024, 6, 20, 10, 0, 0))
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.datetime.fromisoformat = datetime.datetime.fromisoformat

            prices = energyforecast._get_prices_native()

        # base_price = price_ct_kwh / 100 = 0.30
        # Formula: (0.30 * 1.05 + 0.01) * 1.19
        expected = (0.30 * 1.05 + 0.01) * 1.19
        self.assertAlmostEqual(prices[0], expected, places=5)

    def test_empty_forecast_data(self):
        """Test handling of empty forecast data"""
        energyforecast = Energyforecast(self.timezone, self.token)
        energyforecast.set_price_parameters(self.vat, self.fees, self.markup)

        energyforecast.store_raw_data({'data': []})

        with patch('batcontrol.dynamictariff.energyforecast.datetime') as mock_datetime:
            mock_now = self.timezone.localize(datetime.datetime(2024, 6, 20, 10, 0, 0))
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.datetime.fromisoformat = datetime.datetime.fromisoformat

            prices = energyforecast._get_prices_native()

        self.assertEqual(len(prices), 0)

    def test_missing_data_key(self):
        """Test handling when 'data' key is missing"""
        energyforecast = Energyforecast(self.timezone, self.token)
        energyforecast.set_price_parameters(self.vat, self.fees, self.markup)

        energyforecast.store_raw_data({})

        with patch('batcontrol.dynamictariff.energyforecast.datetime') as mock_datetime:
            mock_now = self.timezone.localize(datetime.datetime(2024, 6, 20, 10, 0, 0))
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.datetime.fromisoformat = datetime.datetime.fromisoformat

            prices = energyforecast._get_prices_native()

        self.assertEqual(len(prices), 0)

    def test_token_required(self):
        """Test that API token is required"""
        energyforecast = Energyforecast(self.timezone, None)
        energyforecast.set_price_parameters(self.vat, self.fees, self.markup)

        with self.assertRaises(RuntimeError) as context:
            energyforecast.get_raw_data_from_provider()

        self.assertIn('token is required', str(context.exception).lower())

    def test_market_zone_default(self):
        """Test that the config default 'DE' is normalized to 'DE-LU' for the API"""
        energyforecast = Energyforecast(self.timezone, self.token)
        self.assertEqual(energyforecast.market_zone, 'DE-LU')

    def test_market_zone_aliases(self):
        """Test that DE and LU (and lowercase variants) are normalized to DE-LU"""
        ef_de = Energyforecast(self.timezone, self.token, market_zone='DE')
        self.assertEqual(ef_de.market_zone, 'DE-LU')

        ef_lu = Energyforecast(self.timezone, self.token, market_zone='LU')
        self.assertEqual(ef_lu.market_zone, 'DE-LU')

        ef_lower = Energyforecast(self.timezone, self.token, market_zone='de')
        self.assertEqual(ef_lower.market_zone, 'DE-LU')

        ef_delu = Energyforecast(self.timezone, self.token, market_zone='DE-LU')
        self.assertEqual(ef_delu.market_zone, 'DE-LU')

    def test_market_zone_custom(self):
        """Test that custom market_zone is passed to the API request"""
        energyforecast = Energyforecast(self.timezone, self.token, market_zone='AT')
        self.assertEqual(energyforecast.market_zone, 'AT')

        with patch('batcontrol.dynamictariff.energyforecast.requests.get') as mock_get:
            mock_response = unittest.mock.Mock()
            mock_response.json.return_value = {'data': []}
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response

            energyforecast.get_raw_data_from_provider()

            self.assertEqual(mock_get.call_args.kwargs['params']['market_zone'], 'AT')

    def test_refresh_interval_floor(self):
        """Test that refresh interval is at least 4 hours"""
        ef_default = Energyforecast(self.timezone, self.token, min_time_between_API_calls=0)
        self.assertEqual(ef_default.min_time_between_updates, _PROVIDER_MIN_INTERVAL)

        ef_short = Energyforecast(self.timezone, self.token, min_time_between_API_calls=60)
        self.assertEqual(ef_short.min_time_between_updates, _PROVIDER_MIN_INTERVAL)

        longer = _PROVIDER_MIN_INTERVAL + 3600
        ef_long = Energyforecast(self.timezone, self.token, min_time_between_API_calls=longer)
        self.assertEqual(ef_long.min_time_between_updates, longer)

    def test_api_v2_url(self):
        """Test that the v2 API endpoint is used"""
        energyforecast = Energyforecast(self.timezone, self.token)
        self.assertIn('/api/v2/forecast', energyforecast.url)

    def test_total_price_uses_total_ct_kwh(self):
        """Test that use_total_price=True reads total_ct_kwh instead of price_ct_kwh"""
        ef = Energyforecast(self.timezone, self.token, use_total_price=True)

        raw_data = {
            'data': [
                {
                    'start': '2024-06-20T10:00:00+02:00',
                    'end': '2024-06-20T10:15:00+02:00',
                    'price_ct_kwh': 20.0,
                    'total_ct_kwh': 35.0,
                    'price_origin': 'market'
                }
            ]
        }
        ef.store_raw_data(raw_data)

        with patch('batcontrol.dynamictariff.energyforecast.datetime') as mock_datetime:
            mock_now = self.timezone.localize(datetime.datetime(2024, 6, 20, 10, 0, 0))
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.datetime.fromisoformat = datetime.datetime.fromisoformat

            prices = ef._get_prices_native()

        # Must use total_ct_kwh (35.0 ct = 0.35 EUR), not price_ct_kwh (20.0 ct)
        self.assertAlmostEqual(prices[0], 0.35, places=5)

    def test_total_price_no_local_calculation(self):
        """Test that use_total_price=True does not apply local fees/markup/vat"""
        ef = Energyforecast(self.timezone, self.token, use_total_price=True)
        ef.set_price_parameters(vat=0.19, price_fees=0.10, price_markup=0.05)

        raw_data = {
            'data': [
                {
                    'start': '2024-06-20T10:00:00+02:00',
                    'end': '2024-06-20T10:15:00+02:00',
                    'price_ct_kwh': 20.0,
                    'total_ct_kwh': 35.0,
                    'price_origin': 'market'
                }
            ]
        }
        ef.store_raw_data(raw_data)

        with patch('batcontrol.dynamictariff.energyforecast.datetime') as mock_datetime:
            mock_now = self.timezone.localize(datetime.datetime(2024, 6, 20, 10, 0, 0))
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.datetime.fromisoformat = datetime.datetime.fromisoformat

            prices = ef._get_prices_native()

        # total_ct_kwh / 100 = 0.35, no local vat/fees/markup applied
        self.assertAlmostEqual(prices[0], 0.35, places=5)

    def test_total_price_api_call_without_overrides(self):
        """Test that use_total_price=True does not send vat=0/fixed_cost_cent=0"""
        ef = Energyforecast(self.timezone, self.token, use_total_price=True)

        with patch('batcontrol.dynamictariff.energyforecast.requests.get') as mock_get:
            mock_response = unittest.mock.Mock()
            mock_response.json.return_value = {'data': []}
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response

            ef.get_raw_data_from_provider()

            params = mock_get.call_args.kwargs['params']
            self.assertNotIn('vat', params)
            self.assertNotIn('fixed_cost_cent', params)

    def test_default_mode_api_call_has_overrides(self):
        """Test that default mode (use_total_price=False) still sends vat=0/fixed_cost_cent=0"""
        ef = Energyforecast(self.timezone, self.token)

        with patch('batcontrol.dynamictariff.energyforecast.requests.get') as mock_get:
            mock_response = unittest.mock.Mock()
            mock_response.json.return_value = {'data': []}
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response

            ef.get_raw_data_from_provider()

            params = mock_get.call_args.kwargs['params']
            self.assertEqual(params.get('vat'), 0)
            self.assertEqual(params.get('fixed_cost_cent'), 0)


if __name__ == '__main__':
    unittest.main()
