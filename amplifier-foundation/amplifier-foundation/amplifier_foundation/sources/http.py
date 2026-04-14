"""HTTP source handler for direct https:// and http:// downloads."""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.request import urlopen

from amplifier_foundation.exceptions import BundleNotFoundError
from amplifier_foundation.paths.resolution import ParsedURI
from amplifier_foundation.paths.resolution import ResolvedSource


class HttpSourceHandler:
    """Handler for https:// and http:// URIs (direct file downloads).

    Downloads files to cache and returns local path.
    Uses content-addressable storage (hash of URL).

    Note: For downloading zip archives, use zip+https:// which extracts.
    This handler downloads files as-is without extraction.
    """

    def can_handle(self, parsed: ParsedURI) -> bool:
        """Check if this handler can handle the given URI."""
        return parsed.is_http

    async def resolve(self, parsed: ParsedURI, cache_dir: Path) -> ResolvedSource:
        """Resolve HTTP URI to local cached path.

        Args:
            parsed: Parsed URI components.
            cache_dir: Directory for caching downloaded content.

        Returns:
            ResolvedSource with active_path and source_root.

        Raises:
            BundleNotFoundError: If download fails.
        """
        # Build the full URL
        url = f"{parsed.scheme}://{parsed.host}{parsed.path}"

        # Create cache key from URL
        cache_key = hashlib.sha256(url.encode()).hexdigest()[:16]

        # Preserve file extension for proper handling
        filename = Path(parsed.path).name or "download"
        cached_file = cache_dir / f"{filename}-{cache_key}"

        # Check if already cached
        if cached_file.exists():
            result_path = cached_file
            # If subpath specified, treat cached file as directory
            if parsed.subpath:
                result_path = cached_file / parsed.subpath
            if result_path.exists():
                return ResolvedSource(active_path=result_path, source_root=cached_file)

        # Ensure cache directory exists
        cache_dir.mkdir(parents=True, exist_ok=True)

        try:
            with urlopen(url, timeout=60) as response:  # noqa: S310
                content = response.read()

            cached_file.write_bytes(content)
        except Exception as e:
            raise BundleNotFoundError(f"Failed to download {url}: {e}") from e

        # Apply subpath if specified (for downloaded directories/archives)
        result_path = cached_file
        if parsed.subpath:
            result_path = cached_file / parsed.subpath
            if not result_path.exists():
                raise BundleNotFoundError(f"Subpath not found: {parsed.subpath}")

        return ResolvedSource(active_path=result_path, source_root=cached_file)
