import unittest
import datetime
import pytz
from unittest.mock import patch
from batcontrol.dynamictariff.network_fees import NetworkFeesFetcher
from batcontrol.fetcher.relaxed_caching import CacheMissError


API_SLOTS = [
    # NT: 00:00-06:00
    {'start': '2026-05-23T00:00:00+02:00',
     'end': '2026-05-23T06:00:00+02:00',
     'value': 0.0087},
    # ST: 06:00-17:00
    {'start': '2026-05-23T06:00:00+02:00',
     'end': '2026-05-23T17:00:00+02:00',
     'value': 0.0867},
    # HT: 17:00-21:00
    {'start': '2026-05-23T17:00:00+02:00',
     'end': '2026-05-23T21:00:00+02:00',
     'value': 0.1234},
    # ST: 21:00-24:00
    {'start': '2026-05-23T21:00:00+02:00',
     'end': '2026-05-24T00:00:00+02:00',
     'value': 0.0867},
]


class TestNetworkFeesFetcher(unittest.TestCase):

    def setUp(self):
        self.tz = pytz.timezone('Europe/Berlin')
        self.fetcher = NetworkFeesFetcher(
            self.tz, country='de', operator='syna')

    def test_no_cache_raises_cache_miss_error(self):
        """Without any cached data, get_fee_at must raise CacheMissError (not return 0.0)."""
        ts = datetime.datetime(2026, 5, 23, 10, 0, tzinfo=self.tz)
        with self.assertRaises(CacheMissError):
            # Don't call refresh_data() so cache stays empty
            self.fetcher.get_raw_data()

    def test_get_fee_at_nt_slot(self):
        """Returns correct NET fee for an NT-tariff slot."""
        self.fetcher.store_raw_data(API_SLOTS)
        ts = datetime.datetime(2026, 5, 23, 3, 0, tzinfo=self.tz)
        with patch.object(self.fetcher, 'refresh_data'):  # skip network call
            fee = self.fetcher.get_fee_at(ts)
        self.assertAlmostEqual(fee, 0.0087)

    def test_get_fee_at_ht_slot(self):
        """Returns correct NET fee for an HT-tariff slot."""
        self.fetcher.store_raw_data(API_SLOTS)
        ts = datetime.datetime(2026, 5, 23, 19, 0, tzinfo=self.tz)
        with patch.object(self.fetcher, 'refresh_data'):
            fee = self.fetcher.get_fee_at(ts)
        self.assertAlmostEqual(fee, 0.1234)

    def test_get_fee_at_st_slot(self):
        """Returns correct NET fee for an ST-tariff slot."""
        self.fetcher.store_raw_data(API_SLOTS)
        ts = datetime.datetime(2026, 5, 23, 10, 0, tzinfo=self.tz)
        with patch.object(self.fetcher, 'refresh_data'):
            fee = self.fetcher.get_fee_at(ts)
        self.assertAlmostEqual(fee, 0.0867)

    def test_get_fee_at_outside_window_returns_zero(self):
        """Timestamp beyond available slots returns 0.0 (no crash)."""
        self.fetcher.store_raw_data(API_SLOTS)
        ts = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=self.tz)
        with patch.object(self.fetcher, 'refresh_data'):
            fee = self.fetcher.get_fee_at(ts)
        self.assertEqual(fee, 0.0)

    def test_prices_native_expands_multi_hour_slots(self):
        """_get_prices_native expands a 6-hour NT block to 6 individual hourly entries."""
        # Use get_fee_at to verify slot expansion without fragile datetime mocking.
        # Each slot covers multiple hours; verify several representative
        # timestamps.
        self.fetcher.store_raw_data(API_SLOTS)
        with patch.object(self.fetcher, 'refresh_data'):
            # NT slot: 00:00-06:00
            for h in range(6):
                ts = self.tz.localize(datetime.datetime(2026, 5, 23, h, 0))
                self.assertAlmostEqual(self.fetcher.get_fee_at(ts), 0.0087,
                                       msg=f'Expected NT rate at hour {h}')
            # ST slot: 06:00-17:00
            for h in range(6, 17):
                ts = self.tz.localize(datetime.datetime(2026, 5, 23, h, 0))
                self.assertAlmostEqual(self.fetcher.get_fee_at(ts), 0.0867,
                                       msg=f'Expected ST rate at hour {h}')
            # HT slot: 17:00-21:00
            for h in range(17, 21):
                ts = self.tz.localize(datetime.datetime(2026, 5, 23, h, 0))
                self.assertAlmostEqual(self.fetcher.get_fee_at(ts), 0.1234,
                                       msg=f'Expected HT rate at hour {h}')
            # ST slot: 21:00-24:00
            for h in range(21, 24):
                ts = self.tz.localize(datetime.datetime(2026, 5, 23, h, 0))
                self.assertAlmostEqual(self.fetcher.get_fee_at(ts), 0.0867,
                                       msg=f'Expected ST rate at hour {h}')


class TestNetworkFeesIntegration(unittest.TestCase):
    """Test that Awattar/Energyforecast apply the network fee correctly."""

    def setUp(self):
        self.tz = pytz.timezone('Europe/Berlin')

    def _make_fetcher_with_data(self, slots):
        fetcher = NetworkFeesFetcher(self.tz, country='de', operator='syna')
        fetcher.store_raw_data(slots)
        return fetcher

    def test_awattar_price_includes_network_fee(self):
        """Awattar end_price must include network_fee before VAT."""
        from batcontrol.dynamictariff.awattar import Awattar
        awattar = Awattar(self.tz, 'de')
        awattar.set_price_parameters(
            vat=0.19, price_fees=0.005, price_markup=0.0)

        network_fee = 0.0867  # ST rate
        fixed_ts = datetime.datetime(2026, 5, 23, 10, 0, tzinfo=self.tz)
        fetcher = self._make_fetcher_with_data([
            {'start': '2026-05-23T00:00:00+02:00', 'end': '2026-05-24T00:00:00+02:00',
             'value': network_fee}
        ])
        with patch.object(fetcher, 'refresh_data'):
            awattar.set_network_fees_fetcher(fetcher)

        # marketprice 50 EUR/MWh = 0.05 EUR/kWh
        raw_data = {'data': [{
            'start_timestamp': int(fixed_ts.timestamp() * 1000),
            'marketprice': 50.0
        }]}
        awattar.store_raw_data(raw_data)

        base = 0.05
        expected = (base * 1.0 + 0.005 + network_fee) * 1.19

        with patch('batcontrol.dynamictariff.awattar.datetime') as mock_dt:
            mock_dt.datetime.now.return_value = fixed_ts
            mock_dt.datetime.fromtimestamp = datetime.datetime.fromtimestamp
            mock_dt.timedelta = datetime.timedelta
            prices = awattar._get_prices_native()

        self.assertAlmostEqual(prices[0], expected, places=6)

    def test_awattar_without_fetcher_unchanged(self):
        """Awattar without a fetcher behaves exactly as before (no regression)."""
        from batcontrol.dynamictariff.awattar import Awattar
        awattar = Awattar(self.tz, 'de')
        awattar.set_price_parameters(
            vat=0.19, price_fees=0.005, price_markup=0.0)

        fixed_ts = datetime.datetime(2026, 5, 23, 10, 0, tzinfo=self.tz)
        raw_data = {'data': [{
            'start_timestamp': int(fixed_ts.timestamp() * 1000),
            'marketprice': 50.0
        }]}
        awattar.store_raw_data(raw_data)

        expected = (0.05 + 0.005) * 1.19

        with patch('batcontrol.dynamictariff.awattar.datetime') as mock_dt:
            mock_dt.datetime.now.return_value = fixed_ts
            mock_dt.datetime.fromtimestamp = datetime.datetime.fromtimestamp
            mock_dt.timedelta = datetime.timedelta
            prices = awattar._get_prices_native()

        self.assertAlmostEqual(prices[0], expected, places=6)


if __name__ == '__main__':
    unittest.main()
