"""Protocol for source resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from amplifier_foundation.paths.resolution import ParsedURI
from amplifier_foundation.paths.resolution import ResolvedSource


@dataclass
class SourceStatus:
    """Status of a source for update detection.

    Provides information about the cached state of a source and whether
    updates are available from the remote.
    """

    source_uri: str
    """Original source URI (e.g., git+https://github.com/org/repo@main)."""

    is_cached: bool
    """Whether this source is currently cached locally."""

    cached_at: datetime | None = None
    """When the source was cached (if cached)."""

    cached_ref: str | None = None
    """The ref that was cached (e.g., 'main', 'v1.0.0')."""

    cached_commit: str | None = None
    """The commit SHA that was cached (for git sources)."""

    remote_ref: str | None = None
    """The remote ref that was checked."""

    remote_commit: str | None = None
    """The current remote commit SHA (if checked)."""

    has_update: bool | None = None
    """Whether an update is available. None if unknown/unsupported."""

    error: str | None = None
    """Error message if status check failed."""

    summary: str = ""
    """Human-readable summary of the status."""

    @property
    def is_pinned(self) -> bool:
        """Check if this source is pinned to a specific commit.

        Pinned sources (specific commit SHA or tag) cannot have updates.
        """
        if not self.cached_ref:
            return False
        # If ref looks like a full commit SHA, it's pinned
        if len(self.cached_ref) == 40 and all(c in "0123456789abcdef" for c in self.cached_ref.lower()):
            return True
        # If ref starts with 'v' and contains numbers, likely a version tag
        return self.cached_ref.startswith("v") and any(c.isdigit() for c in self.cached_ref)


class SourceResolverProtocol(Protocol):
    """Protocol for resolving source URIs to local paths.

    Foundation provides SimpleSourceResolver with basic handlers.
    Apps may extend with additional source types or caching strategies.
    """

    async def resolve(self, uri: str) -> ResolvedSource:
        """Resolve a URI to local paths.

        For remote sources (git, http), downloads to cache and returns
        the local cache path. Returns both the active path (what was
        requested, possibly a subdirectory) and the source root (the
        full clone/extract root).

        Args:
            uri: URI string (git+https://..., file://..., /path, ./path, name).

        Returns:
            ResolvedSource with active_path and source_root.

        Raises:
            BundleNotFoundError: If source cannot be resolved.
        """
        ...


class SourceHandlerProtocol(Protocol):
    """Protocol for handling a specific source type."""

    def can_handle(self, parsed: ParsedURI) -> bool:
        """Check if this handler can handle the given URI.

        Args:
            parsed: Parsed URI components.

        Returns:
            True if this handler can resolve this URI type.
        """
        ...

    async def resolve(self, parsed: ParsedURI, cache_dir: Path) -> ResolvedSource:
        """Resolve the URI to local paths.

        Args:
            parsed: Parsed URI components.
            cache_dir: Directory for caching downloaded content.

        Returns:
            ResolvedSource with active_path and source_root.

        Raises:
            BundleNotFoundError: If source cannot be resolved.
        """
        ...


class SourceHandlerWithStatusProtocol(Protocol):
    """Extended protocol for handlers that support status checking.

    This is an optional extension to SourceHandlerProtocol. Handlers
    implementing this protocol can check for updates without downloading.

    Foundation's GitSourceHandler implements this protocol.
    """

    def can_handle(self, parsed: ParsedURI) -> bool:
        """Check if this handler can handle the given URI."""
        ...

    async def resolve(self, parsed: ParsedURI, cache_dir: Path) -> Path:
        """Resolve the URI to a local path."""
        ...

    async def get_status(self, parsed: ParsedURI, cache_dir: Path) -> SourceStatus:
        """Check status of source without downloading.

        Compares cached version (if any) against remote to detect updates.
        This operation should have no side effects.

        For git: Uses `git ls-remote` to check remote HEAD.
        For file: Checks mtime vs cached mtime.
        For http: Uses HEAD request with ETag/Last-Modified.

        Args:
            parsed: Parsed URI components.
            cache_dir: Directory where cached content would be stored.

        Returns:
            SourceStatus with update detection information.
        """
        ...

    async def update(self, parsed: ParsedURI, cache_dir: Path) -> Path:
        """Force re-download of source, ignoring cache.

        Removes any cached version and downloads fresh content.

        Args:
            parsed: Parsed URI components.
            cache_dir: Directory for caching downloaded content.

        Returns:
            Local path to the updated content.

        Raises:
            BundleNotFoundError: If source cannot be resolved.
        """
        ...
