"""Unit tests for fetcher.relaxed_caching module"""

import threading
import pytest
from batcontrol.fetcher import RelaxedCaching, CacheMissError


class TestRelaxedCaching:
    """Test suite for RelaxedCaching class"""

    def test_initialization(self):
        """Test that RelaxedCaching initializes correctly"""
        cache = RelaxedCaching()
        assert cache.entry_key is None
        assert cache.cache_store == {}
        assert cache.ttl_seconds == 12 * 3600  # 12 hours in seconds
        assert cache.max_entries == 2

    def test_store_and_get(self):
        """Test basic store and retrieve"""
        cache = RelaxedCaching()
        test_data = {'key': 'value', 'number': 42}

        cache.store_new_entry(test_data)
        retrieved = cache.get_last_entry()

        assert retrieved == test_data

    def test_cache_miss_empty(self):
        """Test CacheMissError when cache is empty"""
        cache = RelaxedCaching()

        with pytest.raises(CacheMissError) as exc_info:
            cache.get_last_entry()

        assert 'entry_key is None' in str(exc_info.value)

    def test_cache_expiration(self, fake_clock):
        """Test cache expiration after TTL"""
        # ttl_seconds is truncated to int: int(0.001 * 3600) == 3
        cache = RelaxedCaching(ttl_hours=0.001, wall_clock=fake_clock)
        assert cache.ttl_seconds == 3
        cache.store_new_entry({'data': 'test'})

        fake_clock.advance(2.9)
        assert cache.get_last_entry() == {'data': 'test'}

        fake_clock.advance(0.1)

        with pytest.raises(CacheMissError) as exc_info:
            cache.get_last_entry()

        assert 'expired' in str(exc_info.value)

    def test_max_entries_limit(self, fake_clock):
        """Test that cache respects max_entries limit"""
        cache = RelaxedCaching(max_entries=3, wall_clock=fake_clock)

        for i in range(4):
            cache.store_new_entry({'entry': i})
            fake_clock.advance(1)

        assert len(cache.cache_store) == 3
        assert cache.get_last_entry() == {'entry': 3}

    def test_thread_safety(self, fake_clock):
        """Test thread safety with concurrent operations"""
        fake_clock.tick = 0.001  # distinct key per call
        cache = RelaxedCaching(wall_clock=fake_clock)
        results = []

        def worker(thread_id):
            cache.store_new_entry({'thread': thread_id})
            try:
                data = cache.get_last_entry()
                results.append(data)
            except CacheMissError:
                pass

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have succeeded without errors
        assert len(cache.cache_store) <= cache.max_entries
