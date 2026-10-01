"""Relaxed Caching Module

This module provides a thread-safe caching mechanism with TTL (Time To Live) support.
It is designed to be used as a parent class for providers that need to cache API responses.

The RelaxedCaching class uses Python's cachetools library (TTLCache) for efficient
caching with automatic expiration and size management.
"""

import time
import logging
import threading
from typing import Any, Optional
from cachetools import TTLCache

logger = logging.getLogger(__name__)


class CacheMissError(RuntimeError):
    """Exception raised when attempting to retrieve a cache entry that doesn't exist"""


class RelaxedCaching:
    """Thread-safe caching mechanism with TTL support using cachetools.TTLCache

    This class provides a caching layer for API providers with the following features:
    - Thread-safe operations using locks
    - Automatic cache size management via TTLCache (keeps max N entries)
    - TTL-based cache expiration (default: 18 hours) via TTLCache
    - Timestamp-based entry keys

    Attributes:
        entry_key (Optional[float]): Timestamp of the last stored entry, None if cache is empty
        cache_store (TTLCache): TTLCache instance for storing cached entries
        ttl_seconds (int): Time-to-live for cache entries in seconds (default: 43200 = 12 hours)
        max_entries (int): Maximum number of entries to keep in cache (default: 2)
    """

    def __init__(self, ttl_hours: float = 12.0, max_entries: int = 2):
        """Initialize the RelaxedCaching instance

        Args:
            ttl_hours (float): Time-to-live for cache entries in hours (default: 12.0)
            max_entries (int): Maximum number of entries to keep in cache (default: 2)
        """
        self.entry_key: Optional[float] = None
        self.ttl_seconds: int = int(ttl_hours * 3600)
        self.max_entries: int = max_entries

        # Use cachetools.TTLCache for automatic TTL and size management
        self.cache_store: TTLCache = TTLCache(maxsize=max_entries, ttl=self.ttl_seconds)
        self._lock = threading.Lock()

        logger.debug(
            'Initialized RelaxedCaching with TTL=%d seconds (%0.1f hours) and max_entries=%d',
            self.ttl_seconds,
            ttl_hours,
            max_entries
        )

    def get_last_entry(self) -> Any:
        """Retrieve the last cached entry

        Returns the most recent cache entry based on the entry_key timestamp.
        TTLCache automatically handles expiration, so we only need to check if the key exists.

        Returns:
            Any: The cached data

        Raises:
            CacheMissError: If no entry exists or if the entry has expired (removed by TTLCache)
        """
        with self._lock:
            if self.entry_key is None:
                logger.debug('Cache miss: entry_key is None')
                raise CacheMissError('No cache entry available (entry_key is None)')

            # TTLCache automatically removes expired entries
            if self.entry_key not in self.cache_store:
                logger.error(
                    'Cache miss: entry_key %s not found (expired or evicted)',
                    self.entry_key
                )
                raise CacheMissError(
                    f'Cache entry for key {self.entry_key} '
                    f'not found (expired or evicted by TTLCache)'
                )

            return self.cache_store[self.entry_key]

    def store_new_entry(self, data: Any) -> float:
        """Store a new entry in the cache

        Creates a new cache entry with the current timestamp as key.
        Updates entry_key to point to the new entry.
        TTLCache automatically manages size and expiration.

        Args:
            data (Any): The data to cache

        Returns:
            float: The timestamp key of the stored entry
        """
        with self._lock:
            # Get current timestamp as key
            timestamp = time.time()

            # Store the new entry (TTLCache handles size management automatically)
            self.cache_store[timestamp] = data

            # Update entry_key to point to the new entry (blocking operation for other threads)
            self.entry_key = timestamp

            logger.debug(
                'Stored new cache entry with key %s (total entries: %d)',
                timestamp,
                len(self.cache_store)
            )

            return timestamp

    def clear_cache(self) -> None:
        """Clear all cache entries

        Removes all entries from the cache and resets entry_key to None.
        """
        with self._lock:
            entries_count = len(self.cache_store)
            self.cache_store.clear()
            self.entry_key = None
            logger.info('Cache cleared (%d entries removed)', entries_count)

    def get_cache_info(self) -> dict:
        """Get information about the current cache state

        Returns:
            dict: Dictionary containing cache statistics including:
                - entry_count: Number of entries in cache
                - entry_key: Timestamp of last entry (or None)
                - oldest_entry: Timestamp of oldest entry (or None)
                - newest_entry: Timestamp of newest entry (or None)
                - cache_age_seconds: Age of the last entry in seconds (or None)
        """
        with self._lock:
            info = {
                'entry_count': len(self.cache_store),
                'entry_key': self.entry_key,
                'oldest_entry': min(self.cache_store.keys()) if self.cache_store else None,
                'newest_entry': max(self.cache_store.keys()) if self.cache_store else None,
                'cache_age_seconds': None
            }

            if self.entry_key is not None:
                info['cache_age_seconds'] = int(time.time() - self.entry_key)

            return info
