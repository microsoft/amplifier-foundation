"""Disk-based cache implementation for bundles."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from amplifier_foundation.bundle import Bundle


class DiskCache:
    """Disk-based cache for bundles.

    Persists bundle metadata to filesystem for cross-session caching.
    Apps MUST provide cache_dir - foundation doesn't decide where to cache.

    Simple JSON serialization of bundle data. No TTL or eviction - apps
    can clear cache directory or implement their own eviction policy.
    """

    def __init__(self, cache_dir: Path) -> None:
        """Initialize disk cache.

        Args:
            cache_dir: Directory for storing cached bundles.
                       Apps decide this location (e.g., ~/.myapp/cache/bundles/).
        """
        self.cache_dir = cache_dir
        self._ensure_cache_dir()

    def _ensure_cache_dir(self) -> None:
        """Ensure cache directory exists."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key_to_path(self, key: str) -> Path:
        """Convert cache key to filesystem path.

        Args:
            key: Cache key (URI or bundle name).

        Returns:
            Path to cache file.
        """
        # Hash the key to create safe filename
        key_hash = hashlib.sha256(key.encode()).hexdigest()[:16]
        # Keep first part of key for debugging
        safe_prefix = "".join(c if c.isalnum() or c in "-_" else "_" for c in key[:30])
        return self.cache_dir / f"{safe_prefix}-{key_hash}.json"

    def get(self, key: str) -> Bundle | None:
        """Get a cached bundle.

        Args:
            key: Cache key.

        Returns:
            Cached Bundle, or None if not cached or invalid.
        """
        cache_path = self._cache_key_to_path(key)
        if not cache_path.exists():
            return None

        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            # Import here to avoid circular import
            from amplifier_foundation.bundle import Bundle

            bundle = Bundle.from_dict(data)
            # Restore instruction which from_dict doesn't set
            if "instruction" in data:
                bundle.instruction = data["instruction"]
            return bundle
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
            # Invalid cache entry - remove it
            cache_path.unlink(missing_ok=True)
            return None

    def set(self, key: str, bundle: Bundle) -> None:
        """Cache a bundle.

        Args:
            key: Cache key.
            bundle: Bundle to cache.
        """
        self._ensure_cache_dir()
        cache_path = self._cache_key_to_path(key)

        # Serialize bundle to dict in Bundle.from_dict format
        # Context paths need to be converted to strings for JSON
        context_dict = {name: str(path) for name, path in bundle.context.items()}

        data = {
            "bundle": {
                "name": bundle.name,
                "version": bundle.version,
                "description": bundle.description,
            },
            "includes": bundle.includes,
            "session": bundle.session,
            "providers": bundle.providers,
            "tools": bundle.tools,
            "hooks": bundle.hooks,
            "agents": bundle.agents,
            "context": context_dict,
            "instruction": bundle.instruction,
        }

        cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def clear(self) -> None:
        """Clear all cached bundles."""
        if self.cache_dir.exists():
            for cache_file in self.cache_dir.glob("*.json"):
                cache_file.unlink(missing_ok=True)

    def __contains__(self, key: str) -> bool:
        """Check if key is cached."""
        return self._cache_key_to_path(key).exists()
