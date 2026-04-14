"""Protocol for bundle caching."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Protocol

if TYPE_CHECKING:
    from amplifier_foundation.bundle import Bundle


class CacheProviderProtocol(Protocol):
    """Protocol for caching loaded bundles.

    Foundation provides SimpleCache with in-memory caching.
    Apps may extend with disk caching, TTL, etc.
    """

    def get(self, key: str) -> Bundle | None:
        """Get a cached bundle.

        Args:
            key: Cache key (usually the bundle URI or name).

        Returns:
            Cached Bundle, or None if not cached.
        """
        ...

    def set(self, key: str, bundle: Bundle) -> None:
        """Cache a bundle.

        Args:
            key: Cache key.
            bundle: Bundle to cache.
        """
        ...

    def clear(self) -> None:
        """Clear all cached bundles."""
        ...
