"""
Test module for DynamicTariffBaseclass and providers
"""
import pytest
import pytz
from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch
from batcontrol.dynamictariff.baseclass import DynamicTariffBaseclass


class ConcreteTariffProvider(DynamicTariffBaseclass):
    """Concrete implementation of DynamicTariffBaseclass for testing"""

    def __init__(self, timezone, target_resolution=60, native_resolution=60,
                 mock_prices_func=None):
        super().__init__(
            timezone,
            min_time_between_API_calls=900,
            delay_evaluation_by_seconds=0,
            target_resolution=target_resolution,
            native_resolution=native_resolution
        )
        self.mock_prices_func = mock_prices_func

    def _get_prices_native(self) -> dict[int, float]:
        """Mock implementation returning test data"""
        if self.mock_prices_func:
            return self.mock_prices_func()
        # Return simple test data: 0.10 EUR/kWh per hour for 24 hours
        if self.native_resolution == 60:
            return {h: 0.10 + h * 0.01 for h in range(24)}
        else:  # 15-min
            return {i: 0.10 + (i // 4) * 0.01 for i in range(96)}

    def get_raw_data_from_provider(self) -> dict:
        """Mock implementation"""
        return {}


class TestDynamicTariffBaseclass:
    """Tests for DynamicTariffBaseclass"""

    @pytest.fixture
    def timezone(self):
        """Fixture for timezone"""
        return pytz.timezone('Europe/Berlin')

    @pytest.fixture
    def baseclass_60min(self, timezone):
        """Fixture for baseclass instance with 60-min resolution"""
        return ConcreteTariffProvider(
            timezone, target_resolution=60, native_resolution=60)

    @pytest.fixture
    def baseclass_15min_target(self, timezone):
        """Fixture for baseclass with 15-min target from 60-min native"""
        return ConcreteTariffProvider(
            timezone, target_resolution=15, native_resolution=60)

    @pytest.fixture
    def baseclass_15min_native(self, timezone):
        """Fixture for baseclass with 15-min native data"""
        return ConcreteTariffProvider(
            timezone, target_resolution=15, native_resolution=15)

    def test_initialization(self, baseclass_60min, timezone):
        """Test that baseclass initializes correctly"""
        assert baseclass_60min.timezone == timezone
        assert baseclass_60min.target_resolution == 60
        assert baseclass_60min.native_resolution == 60

    def test_initialization_15min_target(self, baseclass_15min_target):
        """Test initialization with 15-minute target resolution"""
        assert baseclass_15min_target.target_resolution == 15
        assert baseclass_15min_target.native_resolution == 60

    def test_get_prices_no_conversion_needed(self, timezone):
        """Test get_prices when native and target resolution match"""
        mock_now = datetime(2024, 1, 15, 10, 20, 0,
                            tzinfo=dt_timezone.utc).astimezone(timezone)

        with patch('batcontrol.dynamictariff.baseclass.datetime') as mock_datetime:
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.timezone = dt_timezone

            instance = ConcreteTariffProvider(
                timezone, target_resolution=60, native_resolution=60)
            prices = instance.get_prices()

            # Should return prices starting from current hour
            assert len(prices) == 24
            assert all(isinstance(v, float) for v in prices.values())

    def test_replication_hourly_to_15min(self, timezone):
        """Test price replication from hourly to 15-min"""
        mock_now = datetime(2024, 1, 15, 10, 0, 0,
                            tzinfo=dt_timezone.utc).astimezone(timezone)

        with patch('batcontrol.dynamictariff.baseclass.datetime') as mock_datetime:
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.timezone = dt_timezone

            # Define hourly prices
            def mock_hourly_prices():
                return {0: 0.10, 1: 0.15, 2: 0.20}

            instance = ConcreteTariffProvider(
                timezone, target_resolution=15, native_resolution=60,
                mock_prices_func=mock_hourly_prices)
            prices = instance.get_prices()

            # Each hour should be replicated to 4 quarters
            # Hour 0 (0.10) -> intervals 0,1,2,3
            # Hour 1 (0.15) -> intervals 4,5,6,7
            # Hour 2 (0.20) -> intervals 8,9,10,11
            assert len(prices) == 12
            assert prices[0] == 0.10
            assert prices[1] == 0.10
            assert prices[2] == 0.10
            assert prices[3] == 0.10
            assert prices[4] == 0.15
            assert prices[5] == 0.15

    def test_averaging_15min_to_hourly(self, timezone):
        """Test price averaging from 15-min to hourly"""
        mock_now = datetime(2024, 1, 15, 10, 0, 0,
                            tzinfo=dt_timezone.utc).astimezone(timezone)

        with patch('batcontrol.dynamictariff.baseclass.datetime') as mock_datetime:
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.timezone = dt_timezone

            # Define 15-min prices
            def mock_15min_prices():
                return {
                    0: 0.10, 1: 0.12, 2: 0.14, 3: 0.16,  # Hour 0: avg = 0.13
                    4: 0.20, 5: 0.20, 6: 0.20, 7: 0.20,  # Hour 1: avg = 0.20
                }

            instance = ConcreteTariffProvider(
                timezone, target_resolution=60, native_resolution=15,
                mock_prices_func=mock_15min_prices)
            prices = instance.get_prices()

            # Should average to hourly
            assert len(prices) == 2
            assert abs(prices[0] - 0.13) < 0.001  # (0.10+0.12+0.14+0.16)/4
            assert abs(prices[1] - 0.20) < 0.001

    def test_interval_alignment(self, timezone):
        """Test that prices are aligned to current interval"""
        # Mock time to 10:20 (in the middle of second 15-min interval)
        mock_now = datetime(2024, 1, 15, 10, 20, 0,
                            tzinfo=dt_timezone.utc).astimezone(timezone)

        with patch('batcontrol.dynamictariff.baseclass.datetime') as mock_datetime:
            mock_datetime.datetime.now.return_value = mock_now
            mock_datetime.timezone = dt_timezone

            # Define hourly prices for replication
            def mock_hourly_prices():
                return {0: 0.10, 1: 0.15, 2: 0.20}

            instance = ConcreteTariffProvider(
                timezone, target_resolution=15, native_resolution=60,
                mock_prices_func=mock_hourly_prices)
            prices = instance.get_prices()

            # At 10:20, we're in interval 1 of the hour (10:15-10:30)
            # So we should get prices starting from interval 1
            # Original: {0:0.10, 1:0.10, 2:0.10, 3:0.10, 4:0.15, 5:0.15, ...}
            # After shift (drop interval 0): {0:0.10, 1:0.10, 2:0.10, 3:0.15, ...}
            assert len(prices) == 11  # 12 - 1 dropped
            assert 0 in prices
            # First interval should be the remaining part of first hour (still 0.10)
            assert prices[0] == 0.10

    def test_empty_native_prices(self, timezone):
        """Test handling of empty native prices"""
        def mock_empty_prices():
            return {}

        instance = ConcreteTariffProvider(
            timezone, target_resolution=60, native_resolution=60,
            mock_prices_func=mock_empty_prices
        )

        prices = instance.get_prices()
        assert prices == {}

    def test_replicate_hourly_to_15min_method(self, baseclass_15min_target):
        """Test _replicate_hourly_to_15min directly"""
        hourly = {0: 0.10, 1: 0.20, 2: 0.30}
        result = baseclass_15min_target._replicate_hourly_to_15min(hourly)

        # Should have 12 intervals (3 hours * 4 quarters)
        assert len(result) == 12

        # Check replication
        for h in range(3):
            for q in range(4):
                idx = h * 4 + q
                assert result[idx] == hourly[h]


class TestAwattarProvider:
    """Tests for Awattar provider"""

    @pytest.fixture
    def timezone(self):
        return pytz.timezone('Europe/Berlin')

    def test_awattar_initialization(self, timezone):
        """Test Awattar provider initialization"""
        from batcontrol.dynamictariff.awattar import Awattar

        provider = Awattar(timezone, 'at', 900, 0, target_resolution=60)
        assert provider.native_resolution == 60
        assert provider.target_resolution == 60
        assert 'awattar.at' in provider.url

    def test_awattar_15min_target(self, timezone):
        """Test Awattar with 15-min target (should replicate)"""
        from batcontrol.dynamictariff.awattar import Awattar

        provider = Awattar(timezone, 'de', 900, 0, target_resolution=15)
        assert provider.native_resolution == 60  # Awattar only provides hourly
        assert provider.target_resolution == 15


class TestTibberProvider:
    """Tests for Tibber provider"""

    @pytest.fixture
    def timezone(self):
        return pytz.timezone('Europe/Berlin')

    def test_tibber_initialization_hourly(self, timezone):
        """Test Tibber provider initialization with hourly"""
        from batcontrol.dynamictariff.tibber import Tibber

        provider = Tibber(timezone, 'test_token', 900, 0, target_resolution=60)
        assert provider.native_resolution == 60
        assert provider.target_resolution == 60
        assert provider.api_resolution == "HOURLY"

    def test_tibber_initialization_15min(self, timezone):
        """Test Tibber provider initialization with 15-min"""
        from batcontrol.dynamictariff.tibber import Tibber

        provider = Tibber(timezone, 'test_token', 900, 0, target_resolution=15)
        assert provider.native_resolution == 15
        assert provider.target_resolution == 15
        assert provider.api_resolution == "QUARTER_HOURLY"


class TestEvccProvider:
    """Tests for evcc provider"""

    @pytest.fixture
    def timezone(self):
        return pytz.timezone('Europe/Berlin')

    def test_evcc_initialization(self, timezone):
        """Test evcc provider initialization"""
        from batcontrol.dynamictariff.evcc import Evcc

        provider = Evcc(timezone, 'http://evcc.local/api/tariff/grid', 60,
                        target_resolution=60)
        assert provider.native_resolution == 15  # evcc native is 15-min
        assert provider.target_resolution == 60

    def test_evcc_15min_target(self, timezone):
        """Test evcc with 15-min target (no conversion needed)"""
        from batcontrol.dynamictariff.evcc import Evcc

        provider = Evcc(timezone, 'http://evcc.local/api/tariff/grid', 60,
                        target_resolution=15)
        assert provider.native_resolution == 15
        assert provider.target_resolution == 15


class TestEnergyforecastProvider:
    """Tests for Energyforecast provider"""

    @pytest.fixture
    def timezone(self):
        return pytz.timezone('Europe/Berlin')

    def test_energyforecast_initialization_hourly(self, timezone):
        """Test Energyforecast provider initialization with hourly"""
        from batcontrol.dynamictariff.energyforecast import Energyforecast

        provider = Energyforecast(timezone, 'test_token', 900, 0,
                                  target_resolution=60)
        assert provider.native_resolution == 60
        assert provider.target_resolution == 60
        assert provider.api_resolution == "hourly"

    def test_energyforecast_initialization_15min(self, timezone):
        """Test Energyforecast provider initialization with 15-min"""
        from batcontrol.dynamictariff.energyforecast import Energyforecast

        provider = Energyforecast(timezone, 'test_token', 900, 0,
                                  target_resolution=15)
        assert provider.native_resolution == 15
        assert provider.target_resolution == 15
        assert provider.api_resolution == "quarter_hourly"


class TestDynamicTariffFactory:
    """Tests for the DynamicTariff factory"""

    @pytest.fixture
    def timezone(self):
        return pytz.timezone('Europe/Berlin')

    def test_factory_passes_target_resolution(self, timezone):
        """Test that factory passes target_resolution to providers"""
        from batcontrol.dynamictariff.dynamictariff import DynamicTariff

        config = {
            'type': 'awattar_at',
            'vat': 0.19,
            'markup': 0.01,
            'fees': 0.05
        }

        provider = DynamicTariff.create_tarif_provider(
            config, timezone, 900, 0, target_resolution=15
        )

        assert provider.target_resolution == 15
        assert provider.native_resolution == 60  # Awattar is always hourly

    def test_factory_default_resolution(self, timezone):
        """Test that factory uses default 60-min when target_resolution not provided"""
        from batcontrol.dynamictariff.dynamictariff import DynamicTariff

        config = {
            'type': 'awattar_de',
            'vat': 0.19,
            'markup': 0.01,
            'fees': 0.05
        }

        provider = DynamicTariff.create_tarif_provider(
            config, timezone, 900, 0
        )

        assert provider.target_resolution == 60
