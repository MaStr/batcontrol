"""Tests for EVCC dynamic tariff provider with 15-minute native resolution.

The EVCC provider has native_resolution=15 and returns 15-minute interval prices directly.
Averaging to hourly is done by the baseclass when target_resolution=60.
"""
import unittest
import datetime
import pytz
from unittest.mock import patch
from batcontrol.dynamictariff.evcc import Evcc


class TestEvcc(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures"""
        self.timezone = pytz.timezone('Europe/Berlin')
        self.url = 'https://demo.evcc.io/api/tariff/grid'

    def test_hourly_price_to_15min_intervals(self):
        """Test that hourly prices are mapped to 15-min interval indices"""
        evcc = Evcc(self.timezone, self.url)

        # Mock raw data with one price per hour
        raw_data = {
            'rates': [
                {
                    'start': '2024-06-20T10:00:00+02:00',
                    'end': '2024-06-20T11:00:00+02:00',
                    'value': 0.25
                },
                {
                    'start': '2024-06-20T11:00:00+02:00',
                    'end': '2024-06-20T12:00:00+02:00',
                    'value': 0.30
                },
                {
                    'start': '2024-06-20T12:00:00+02:00',
                    'end': '2024-06-20T13:00:00+02:00',
                    'value': 0.28
                }
            ]
        }
        evcc.store_raw_data(raw_data)

        with patch('batcontrol.dynamictariff.evcc.datetime') as mock_datetime:
            mock_now = self.timezone.localize(datetime.datetime(2024, 6, 20, 10, 0, 0))
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.datetime.fromisoformat = datetime.datetime.fromisoformat

            prices = evcc._get_prices_native()

        # Hourly prices map to 15-min interval indices:
        # 10:00 -> interval 0, 11:00 -> interval 4, 12:00 -> interval 8
        self.assertEqual(prices[0], 0.25)
        self.assertEqual(prices[4], 0.30)
        self.assertEqual(prices[8], 0.28)

    def test_15min_prices_to_intervals(self):
        """Test that 15-min prices map to correct intervals"""
        evcc = Evcc(self.timezone, self.url)

        raw_data = {
            'rates': [
                {
                    'start': '2024-06-20T10:00:00+02:00',
                    'end': '2024-06-20T10:15:00+02:00',
                    'value': 0.20
                },
                {
                    'start': '2024-06-20T10:15:00+02:00',
                    'end': '2024-06-20T10:30:00+02:00',
                    'value': 0.22
                },
                {
                    'start': '2024-06-20T10:30:00+02:00',
                    'end': '2024-06-20T10:45:00+02:00',
                    'value': 0.24
                },
                {
                    'start': '2024-06-20T10:45:00+02:00',
                    'end': '2024-06-20T11:00:00+02:00',
                    'value': 0.26
                },
                {
                    'start': '2024-06-20T11:00:00+02:00',
                    'end': '2024-06-20T11:15:00+02:00',
                    'value': 0.30
                },
                {
                    'start': '2024-06-20T11:15:00+02:00',
                    'end': '2024-06-20T11:30:00+02:00',
                    'value': 0.32
                },
                {
                    'start': '2024-06-20T11:30:00+02:00',
                    'end': '2024-06-20T11:45:00+02:00',
                    'value': 0.34
                },
                {
                    'start': '2024-06-20T11:45:00+02:00',
                    'end': '2024-06-20T12:00:00+02:00',
                    'value': 0.36
                }
            ]
        }
        evcc.store_raw_data(raw_data)

        with patch('batcontrol.dynamictariff.evcc.datetime') as mock_datetime:
            mock_now = self.timezone.localize(datetime.datetime(2024, 6, 20, 10, 0, 0))
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.datetime.fromisoformat = datetime.datetime.fromisoformat

            prices = evcc._get_prices_native()

        # Each 15-min interval has its own price
        # Hour 0: intervals 0, 1, 2, 3
        self.assertEqual(prices[0], 0.20)
        self.assertEqual(prices[1], 0.22)
        self.assertEqual(prices[2], 0.24)
        self.assertEqual(prices[3], 0.26)
        # Hour 1: intervals 4, 5, 6, 7
        self.assertEqual(prices[4], 0.30)
        self.assertEqual(prices[5], 0.32)
        self.assertEqual(prices[6], 0.34)
        self.assertEqual(prices[7], 0.36)

    def test_price_field_compatibility(self):
        """Test compatibility with 'price' field (pre-0.203.0)"""
        evcc = Evcc(self.timezone, self.url)

        evcc.store_raw_data({
            'rates': [
                {
                    'start': '2024-06-20T10:00:00+02:00',
                    'end': '2024-06-20T10:15:00+02:00',
                    'price': 0.20
                },
                {
                    'start': '2024-06-20T10:15:00+02:00',
                    'end': '2024-06-20T10:30:00+02:00',
                    'price': 0.22
                }
            ]
        })

        with patch('batcontrol.dynamictariff.evcc.datetime') as mock_datetime:
            mock_now = self.timezone.localize(datetime.datetime(2024, 6, 20, 10, 0, 0))
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.datetime.fromisoformat = datetime.datetime.fromisoformat

            prices = evcc._get_prices_native()

        # Should read from 'price' field correctly
        self.assertEqual(prices[0], 0.20)
        self.assertEqual(prices[1], 0.22)

    def test_result_field_compatibility(self):
        """Test compatibility with old API format (pre-0.207.0)"""
        evcc = Evcc(self.timezone, self.url)

        evcc.store_raw_data({
            'result': {
                'rates': [
                    {
                        'start': '2024-06-20T10:00:00+02:00',
                        'end': '2024-06-20T11:00:00+02:00',
                        'price': 0.25
                    }
                ]
            }
        })

        with patch('batcontrol.dynamictariff.evcc.datetime') as mock_datetime:
            mock_now = self.timezone.localize(datetime.datetime(2024, 6, 20, 10, 0, 0))
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.datetime.fromisoformat = datetime.datetime.fromisoformat

            prices = evcc._get_prices_native()

        self.assertEqual(prices[0], 0.25)

    def test_mixed_granularity_prices(self):
        """Test with mixed hourly and 15-min prices"""
        evcc = Evcc(self.timezone, self.url)

        evcc.store_raw_data({
            'rates': [
                # Hour 0: single hourly price
                {
                    'start': '2024-06-20T10:00:00+02:00',
                    'end': '2024-06-20T11:00:00+02:00',
                    'value': 0.25
                },
                # Hour 1: four 15-minute prices
                {
                    'start': '2024-06-20T11:00:00+02:00',
                    'end': '2024-06-20T11:15:00+02:00',
                    'value': 0.30
                },
                {
                    'start': '2024-06-20T11:15:00+02:00',
                    'end': '2024-06-20T11:30:00+02:00',
                    'value': 0.32
                },
                {
                    'start': '2024-06-20T11:30:00+02:00',
                    'end': '2024-06-20T11:45:00+02:00',
                    'value': 0.34
                },
                {
                    'start': '2024-06-20T11:45:00+02:00',
                    'end': '2024-06-20T12:00:00+02:00',
                    'value': 0.36
                },
                # Hour 2: single hourly price
                {
                    'start': '2024-06-20T12:00:00+02:00',
                    'end': '2024-06-20T13:00:00+02:00',
                    'value': 0.28
                }
            ]
        })

        with patch('batcontrol.dynamictariff.evcc.datetime') as mock_datetime:
            mock_now = self.timezone.localize(datetime.datetime(2024, 6, 20, 10, 0, 0))
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.datetime.fromisoformat = datetime.datetime.fromisoformat

            prices = evcc._get_prices_native()

        # Hour 0: interval 0 = 0.25 (hourly price at start of hour)
        self.assertEqual(prices[0], 0.25)
        # Hour 1: intervals 4-7 = individual 15-min prices
        self.assertEqual(prices[4], 0.30)
        self.assertEqual(prices[5], 0.32)
        self.assertEqual(prices[6], 0.34)
        self.assertEqual(prices[7], 0.36)
        # Hour 2: interval 8 = 0.28 (hourly price at start of hour)
        self.assertEqual(prices[8], 0.28)

    def test_native_resolution_is_15min(self):
        """Test that EVCC provider has native 15-min resolution"""
        evcc = Evcc(self.timezone, self.url)
        self.assertEqual(evcc.native_resolution, 15)

    def test_filters_past_intervals(self):
        """Test that past intervals are filtered out"""
        evcc = Evcc(self.timezone, self.url)

        raw_data = {
            'rates': [
                # This is in the past (9:00 when current time is 10:00)
                {
                    'start': '2024-06-20T09:00:00+02:00',
                    'end': '2024-06-20T10:00:00+02:00',
                    'value': 0.15
                },
                # This is current/future
                {
                    'start': '2024-06-20T10:00:00+02:00',
                    'end': '2024-06-20T11:00:00+02:00',
                    'value': 0.25
                }
            ]
        }
        evcc.store_raw_data(raw_data)

        with patch('batcontrol.dynamictariff.evcc.datetime') as mock_datetime:
            mock_now = self.timezone.localize(datetime.datetime(2024, 6, 20, 10, 0, 0))
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.datetime.fromisoformat = datetime.datetime.fromisoformat

            prices = evcc._get_prices_native()

        # Past price should not be included (negative interval)
        self.assertNotIn(-4, prices)  # 9:00 is 4 intervals before 10:00
        # Current price should be at interval 0
        self.assertEqual(prices[0], 0.25)

    def test_time_consistency_within_15min_interval(self):
        """Test price calculation consistency at different points within 15-min interval

        This verifies interval calculation is consistent regardless of when called.
        """
        evcc = Evcc(self.timezone, self.url)

        # Mock raw data with 15-minute intervals at 10:00
        evcc.store_raw_data({
            'rates': [
                {
                    'start': '2024-06-20T10:00:00+02:00',
                    'end': '2024-06-20T10:15:00+02:00',
                    'value': 0.20
                },
                {
                    'start': '2024-06-20T10:15:00+02:00',
                    'end': '2024-06-20T10:30:00+02:00',
                    'value': 0.22
                },
                {
                    'start': '2024-06-20T10:30:00+02:00',
                    'end': '2024-06-20T10:45:00+02:00',
                    'value': 0.24
                },
                {
                    'start': '2024-06-20T10:45:00+02:00',
                    'end': '2024-06-20T11:00:00+02:00',
                    'value': 0.26
                }
            ]
        })

        # Test at different times within the 10:00-10:15 interval
        # All should produce consistent results
        test_times = [
            datetime.datetime(2024, 6, 20, 10, 0, 0),   # At interval start
            datetime.datetime(2024, 6, 20, 10, 7, 30),  # Mid interval
            datetime.datetime(2024, 6, 20, 10, 14, 59), # End of interval
        ]

        for test_time in test_times:
            with patch('batcontrol.dynamictariff.evcc.datetime') as mock_datetime:
                mock_now = self.timezone.localize(test_time)
                mock_datetime.datetime.now.return_value = mock_now
                mock_datetime.datetime.fromisoformat = datetime.datetime.fromisoformat

                prices = evcc._get_prices_native()

                # All test times should see interval 0 with price 0.20
                self.assertIn(0, prices,
                    f"interval 0 should be present at {test_time.strftime('%H:%M:%S')}")
                self.assertEqual(
                    prices[0],
                    0.20,
                    msg=f"Price at {test_time.strftime('%H:%M:%S')} should be 0.20"
                )

    def test_get_prices_with_target_resolution_60(self):
        """Test that get_prices() averages to hourly when target_resolution=60"""
        evcc = Evcc(self.timezone, self.url, target_resolution=60)

        raw_data = {
            'rates': [
                {
                    'start': '2024-06-20T10:00:00+02:00',
                    'end': '2024-06-20T10:15:00+02:00',
                    'value': 0.20
                },
                {
                    'start': '2024-06-20T10:15:00+02:00',
                    'end': '2024-06-20T10:30:00+02:00',
                    'value': 0.22
                },
                {
                    'start': '2024-06-20T10:30:00+02:00',
                    'end': '2024-06-20T10:45:00+02:00',
                    'value': 0.24
                },
                {
                    'start': '2024-06-20T10:45:00+02:00',
                    'end': '2024-06-20T11:00:00+02:00',
                    'value': 0.26
                }
            ]
        }
        evcc.store_raw_data(raw_data)

        # get_prices() triggers refresh_data() which calls get_raw_data_from_provider()
        # on the first invocation (next_update_ts == 0). Mock it to return the same
        # pre-loaded data so no real network request is made.
        with patch.object(evcc, 'get_raw_data_from_provider', return_value=raw_data):
            with patch('batcontrol.dynamictariff.evcc.datetime') as mock_datetime:
                mock_now = self.timezone.localize(datetime.datetime(2024, 6, 20, 10, 0, 0))
                mock_datetime.datetime.now.return_value = mock_now
                mock_datetime.datetime.fromisoformat = datetime.datetime.fromisoformat

                with patch('batcontrol.dynamictariff.baseclass.datetime') as mock_base_datetime:
                    mock_base_datetime.datetime.now.return_value = mock_now
                    mock_base_datetime.timezone = datetime.timezone

                    prices = evcc.get_prices()

        # When target_resolution=60, baseclass averages 15-min prices to hourly
        # Average of 0.20, 0.22, 0.24, 0.26 = 0.23
        self.assertAlmostEqual(prices[0], 0.23, places=5)


if __name__ == '__main__':
    unittest.main()
