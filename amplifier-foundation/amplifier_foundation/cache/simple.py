"""Simple in-memory cache implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from amplifier_foundation.bundle import Bundle


class SimpleCache:
    """Simple in-memory cache for bundles.

    No TTL or eviction policy - bundles cached until clear() is called
    or process ends. Suitable for CLI tools and short-lived sessions.

    Apps needing persistent caching or TTL should implement CacheProviderProtocol.
    """

    def __init__(self) -> None:
        """Initialize empty cache."""
        self._cache: dict[str, Bundle] = {}

    def get(self, key: str) -> Bundle | None:
        """Get a cached bundle.

        Args:
            key: Cache key.

        Returns:
            Cached Bundle, or None if not cached.
        """
        return self._cache.get(key)

    def set(self, key: str, bundle: Bundle) -> None:
        """Cache a bundle.

        Args:
            key: Cache key.
            bundle: Bundle to cache.
        """
        self._cache[key] = bundle

    def clear(self) -> None:
        """Clear all cached bundles."""
        self._cache.clear()

    def __contains__(self, key: str) -> bool:
        """Check if key is cached."""
        return key in self._cache
