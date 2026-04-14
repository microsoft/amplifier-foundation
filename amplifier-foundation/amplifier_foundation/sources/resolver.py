"""Simple source resolver implementation."""

from __future__ import annotations

from pathlib import Path

from amplifier_foundation.exceptions import BundleNotFoundError
from amplifier_foundation.paths.resolution import ResolvedSource
from amplifier_foundation.paths.resolution import get_amplifier_home
from amplifier_foundation.paths.resolution import parse_uri

from .file import FileSourceHandler
from .git import GitSourceHandler
from .http import HttpSourceHandler
from .protocol import SourceHandlerProtocol
from .zip import ZipSourceHandler


class SimpleSourceResolver:
    """Simple implementation of SourceResolverProtocol.

    Supports:
    - file:// and local paths via FileSourceHandler
    - git+https:// via GitSourceHandler
    - https:// and http:// via HttpSourceHandler
    - zip+https:// and zip+file:// via ZipSourceHandler

    Apps can extend by adding custom handlers.
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        base_path: Path | None = None,
    ) -> None:
        """Initialize resolver.

        Args:
            cache_dir: Directory for caching remote content.
            base_path: Base path for resolving relative paths.
        """
        self.cache_dir = cache_dir or get_amplifier_home() / "cache" / "bundles"
        self.base_path = base_path or Path.cwd()

        # Default handlers - order matters for URI matching
        self._handlers: list[SourceHandlerProtocol] = [
            FileSourceHandler(base_path=self.base_path),
            GitSourceHandler(),
            ZipSourceHandler(),  # Must be before HttpSourceHandler (zip+https matches before https)
            HttpSourceHandler(),
        ]

    def add_handler(self, handler: SourceHandlerProtocol) -> None:
        """Add a custom source handler.

        Handlers are tried in order, first match wins.

        Args:
            handler: Handler to add.
        """
        self._handlers.insert(0, handler)  # Custom handlers take priority

    async def resolve(self, uri: str) -> ResolvedSource:
        """Resolve a URI to local paths.

        Args:
            uri: URI string.

        Returns:
            ResolvedSource with active_path and source_root.

        Raises:
            BundleNotFoundError: If no handler can resolve the URI.
        """
        parsed = parse_uri(uri)

        for handler in self._handlers:
            if handler.can_handle(parsed):
                return await handler.resolve(parsed, self.cache_dir)

        raise BundleNotFoundError(f"No handler for URI: {uri}")
