"""Relaxed Caching Module

This module provides a thread-safe caching mechanism with TTL (Time To Live) support.
It is designed to be used as a parent class for providers that need to cache API responses.

The RelaxedCaching class uses Python's cachetools library (TTLCache) for efficient
caching with automatic expiration and size management.
"""

import json
import logging
import math
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Optional

from cachetools import TTLCache

logger = logging.getLogger(__name__)


class CacheMissError(RuntimeError):
    """Exception raised when attempting to retrieve a cache entry that doesn't exist"""


def _decode_persisted_entry(document: Any, now: float, ttl_seconds: int):
    """Validate an on-disk entry and return its original timestamp and data."""
    if not isinstance(document, dict):
        raise CacheMissError('Persisted cache document is not an object')
    if 'stored_at' not in document or 'data' not in document:
        raise CacheMissError('Persisted cache document is incomplete')

    stored_at = document['stored_at']
    if isinstance(stored_at, bool) or not isinstance(stored_at, (int, float)):
        raise CacheMissError('Persisted cache timestamp is invalid')
    if not math.isfinite(stored_at):
        raise CacheMissError('Persisted cache timestamp is invalid')

    age = now - float(stored_at)
    if age < 0:
        raise CacheMissError('Persisted cache timestamp is in the future')
    if age >= ttl_seconds:
        raise CacheMissError('Persisted cache entry has expired')

    return float(stored_at), document['data']


class RelaxedCaching:
    """Thread-safe caching mechanism with TTL support using cachetools.TTLCache

    This class provides a caching layer for API providers with the following features:
    - Thread-safe operations using locks
    - Automatic cache size management via TTLCache (keeps max N entries)
    - TTL-based cache expiration (default: 12 hours) via TTLCache
    - Timestamp-based entry keys
    - Optional atomic persistence of the newest entry across restarts

    Attributes:
        entry_key (Optional[float]): Timestamp of the last stored entry, None if cache is empty
        cache_store (TTLCache): TTLCache instance for storing cached entries
        ttl_seconds (int): Time-to-live for cache entries in seconds (default: 43200 = 12 hours)
        max_entries (int): Maximum number of entries to keep in cache (default: 2)
    """

    def __init__(
            self,
            ttl_hours: float = 12.0,
            max_entries: int = 2,
            persistence_path=None,
            wall_clock: Callable[[], float] = time.time,
    ):
        """Initialize the RelaxedCaching instance

        Args:
            ttl_hours (float): Time-to-live for cache entries in hours (default: 12.0)
            max_entries (int): Maximum number of entries to keep in cache (default: 2)
            persistence_path: Optional JSON file for the newest cache entry
            wall_clock: Injectable epoch clock used for restart-safe age checks
        """
        self.entry_key: Optional[float] = None
        self.ttl_seconds: int = int(ttl_hours * 3600)
        self.max_entries: int = max_entries
        self._persistence = (
            Path(persistence_path) if persistence_path is not None else None,
            wall_clock,
        )
        self._entry_loaded_from_disk = False

        # Use cachetools.TTLCache for automatic TTL and size management
        self.cache_store: TTLCache = TTLCache(
            maxsize=max_entries,
            ttl=self.ttl_seconds,
        )
        self._lock = threading.Lock()

        logger.debug(
            'Initialized RelaxedCaching with TTL=%d seconds (%0.1f hours) and max_entries=%d',
            self.ttl_seconds,
            ttl_hours,
            max_entries
        )
        self._load_persisted_entry()

    def _load_persisted_entry(self) -> None:
        """Load a fresh restart entry, treating every file problem as a miss."""
        persistence_path, wall_clock = self._persistence
        if persistence_path is None:
            return

        try:
            with persistence_path.open('r', encoding='utf-8') as handle:
                document = json.load(handle)
            stored_at, data = _decode_persisted_entry(
                document,
                wall_clock(),
                self.ttl_seconds,
            )
        except FileNotFoundError:
            return
        except (OSError, UnicodeError, json.JSONDecodeError, CacheMissError):
            logger.warning('Ignoring unusable persisted cache entry')
            return

        self.cache_store[stored_at] = data
        self.entry_key = stored_at
        self._entry_loaded_from_disk = True
        logger.info(
            'Loaded persisted cache entry (%d seconds old)',
            int(wall_clock() - stored_at),
        )

    def _persist_entry(self, stored_at: float, data: Any) -> None:
        """Atomically replace the restart entry with owner-only permissions."""
        persistence_path, _ = self._persistence
        if persistence_path is None:
            return

        persistence_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary_path = persistence_path.with_name(persistence_path.name + '.tmp')
        document = {
            'stored_at': stored_at,
            'data': data,
        }

        descriptor = None
        try:
            descriptor = os.open(
                temporary_path,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                descriptor = None
                json.dump(document, handle, separators=(',', ':'))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, persistence_path)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

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

            _, wall_clock = self._persistence
            age = wall_clock() - self.entry_key
            if self._entry_loaded_from_disk and (
                    age < 0 or age >= self.ttl_seconds):
                self.cache_store.pop(self.entry_key, None)
                self.entry_key = None
                self._entry_loaded_from_disk = False
                raise CacheMissError('Cache entry has expired')

            # TTLCache automatically removes expired entries
            if self.entry_key not in self.cache_store:
                logger.error(
                    'Cache miss: entry_key %s not found (expired or evicted)',
                    self.entry_key
                )
                raise CacheMissError(
                    f'Cache entry for key {self.entry_key} not found (expired or evicted by TTLCache)'
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
            _, wall_clock = self._persistence
            timestamp = wall_clock()

            # Store the new entry (TTLCache handles size management automatically)
            self.cache_store[timestamp] = data

            # Update entry_key to point to the new entry (blocking operation for other threads)
            self.entry_key = timestamp
            self._entry_loaded_from_disk = False

            try:
                self._persist_entry(timestamp, data)
            except (OSError, TypeError, ValueError):
                logger.warning('Could not persist cache entry')

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
            self._entry_loaded_from_disk = False

            persistence_path, _ = self._persistence
            if persistence_path is not None:
                try:
                    persistence_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    logger.warning('Could not remove persisted cache entry')

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
                _, wall_clock = self._persistence
                info['cache_age_seconds'] = int(wall_clock() - self.entry_key)

            return info
