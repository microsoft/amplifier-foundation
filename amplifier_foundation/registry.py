"""Bundle registry - central bundle management for Amplifier.

Handles registration, loading, caching, and update checking.
Uses AMPLIFIER_HOME env var or defaults to ~/.amplifier.

Structure under home:
    home/
    ├── registry.json   # Persisted state
    └── cache/          # Cached remote bundles

Sub-assemblies available for advanced users:
    - SimpleSourceResolver: URI → local path resolution
    - BundleLoader (internal): Parse bundle files → Bundle

Per IMPLEMENTATION_PHILOSOPHY: Single class replaces SimpleBundleDiscovery + BundleResolver.
Per MODULAR_DESIGN_PHILOSOPHY: Sub-assemblies remain accessible for custom composition.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from amplifier_foundation.bundle import Bundle
from amplifier_foundation.exceptions import BundleDependencyError
from amplifier_foundation.exceptions import BundleLoadError
from amplifier_foundation.exceptions import BundleNotFoundError
from amplifier_foundation.io.frontmatter import parse_frontmatter
from amplifier_foundation.io.yaml import read_yaml
from amplifier_foundation.paths.resolution import get_amplifier_home
from amplifier_foundation.sources.resolver import SimpleSourceResolver

if TYPE_CHECKING:
    from amplifier_foundation.bundle import Bundle as BundleType  # noqa: F401

logger = logging.getLogger(__name__)


@dataclass
class BundleState:
    """Tracked state for a registered bundle."""

    uri: str
    name: str
    version: str | None = None
    loaded_at: datetime | None = None
    checked_at: datetime | None = None
    local_path: str | None = None  # Stored as string for JSON serialization

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "uri": self.uri,
            "name": self.name,
            "version": self.version,
            "loaded_at": self.loaded_at.isoformat() if self.loaded_at else None,
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
            "local_path": self.local_path,
        }

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> BundleState:
        """Create from JSON dict."""
        return cls(
            uri=data["uri"],
            name=name,
            version=data.get("version"),
            loaded_at=datetime.fromisoformat(data["loaded_at"]) if data.get("loaded_at") else None,
            checked_at=datetime.fromisoformat(data["checked_at"]) if data.get("checked_at") else None,
            local_path=data.get("local_path"),
        )


@dataclass
class UpdateInfo:
    """Information about an available update."""

    name: str
    current_version: str | None
    available_version: str
    uri: str


class BundleRegistry:
    """Central bundle management for the Amplifier ecosystem.

    Handles registration, loading, caching, and update checking.
    Uses AMPLIFIER_HOME env var or defaults to ~/.amplifier.

    Example:
        registry = BundleRegistry()
        registry.register({"foundation": "git+https://github.com/microsoft/amplifier-foundation@main"})
        bundle = await registry.load("foundation")
    """

    def __init__(self, home: Path | None = None) -> None:
        """Initialize registry.

        Args:
            home: Base directory. Resolves in order:
                  1. Explicit parameter
                  2. AMPLIFIER_HOME env var
                  3. ~/.amplifier (default)
        """
        self._home = self._resolve_home(home)
        self._registry: dict[str, BundleState] = {}
        self._source_resolver = SimpleSourceResolver(
            cache_dir=self._home / "cache",
            base_path=Path.cwd(),
        )
        self._loading: set[str] = set()  # Cycle detection for includes
        self._load_persisted_state()

    @property
    def home(self) -> Path:
        """Base directory for all registry data."""
        return self._home

    def _resolve_home(self, home: Path | None) -> Path:
        """Resolve home directory from args or use default."""
        if home is not None:
            return home.expanduser().resolve()
        return get_amplifier_home()

    # =========================================================================
    # Discovery Methods
    # =========================================================================

    def register(self, bundles: dict[str, str]) -> None:
        """Register name → URI mappings.

        Always accepts a dict. For single entry: {"name": "uri"}
        Overwrites existing registrations for same names.
        Does NOT persist automatically - call save() to persist.

        Args:
            bundles: Dict of name → URI pairs.
                     e.g. {"foundation": "git+https://..."}
        """
        for name, uri in bundles.items():
            existing = self._registry.get(name)
            if existing:
                # Preserve existing state, update URI
                existing.uri = uri
            else:
                self._registry[name] = BundleState(uri=uri, name=name)
            logger.debug(f"Registered bundle: {name} → {uri}")

    def find(self, name: str) -> str | None:
        """Look up URI for registered name.

        Args:
            name: Bundle name.

        Returns:
            URI string or None if not registered.
        """
        state = self._registry.get(name)
        return state.uri if state else None

    def list_registered(self) -> list[str]:
        """List all registered bundle names.

        Returns:
            Sorted list of registered names.
        """
        return sorted(self._registry.keys())

    # =========================================================================
    # Loading Methods
    # =========================================================================

    async def load(
        self,
        name_or_uri: str | None = None,
        *,
        auto_register: bool = True,
    ) -> Bundle | dict[str, Bundle]:
        """Load bundle(s).

        Args:
            name_or_uri: Name (from registry), URI (direct), or None (all registered).
            auto_register: If True, URI loads register using extracted name.

        Returns:
            - name/URI provided: Single Bundle
            - None: Dict of name → Bundle for all registered
        """
        if name_or_uri is None:
            # Load all registered bundles concurrently
            names = self.list_registered()
            if not names:
                return {}

            results = await asyncio.gather(
                *[self._load_single(name, auto_register=False) for name in names],
                return_exceptions=True,
            )

            bundles = {}
            for name, result in zip(names, results, strict=True):
                if isinstance(result, Exception):
                    logger.warning(f"Failed to load bundle '{name}': {result}")
                else:
                    bundles[name] = result

            return bundles

        return await self._load_single(name_or_uri, auto_register=auto_register)

    async def _load_single(
        self,
        name_or_uri: str,
        *,
        auto_register: bool = True,
        auto_include: bool = True,
        refresh: bool = False,  # noqa: ARG002 - Reserved for future cache bypass
    ) -> Bundle:
        """Load a single bundle by name or URI.

        Args:
            name_or_uri: Bundle name or URI.
            auto_register: Register URI bundles by extracted name.
            auto_include: Load and compose includes.
            refresh: Bypass cache, fetch fresh (reserved for future use).

        Returns:
            Loaded Bundle.

        Raises:
            BundleNotFoundError: Bundle not found.
            BundleLoadError: Failed to load bundle.
        """
        # Determine if this is a registered name or a URI
        registered_name: str | None = None
        uri: str

        if name_or_uri in self._registry:
            registered_name = name_or_uri
            uri = self._registry[name_or_uri].uri
        else:
            uri = name_or_uri

        # Cycle detection
        if uri in self._loading:
            raise BundleDependencyError(f"Circular dependency detected: {uri}")

        self._loading.add(uri)
        try:
            # Resolve URI to local paths (active_path and source_root)
            resolved = await self._source_resolver.resolve(uri)
            if resolved is None:
                raise BundleNotFoundError(f"Could not resolve URI: {uri}")

            local_path = resolved.active_path

            # Load bundle from path
            bundle = await self._load_from_path(local_path)

            # If loading from subdirectory, set up source_base_paths for root access
            if resolved.is_subdirectory:
                root_bundle_path = self._find_nearest_bundle_file(
                    start=resolved.active_path.parent,
                    stop=resolved.source_root,
                )
                if root_bundle_path:
                    # Load root bundle to get its name for namespace registration
                    # This allows @rootname:path references (e.g., @recipes:examples/)
                    root_bundle = await self._load_from_path(root_bundle_path)
                    if root_bundle.name:
                        bundle.source_base_paths[root_bundle.name] = resolved.source_root
                        logger.debug(
                            f"Subdirectory bundle '{bundle.name}' registered root namespace "
                            f"@{root_bundle.name}: -> {resolved.source_root}"
                        )
                    # Also register subdirectory bundle's own name if different
                    if bundle.name and bundle.name != root_bundle.name:
                        bundle.source_base_paths[bundle.name] = resolved.source_root
                        logger.debug(
                            f"Subdirectory bundle also registered own namespace "
                            f"@{bundle.name}: -> {resolved.source_root}"
                        )

            # Register bundle for namespace resolution before processing includes.
            # This is needed even when auto_register=False because the bundle's
            # own includes may reference its namespace (self-referencing includes
            # like "design-intelligence:behaviors/design-intelligence").
            if bundle.name and bundle.name not in self._registry:
                self._registry[bundle.name] = BundleState(
                    uri=uri,
                    name=bundle.name,
                    version=bundle.version,
                    loaded_at=datetime.now(),
                    local_path=str(local_path),
                )
                logger.debug(f"Registered bundle for namespace resolution: {bundle.name}")

            # Update state for known bundle (pre-registered via well-known bundles, etc.)
            # Handle both: loaded by registered name OR loaded by URI but bundle.name matches registry
            update_name = registered_name or (bundle.name if bundle.name in self._registry else None)
            if update_name:
                state = self._registry[update_name]
                state.version = bundle.version
                state.loaded_at = datetime.now()
                state.local_path = str(local_path)

            # Load includes and compose
            if auto_include and bundle.includes:
                bundle = await self._compose_includes(bundle)

            # Store source URI for update checking (used by check_bundle_status)
            # Must be set AFTER composition since compose() returns a new Bundle
            bundle._source_uri = uri  # type: ignore[attr-defined]

            return bundle

        finally:
            self._loading.discard(uri)

    async def _load_from_path(self, path: Path) -> Bundle:
        """Load bundle from local path.

        Args:
            path: Path to bundle file or directory.

        Returns:
            Bundle instance.

        Raises:
            BundleLoadError: Failed to load bundle.
        """
        if path.is_dir():
            bundle_md = path / "bundle.md"
            bundle_yaml = path / "bundle.yaml"

            if bundle_md.exists():
                return await self._load_markdown_bundle(bundle_md)
            if bundle_yaml.exists():
                return await self._load_yaml_bundle(bundle_yaml)
            raise BundleLoadError(f"Not a valid bundle: missing bundle.md or bundle.yaml in {path}")

        if path.suffix == ".md":
            return await self._load_markdown_bundle(path)
        if path.suffix in (".yaml", ".yml"):
            return await self._load_yaml_bundle(path)
        raise BundleLoadError(f"Unknown bundle format: {path}")

    async def _load_markdown_bundle(self, path: Path) -> Bundle:
        """Load bundle from markdown file with frontmatter."""
        content = path.read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter(content)

        bundle = Bundle.from_dict(frontmatter, base_path=path.parent)
        bundle.instruction = body.strip() if body.strip() else None

        return bundle

    async def _load_yaml_bundle(self, path: Path) -> Bundle:
        """Load bundle from YAML file."""
        data = await read_yaml(path)
        if data is None:
            data = {}

        return Bundle.from_dict(data, base_path=path.parent)

    async def _compose_includes(self, bundle: Bundle) -> Bundle:
        """Load and compose included bundles."""
        if not bundle.includes:
            return bundle

        included_bundles: list[Bundle] = []
        for include in bundle.includes:
            include_source = self._parse_include(include)
            if include_source:
                try:
                    # Resolve namespace:path syntax before loading
                    resolved_source = self._resolve_include_source(include_source)
                    if resolved_source is None:
                        logger.warning(f"Include could not be resolved (skipping): {include_source}")
                        continue

                    included = await self._load_single(
                        resolved_source,
                        auto_register=True,  # Register includes as first-class bundles
                        auto_include=True,
                    )
                    included_bundles.append(included)
                except BundleNotFoundError:
                    # Includes are opportunistic - but warn so users know
                    logger.warning(f"Include not found (skipping): {include_source}")
                    pass

        if not included_bundles:
            return bundle

        # Compose: includes first, then current bundle overrides
        result = included_bundles[0]
        for included in included_bundles[1:]:
            result = result.compose(included)

        return result.compose(bundle)

    def _resolve_include_source(self, source: str) -> str | None:
        """Resolve include source to a loadable URI.

        Resolution priority:
        1. URIs: git+, http://, https://, file:// → Return as-is
        2. namespace:path syntax (e.g., foundation:behaviors/streaming-ui)
           → Look up namespace's local_path, resolve path within it
        3. Plain names → Return as-is (let _load_single handle registry lookup)

        Args:
            source: Include source string.

        Returns:
            URI string, or None if namespace:path cannot be resolved.
        """
        # 1. Check if it's already a URI - return as-is
        if "://" in source or source.startswith("git+"):
            return source

        # 2. Check for namespace:path syntax
        if ":" in source:
            namespace, rel_path = source.split(":", 1)

            # Look up the namespace in the registry
            state = self._registry.get(namespace)
            if state and state.local_path:
                namespace_path = Path(state.local_path)

                # Resolve the path within the namespace's bundle directory
                if namespace_path.is_file():
                    # If namespace points to a file, resolve relative to its parent
                    resource_path = namespace_path.parent / rel_path
                else:
                    # If namespace points to a directory, resolve relative to it
                    resource_path = namespace_path / rel_path

                # Try common extensions if path doesn't exist directly
                candidates = [
                    resource_path,
                    resource_path.with_suffix(".yaml"),
                    resource_path.with_suffix(".yml"),
                    resource_path.with_suffix(".md"),
                    resource_path / "bundle.yaml",
                    resource_path / "bundle.md",
                ]
                for candidate in candidates:
                    if candidate.exists():
                        return f"file://{candidate.resolve()}"

                logger.debug(f"Namespace '{namespace}' found but path '{rel_path}' not found within it")
            else:
                logger.debug(f"Namespace '{namespace}' not found in registry or has no local_path")

            return None

        # 3. Plain name - return as-is for registry lookup
        return source

    def _parse_include(self, include: str | dict[str, Any]) -> str | None:
        """Parse include directive to source string."""
        if isinstance(include, str):
            return include
        if isinstance(include, dict):
            bundle_ref = include.get("bundle")
            if bundle_ref:
                return str(bundle_ref)
        return None

    def _find_nearest_bundle_file(self, start: Path, stop: Path) -> Path | None:
        """Walk up from start to stop looking for bundle.md or bundle.yaml.

        This enables subdirectory bundles to discover their root bundle,
        allowing access to shared resources in the source tree.

        Args:
            start: Directory to start searching from (typically subdirectory parent).
            stop: Directory to stop searching at (the source_root).

        Returns:
            Path to the nearest bundle file, or None if not found.
        """
        current = start.resolve()
        stop = stop.resolve()

        while current >= stop:
            bundle_md = current / "bundle.md"
            bundle_yaml = current / "bundle.yaml"

            if bundle_md.exists():
                return bundle_md
            if bundle_yaml.exists():
                return bundle_yaml

            # Don't go above stop
            if current == stop:
                break

            current = current.parent

        return None

    # =========================================================================
    # Update Methods
    # =========================================================================

    async def check_update(
        self,
        name: str | None = None,
    ) -> UpdateInfo | list[UpdateInfo] | None:
        """Check for updates.

        Args:
            name: Bundle name, or None to check all registered.

        Returns:
            - name provided: UpdateInfo if update available, None if up-to-date
            - name is None: List of UpdateInfo for bundles with updates
        """
        if name is None:
            # Check all registered bundles concurrently
            names = self.list_registered()
            if not names:
                return []

            results = await asyncio.gather(
                *[self._check_update_single(n) for n in names],
                return_exceptions=True,
            )

            updates = []
            for n, result in zip(names, results, strict=True):
                if isinstance(result, Exception):
                    logger.warning(f"Failed to check update for '{n}': {result}")
                elif result is not None:
                    updates.append(result)

            return updates

        return await self._check_update_single(name)

    async def _check_update_single(self, name: str) -> UpdateInfo | None:
        """Check if a single bundle has updates.

        Updates the checked_at timestamp. Returns None if no update is available.
        """
        state = self._registry.get(name)
        if not state:
            return None

        # Update checked_at timestamp
        state.checked_at = datetime.now()

        logger.debug(f"Checked for updates: {name} (checked_at={state.checked_at})")
        return None

    async def update(
        self,
        name: str | None = None,
    ) -> Bundle | dict[str, Bundle]:
        """Update to latest version (bypasses cache).

        Args:
            name: Bundle name, or None to update all registered.

        Returns:
            - name provided: Updated Bundle
            - name is None: Dict of name → Bundle for all updated

        Raises:
            KeyError: If specific name not registered (not raised for None).
        """
        if name is None:
            # Update all registered bundles concurrently
            names = self.list_registered()
            if not names:
                return {}

            results = await asyncio.gather(
                *[self._update_single(n) for n in names],
                return_exceptions=True,
            )

            bundles = {}
            for n, result in zip(names, results, strict=True):
                if isinstance(result, Exception):
                    logger.warning(f"Failed to update bundle '{n}': {result}")
                else:
                    bundles[n] = result

            return bundles

        return await self._update_single(name)

    async def _update_single(self, name: str) -> Bundle:
        """Update a single bundle to latest version."""
        state = self._registry.get(name)
        if not state:
            raise KeyError(f"Bundle '{name}' not registered")

        # Load with refresh=True to bypass cache
        bundle = await self._load_single(
            name,
            auto_register=False,
            refresh=True,
        )

        # Update state timestamps
        state.version = bundle.version
        state.loaded_at = datetime.now()
        state.checked_at = datetime.now()

        return bundle

    # =========================================================================
    # State Methods
    # =========================================================================

    def get_state(
        self,
        name: str | None = None,
    ) -> BundleState | dict[str, BundleState] | None:
        """Get tracked state.

        Args:
            name: Bundle name, or None to get all.

        Returns:
            - name provided: BundleState or None if not registered
            - name is None: Dict of name → BundleState
        """
        if name is None:
            return dict(self._registry)

        return self._registry.get(name)

    # =========================================================================
    # Persistence Methods
    # =========================================================================

    def save(self) -> None:
        """Persist registry state to home/registry.json."""
        self._home.mkdir(parents=True, exist_ok=True)
        registry_path = self._home / "registry.json"

        data = {
            "version": 1,
            "bundles": {name: state.to_dict() for name, state in self._registry.items()},
        }

        with open(registry_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        logger.debug(f"Saved registry to {registry_path}")

    def _load_persisted_state(self) -> None:
        """Load persisted state from disk."""
        registry_path = self._home / "registry.json"
        if not registry_path.exists():
            return

        try:
            with open(registry_path, encoding="utf-8") as f:
                data = json.load(f)

            for name, bundle_data in data.get("bundles", {}).items():
                self._registry[name] = BundleState.from_dict(name, bundle_data)

            logger.debug(f"Loaded registry from {registry_path} ({len(self._registry)} bundles)")
        except Exception as e:
            logger.warning(f"Failed to load registry from {registry_path}: {e}")


# Convenience function for simple usage
async def load_bundle(
    source: str,
    auto_include: bool = True,
    registry: BundleRegistry | None = None,
) -> Bundle:
    """Convenience function to load a bundle.

    Args:
        source: URI or bundle name.
        auto_include: Whether to load includes.
        registry: Optional registry (creates default if not provided).

    Returns:
        Loaded Bundle.
    """
    registry = registry or BundleRegistry()
    return await registry._load_single(source, auto_register=True, auto_include=auto_include)
