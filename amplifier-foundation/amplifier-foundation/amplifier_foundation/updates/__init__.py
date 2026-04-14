"""Bundle update utilities.

This module provides mechanisms for checking bundle update status and updating
cached sources. Following the kernel philosophy, these are MECHANISMS that apps
can use - the app decides WHEN and HOW to apply updates.

Example usage:

    from amplifier_foundation import load_bundle
    from amplifier_foundation.updates import check_bundle_status, update_bundle

    # Load a bundle
    bundle = await load_bundle("git+https://github.com/org/my-bundle@main")

    # Check for updates (no side effects)
    status = await check_bundle_status(bundle)
    print(f"Has updates: {status.has_updates}")
    for source in status.sources:
        print(f"  {source.summary}")

    # Update if updates available (side effects - re-downloads and reinstalls deps)
    if status.has_updates:
        updated_bundle = await update_bundle(bundle)
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TYPE_CHECKING

from amplifier_foundation.paths.resolution import ParsedURI
from amplifier_foundation.paths.resolution import get_amplifier_home
from amplifier_foundation.paths.resolution import parse_uri
from amplifier_foundation.sources.git import GitSourceHandler
from amplifier_foundation.sources.protocol import SourceStatus

if TYPE_CHECKING:
    from amplifier_foundation.bundle import Bundle


@dataclass
class BundleStatus:
    """Status of a bundle and all its sources.

    Provides aggregate information about update availability across
    all sources in a bundle (modules, included bundles, etc.).
    """

    bundle_name: str
    """Name of the bundle."""

    bundle_source: str | None
    """Source URI of the bundle itself, if loaded from remote."""

    sources: list[SourceStatus] = field(default_factory=list)
    """Status of each source in the bundle."""

    @property
    def has_updates(self) -> bool:
        """Check if any source has an update available."""
        return any(s.has_update is True for s in self.sources)

    @property
    def updateable_sources(self) -> list[SourceStatus]:
        """Get list of sources that have updates available."""
        return [s for s in self.sources if s.has_update is True]

    @property
    def up_to_date_sources(self) -> list[SourceStatus]:
        """Get list of sources that are up to date."""
        return [s for s in self.sources if s.has_update is False]

    @property
    def unknown_sources(self) -> list[SourceStatus]:
        """Get list of sources with unknown update status."""
        return [s for s in self.sources if s.has_update is None]

    @property
    def summary(self) -> str:
        """Human-readable summary of bundle status."""
        total = len(self.sources)
        updates = len(self.updateable_sources)
        up_to_date = len(self.up_to_date_sources)
        unknown = len(self.unknown_sources)

        if updates > 0:
            return f"{updates} update(s) available ({up_to_date} up to date, {unknown} unknown)"
        if unknown > 0:
            return f"Up to date ({unknown} source(s) could not be checked)"
        return f"All {total} source(s) up to date"


def _get_cache_dir() -> Path:
    """Get the default cache directory for modules."""
    return get_amplifier_home() / "cache"


def _collect_source_uris(bundle: Bundle) -> list[str]:
    """Collect all source URIs from a bundle.

    Extracts sources from:
    - Bundle's own source (if loaded from remote)
    - Session orchestrator and context
    - Providers, tools, hooks
    - Included bundle URIs

    Args:
        bundle: Bundle to collect sources from.

    Returns:
        List of unique source URIs.
    """
    sources: set[str] = set()

    # Bundle's own source (stored in _source_uri if loaded via load_bundle)
    bundle_source_uri = getattr(bundle, "_source_uri", None)
    if bundle_source_uri:
        sources.add(bundle_source_uri)

    # Session config
    session = bundle.session or {}
    if isinstance(session.get("orchestrator"), dict) and "source" in session["orchestrator"]:
        sources.add(session["orchestrator"]["source"])
    if isinstance(session.get("context"), dict) and "source" in session["context"]:
        sources.add(session["context"]["source"])

    # Module lists
    for module_list in [bundle.providers, bundle.tools, bundle.hooks]:
        for mod in module_list:
            if isinstance(mod, dict) and "source" in mod:
                sources.add(mod["source"])

    # Note: Included bundles are now registered as first-class bundles
    # and will be checked independently by _check_all_bundle_status().
    # No need to collect their URIs here.

    return list(sources)


async def check_bundle_status(
    bundle: Bundle,
    cache_dir: Path | None = None,
) -> BundleStatus:
    """Check update status of all sources in a bundle.

    This is a MECHANISM that has no side effects - it only checks
    whether updates are available without downloading anything.

    For git sources, uses `git ls-remote` to compare cached commits
    against remote HEAD.

    Args:
        bundle: Bundle to check.
        cache_dir: Cache directory for modules. Defaults to ~/.amplifier/cache.

    Returns:
        BundleStatus with status of each source.

    Example:
        status = await check_bundle_status(bundle)
        if status.has_updates:
            print(f"Updates available: {status.updateable_sources}")
    """
    if cache_dir is None:
        cache_dir = _get_cache_dir()

    # Collect all source URIs
    source_uris = _collect_source_uris(bundle)

    # Check status of each source
    git_handler = GitSourceHandler()
    statuses: list[SourceStatus] = []

    for uri in source_uris:
        parsed = parse_uri(uri)

        if git_handler.can_handle(parsed):
            status = await git_handler.get_status(parsed, cache_dir)
            statuses.append(status)
        else:
            # For non-git sources, report as unknown
            statuses.append(
                SourceStatus(
                    source_uri=uri,
                    is_cached=True,  # Assume cached since bundle loaded
                    has_update=None,
                    summary="Update checking not supported for this source type",
                )
            )

    # Get bundle source for display
    bundle_source = getattr(bundle, "_source_uri", None)

    return BundleStatus(
        bundle_name=bundle.name or "unnamed",
        bundle_source=bundle_source,
        sources=statuses,
    )


async def update_bundle(
    bundle: Bundle,
    cache_dir: Path | None = None,
    selective: list[str] | None = None,
    install_deps: bool = True,
) -> Bundle:
    """Update bundle sources by re-downloading from remote and reinstalling dependencies.

    This is a MECHANISM that has side effects - it removes cached
    versions, re-downloads fresh content, and reinstalls dependencies.

    Args:
        bundle: Bundle to update.
        cache_dir: Cache directory for modules. Defaults to ~/.amplifier/cache.
        selective: If provided, only update these source URIs.
            If None, updates all sources with available updates.
        install_deps: If True (default), reinstall dependencies after updating.
            This ensures new dependencies added to pyproject.toml are installed.

    Returns:
        The same bundle (sources are updated in cache, bundle config unchanged).

    Example:
        # Update all sources with updates
        await update_bundle(bundle)

        # Update specific sources
        await update_bundle(bundle, selective=["git+https://github.com/org/module@main"])
    """
    if cache_dir is None:
        cache_dir = _get_cache_dir()

    # Get current status to know what to update
    status = await check_bundle_status(bundle, cache_dir)

    # Determine which sources to update
    if selective is not None:
        sources_to_update = selective
    else:
        # Update all sources with available updates
        sources_to_update = [s.source_uri for s in status.updateable_sources]

    # Update each source
    git_handler = GitSourceHandler()
    updated_paths: list[Path] = []

    for uri in sources_to_update:
        parsed = parse_uri(uri)

        if git_handler.can_handle(parsed):
            resolved = await git_handler.update(parsed, cache_dir)
            updated_paths.append(resolved.active_path)
        # Non-git sources: no-op for now (could add support later)

    # Reinstall dependencies for updated modules
    if install_deps and updated_paths:
        from amplifier_foundation.modules.activator import ModuleActivator

        activator = ModuleActivator(cache_dir=cache_dir)
        for module_path in updated_paths:
            # Only reinstall if it's a Python module (has pyproject.toml)
            if (module_path / "pyproject.toml").exists():
                await activator._install_dependencies(module_path)

    return bundle


__all__ = [
    "BundleStatus",
    "SourceStatus",
    "check_bundle_status",
    "update_bundle",
]
