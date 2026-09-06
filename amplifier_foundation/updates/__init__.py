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

import logging
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
    from amplifier_foundation.registry import BundleRegistry

logger = logging.getLogger(__name__)

MAX_INCLUDE_DEPTH = 16
"""Belt-and-braces bound on include-graph depth.

Cycles are already terminated by the walk's ``visited`` set; this only caps a
pathological (non-cyclic) chain so a malformed graph cannot spin forever.
"""


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
    if (
        isinstance(session.get("orchestrator"), dict)
        and "source" in session["orchestrator"]
    ):
        sources.add(session["orchestrator"]["source"])
    if isinstance(session.get("context"), dict) and "source" in session["context"]:
        sources.add(session["context"]["source"])

    # Module lists
    for module_list in [bundle.providers, bundle.tools, bundle.hooks]:
        for mod in module_list:
            if isinstance(mod, dict) and "source" in mod:
                sources.add(mod["source"])

    # Included bundles are deliberately NOT collected here -- this function
    # reports only what the bundle declares *directly*.  Transitive includes
    # are walked separately by collect_transitive_source_uris(), which needs a
    # registry (for include resolution) and the cache dir (to read already-
    # cached bundles), neither of which belongs in this signature.
    #
    # An earlier comment here claimed includes "are registered as first-class
    # bundles and will be checked independently".  That premise is false in the
    # case that matters: a `#subdirectory=` include registers with
    # is_root=False (registry.py), and host enumeration that keeps only root
    # entries therefore never checks it.  A repo whose only registry presence
    # is a non-root sub-bundle entry was checked by nobody, and this function's
    # caller reported it green while the cache lagged upstream forever --
    # GitSourceHandler.resolve() returns an existing cache verbatim, with no
    # fetch, so a source the update path does not enumerate never moves.

    return list(sources)


def _strip_uri_fragment(uri: str) -> str:
    """Drop a URI's ``#fragment``, keeping any ``@ref`` intact.

    The ``#subdirectory=`` fragment names a file *inside* a repo; the repo at a
    ref is the update target.  ``@main`` and ``@v2`` of the same repo are two
    different targets, so the ref must survive.
    """
    return uri.split("#", 1)[0]


def _cached_path_for(uri: str, cache_dir: Path) -> Path | None:
    """Best-available on-disk path for *uri*, WITHOUT touching the network.

    Returns ``None`` when the source is not a readable local/cached thing --
    which is the correct answer for a source whose cache has been deleted.
    Resolving it would clone, and a clone here would make a missing cache look
    healthy, defeating the no-side-effects contract of check_bundle_status().
    """
    try:
        parsed = parse_uri(uri)
    except Exception:  # noqa: BLE001 - an unparseable URI is simply not walkable
        return None

    git_handler = GitSourceHandler()
    if git_handler.can_handle(parsed):
        # _get_cache_path is a pure key derivation -- it never downloads.
        cache_path = git_handler._get_cache_path(parsed, cache_dir)
        if not cache_path.exists():
            return None
        active = cache_path / parsed.subpath if parsed.subpath else cache_path
        return active if active.exists() else None

    if parsed.is_file:
        path = Path(parsed.path)
        return path if path.exists() else None

    return None


def _is_git_uri(uri: str) -> bool:
    return uri.startswith("git+") or uri.startswith("git://")


async def collect_transitive_source_uris(
    bundle: Bundle,
    *,
    registry: BundleRegistry,
    cache_dir: Path,
    known_uris: set[str] | None = None,
    max_depth: int = MAX_INCLUDE_DEPTH,
) -> dict[str, str]:
    """Walk ``includes:`` from *bundle* and return the git sources found.

    Cycle-safe: a ``visited`` set keyed on the full include URI terminates
    B -> C -> B.  Read-only: caches that already exist are read via the git
    handler's own ``_get_cache_path``; a missing cache is skipped rather than
    resolved, because resolving downloads.

    Resolution is *reused*, never reimplemented -- ``BundleRegistry``'s own
    ``_parse_include`` / ``_resolve_include_source`` / ``_load_from_path`` do
    the work, so ``@namespace:path``, ``git+...`` and ``#subdirectory=`` forms
    resolve exactly the way a real session resolves them.

    The walk seeds from the bundle's ``_source_uri`` and re-reads that bundle
    file from disk, rather than trusting the in-memory ``bundle.includes``.
    That is deliberate: ``Bundle.compose()`` keeps only ``self.includes``, so
    after ``load_bundle()`` composes a bundle's includes the returned object's
    ``.includes`` is the *first included bundle's* list, not its own.  Walking
    the in-memory list would therefore walk the wrong subtree.  When no source
    URI is available (a hand-constructed, never-loaded Bundle),
    ``bundle.includes`` is the only handle there is, and is used as the seed.

    Args:
        bundle: Bundle whose include graph to walk.
        registry: Registry supplying include parsing/resolution and the bundle
            file parser.  Namespace includes resolve against its state, so pass
            the same registry that loaded the bundle where possible.
        cache_dir: Cache root (``<amplifier home>/cache``).
        known_uris: Fragment-stripped URIs already covered by a direct source.
            Anything listed here is skipped -- it is not "transitive", it is a
            source that was already going to be checked.
        max_depth: Bound on graph depth (see ``MAX_INCLUDE_DEPTH``).

    Returns:
        Mapping of fragment-stripped git URI -> display name of the bundle
        whose ``includes:`` reaches it, in discovery order.
    """
    known = {_strip_uri_fragment(u) for u in (known_uris or set())}
    found: dict[str, str] = {}

    # Visited on the FULL uri (fragment included): two sub-bundles in one repo
    # are different files with different includes, so collapsing them on the
    # repo URI would skip half the graph.  This set is also what makes a cycle
    # (B includes C includes B) terminate.
    visited: set[str] = set()

    # (parent display name, uri, depth)
    queue: list[tuple[str, str, int]] = []
    root_label = bundle.name or "bundle"

    source_uri = getattr(bundle, "_source_uri", None)
    if source_uri:
        visited.add(source_uri)
        queue.append((root_label, source_uri, 0))
    else:
        # No source URI: the in-memory includes list is all we have.
        for raw_include in bundle.includes or []:
            spec = registry._parse_include(raw_include)
            resolved = _resolve_include(registry, spec) if spec else None
            if not resolved or resolved in visited:
                continue
            visited.add(resolved)
            if _is_git_uri(resolved):
                key = _strip_uri_fragment(resolved)
                if key not in known and key not in found:
                    found[key] = root_label
            queue.append((root_label, resolved, 1))

    while queue:
        label, uri, depth = queue.pop(0)
        if depth >= max_depth:
            logger.debug(f"Include walk hit max depth at: {uri}")
            continue

        path = _cached_path_for(uri, cache_dir)
        if path is None:
            continue

        try:
            included = await registry._load_from_path(path)
        except Exception as exc:  # noqa: BLE001 - an unreadable bundle is not fatal
            logger.debug(f"Could not read includes from {uri}: {exc}")
            continue

        parent_label = included.name or label

        for raw_include in included.includes or []:
            spec = registry._parse_include(raw_include)
            if not spec:
                continue
            resolved = _resolve_include(registry, spec)
            if not resolved or resolved in visited:
                continue
            visited.add(resolved)

            if _is_git_uri(resolved):
                key = _strip_uri_fragment(resolved)
                if key not in known and key not in found:
                    found[key] = parent_label

            queue.append((parent_label, resolved, depth + 1))

    return found


def _resolve_include(registry: BundleRegistry, spec: str) -> str | None:
    """Resolve one include spec to a loadable URI, or None."""
    try:
        resolved = registry._resolve_include_source(spec)
    except Exception as exc:  # noqa: BLE001 - one bad include must not sink the walk
        logger.debug(f"Could not resolve include '{spec}': {exc}")
        return None

    if not resolved:
        return None

    # A plain name is a registry alias; the loader lets _load_single look it
    # up, so look it up the same way here.
    if "://" not in resolved and not resolved.startswith("git+"):
        return registry.find(resolved)

    return resolved


async def _check_transitive_sources(
    bundle: Bundle,
    *,
    cache_dir: Path,
    registry: BundleRegistry | None,
    known_uris: set[str],
    git_handler: GitSourceHandler,
) -> list[SourceStatus]:
    """Status-check every git source reached only through ``includes:``.

    Uses the same ``GitSourceHandler.get_status`` call a direct source gets, so
    a transitive row carries the same cached/remote commit truth -- and the
    same pinned handling -- as a direct one.  One bad source is logged and
    skipped rather than sinking the whole report.
    """
    if registry is None:
        # Imported lazily: registry.py is a heavier import and pulling it at
        # module scope would create an import cycle through amplifier_foundation.
        from amplifier_foundation.registry import BundleRegistry as _BundleRegistry

        try:
            registry = _BundleRegistry(home=cache_dir.parent)
        except Exception as exc:  # noqa: BLE001 - no registry means no walk, not a crash
            logger.debug(f"Could not build a registry for the include walk: {exc}")
            return []

    try:
        transitive = await collect_transitive_source_uris(
            bundle,
            registry=registry,
            cache_dir=cache_dir,
            known_uris=known_uris,
        )
    except Exception as exc:  # noqa: BLE001 - a walk failure must not sink the report
        logger.debug(f"Include walk failed: {exc}")
        return []

    statuses: list[SourceStatus] = []
    for uri, parent in transitive.items():
        try:
            parsed = parse_uri(uri)
            if not git_handler.can_handle(parsed):
                continue
            status = await git_handler.get_status(parsed, cache_dir)
        except Exception as exc:  # noqa: BLE001 - one bad source, not the report
            logger.debug(f"Status check failed for {uri}: {exc}")
            continue
        status.via = parent
        statuses.append(status)

    return statuses


async def check_bundle_status(
    bundle: Bundle,
    cache_dir: Path | None = None,
    *,
    registry: BundleRegistry | None = None,
    include_transitive: bool = True,
) -> BundleStatus:
    """Check update status of all sources in a bundle.

    This is a MECHANISM that has no side effects - it only checks
    whether updates are available without downloading anything.

    For git sources, uses `git ls-remote` to compare cached commits
    against remote HEAD.

    Transitively-included bundles are checked too (``include_transitive``,
    default True).  They must be: an include reached only by
    ``#subdirectory=`` registers with ``is_root=False``, and host enumeration
    that keeps only root entries never checks it -- so before this, a bundle
    whose only registry presence was a non-root sub-bundle entry was checked
    by nobody, and this function reported "All N source(s) up to date" over a
    cache stuck at an old commit.  Each such source is reported with ``via``
    set to the bundle that includes it.

    Args:
        bundle: Bundle to check.
        cache_dir: Cache directory for modules. Defaults to ~/.amplifier/cache.
        registry: Registry used to resolve includes during the transitive
            walk. Defaults to a registry rooted at ``cache_dir.parent`` (the
            conventional ``<amplifier home>/cache`` layout). Pass the registry
            that loaded the bundle when using a non-conventional cache dir.
        include_transitive: Walk ``includes:`` and report transitively-reached
            git sources. Set False for the old direct-sources-only behavior.

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

    if include_transitive:
        statuses.extend(
            await _check_transitive_sources(
                bundle,
                cache_dir=cache_dir,
                registry=registry,
                known_uris=set(source_uris),
                git_handler=git_handler,
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
    "MAX_INCLUDE_DEPTH",
    "BundleStatus",
    "SourceStatus",
    "check_bundle_status",
    "collect_transitive_source_uris",
    "update_bundle",
]
