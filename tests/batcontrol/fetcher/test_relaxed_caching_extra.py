"""Additional unit tests for fetcher.relaxed_caching.

These tests used to live in ``tests/batcontrol/fetcher/__init__.py`` where
pytest never collected them.
"""

import threading

from batcontrol.fetcher import RelaxedCaching, CacheMissError


class TestRelaxedCachingExtra:
    """Extra test suite for RelaxedCaching"""

    def test_initialization_custom_ttl(self):
        """Test initialization with custom TTL"""
        cache = RelaxedCaching(ttl_hours=6.0)
        assert cache.ttl_seconds == 6 * 3600

    def test_initialization_custom_max_entries(self):
        """Test initialization with custom max_entries"""
        cache = RelaxedCaching(max_entries=5)
        assert cache.max_entries == 5

    def test_store_new_entry(self, fake_clock):
        """Test storing a new entry"""
        cache = RelaxedCaching(wall_clock=fake_clock)
        test_data = {'key': 'value', 'number': 42}

        timestamp = cache.store_new_entry(test_data)

        assert timestamp == fake_clock.now
        assert cache.entry_key == timestamp
        assert timestamp in cache.cache_store
        assert cache.cache_store[timestamp] == test_data
        assert len(cache.cache_store) == 1

    def test_multiple_entries(self, fake_clock):
        """Test storing multiple entries"""
        cache = RelaxedCaching(max_entries=3, wall_clock=fake_clock)

        data1 = {'entry': 1}
        data2 = {'entry': 2}
        data3 = {'entry': 3}

        timestamp1 = cache.store_new_entry(data1)
        fake_clock.advance(1)
        timestamp2 = cache.store_new_entry(data2)
        fake_clock.advance(1)
        timestamp3 = cache.store_new_entry(data3)

        # Should have 3 entries
        assert len(cache.cache_store) == 3

        # Last entry should be data3
        assert cache.get_last_entry() == data3
        assert cache.entry_key == timestamp3

        # All entries should be in cache
        assert timestamp1 in cache.cache_store
        assert timestamp2 in cache.cache_store
        assert timestamp3 in cache.cache_store

    def test_clear_cache(self, fake_clock):
        """Test clearing the cache"""
        cache = RelaxedCaching(wall_clock=fake_clock)

        cache.store_new_entry({'data': 'test1'})
        fake_clock.advance(1)
        cache.store_new_entry({'data': 'test2'})

        assert len(cache.cache_store) == 2
        assert cache.entry_key is not None

        cache.clear_cache()

        assert len(cache.cache_store) == 0
        assert cache.entry_key is None

    def test_get_cache_info_empty(self):
        """Test get_cache_info on empty cache"""
        cache = RelaxedCaching()

        info = cache.get_cache_info()

        assert info['entry_count'] == 0
        assert info['entry_key'] is None
        assert info['oldest_entry'] is None
        assert info['newest_entry'] is None
        assert info['cache_age_seconds'] is None

    def test_get_cache_info_with_entries(self, fake_clock):
        """Test get_cache_info with entries"""
        cache = RelaxedCaching(wall_clock=fake_clock)

        first = cache.store_new_entry({'data': 'test1'})
        fake_clock.advance(10)
        second = cache.store_new_entry({'data': 'test2'})
        fake_clock.advance(5)

        info = cache.get_cache_info()

        assert info['entry_count'] == 2
        assert info['entry_key'] == second
        assert info['oldest_entry'] == first
        assert info['newest_entry'] == second
        assert info['cache_age_seconds'] == 5

    def test_thread_safety_concurrent_stores(self, fake_clock):
        """Test thread safety with concurrent store operations"""
        fake_clock.tick = 0.001  # distinct key per call
        cache = RelaxedCaching(wall_clock=fake_clock)
        results = []

        def store_data(thread_id):
            timestamp = cache.store_new_entry({'thread': thread_id})
            results.append(timestamp)

        threads = [threading.Thread(target=store_data, args=(i,))
                   for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All stores should have succeeded
        assert len(results) == 10

        # Cache should contain at most max_entries
        assert len(cache.cache_store) <= cache.max_entries

        # entry_key should be one of the stored timestamps
        assert cache.entry_key in results

    def test_thread_safety_concurrent_reads(self):
        """Test thread safety with concurrent read operations"""
        cache = RelaxedCaching()
        cache.store_new_entry({'data': 'test_data'})

        results = []
        errors = []

        def read_data():
            try:
                results.append(cache.get_last_entry())
            except CacheMissError as e:
                errors.append(e)

        threads = [threading.Thread(target=read_data) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All reads should have succeeded
        assert len(results) == 10
        assert len(errors) == 0
        assert all(r == {'data': 'test_data'} for r in results)

    def test_thread_safety_mixed_operations(self, fake_clock):
        """Test thread safety with mixed read/write operations"""
        fake_clock.tick = 0.001  # distinct key per call
        cache = RelaxedCaching(max_entries=5, wall_clock=fake_clock)
        cache.store_new_entry({'initial': 'data'})

        reads = []

        def worker(worker_id):
            for i in range(5):
                if i % 2 == 0:
                    cache.store_new_entry({'worker': worker_id, 'iteration': i})
                else:
                    # No expiry with the fake clock: every read must succeed
                    reads.append(cache.get_last_entry())

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Cache should be valid after all operations
        assert len(reads) == 10
        assert cache.entry_key is not None
        assert len(cache.cache_store) <= cache.max_entries

        # Should be able to retrieve last entry
        assert isinstance(cache.get_last_entry(), dict)

    def test_store_complex_data_structures(self):
        """Test storing complex data structures"""
        cache = RelaxedCaching()

        complex_data = {
            'prices': {0: 10.5, 1: 12.3, 2: 8.7},
            'metadata': {
                'provider': 'test',
                'timestamp': 1_700_000_000.0,
                'nested': {'key': 'value'}
            },
            'list_data': [1, 2, 3, 4, 5]
        }

        cache.store_new_entry(complex_data)
        retrieved = cache.get_last_entry()

        assert retrieved == complex_data
        assert retrieved['prices'][1] == 12.3
        assert retrieved['metadata']['nested']['key'] == 'value'
