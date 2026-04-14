"""File source handler for local paths."""

from __future__ import annotations

from pathlib import Path

from amplifier_foundation.exceptions import BundleNotFoundError
from amplifier_foundation.paths.resolution import ParsedURI
from amplifier_foundation.paths.resolution import ResolvedSource


class FileSourceHandler:
    """Handler for file:// URIs and local paths."""

    def __init__(self, base_path: Path | None = None) -> None:
        """Initialize handler.

        Args:
            base_path: Base path for resolving relative paths.
        """
        self.base_path = base_path or Path.cwd()

    def can_handle(self, parsed: ParsedURI) -> bool:
        """Check if this handler can handle the given URI."""
        return parsed.is_file

    async def resolve(self, parsed: ParsedURI, cache_dir: Path) -> ResolvedSource:
        """Resolve file URI to local path.

        Args:
            parsed: Parsed URI components.
            cache_dir: Cache directory for detecting cached bundle paths.

        Returns:
            ResolvedSource with active_path and source_root.

        Raises:
            BundleNotFoundError: If file doesn't exist.
        """
        path_str = parsed.path

        # Handle relative paths
        if path_str.startswith("./") or path_str.startswith("../"):
            resolved_path = self.base_path / path_str
        else:
            resolved_path = Path(path_str)

        resolved_path = resolved_path.resolve()

        # Apply subpath if specified (from #subdirectory= fragment)
        active_path = resolved_path
        if parsed.subpath:
            active_path = resolved_path / parsed.subpath

        if not active_path.exists():
            raise BundleNotFoundError(f"File not found: {active_path}")

        # Determine source_root: the actual repository/bundle root
        # If a subdirectory was specified, the source_root is the base path
        # (before applying the subdirectory). Otherwise, try to detect it
        # from the cache directory structure.
        if parsed.subpath:
            # Subdirectory URI: source_root is the base path
            source_root = resolved_path
        else:
            # No subdirectory: try to find source root from cache structure
            source_root = self._find_source_root(active_path, cache_dir)

        return ResolvedSource(active_path=active_path, source_root=source_root)

    def _find_source_root(self, active_path: Path, cache_dir: Path) -> Path:
        """Find the actual source root for a file path.

        For files within the cache directory, the source root is the
        repository directory (first level under cache). This enables
        proper nested bundle detection for behaviors, providers, etc.
        that are loaded via file:// URIs from within cached bundles.

        Args:
            active_path: The resolved file path.
            cache_dir: The cache directory to check against.

        Returns:
            The source root path.
        """
        cache_dir = cache_dir.resolve()

        try:
            # Check if active_path is within the cache directory
            relative = active_path.relative_to(cache_dir)

            # The first component is the repository folder name
            # e.g., for "amplifier-foundation-abc123/behaviors/logging.yaml"
            # the repo folder is "amplifier-foundation-abc123"
            if relative.parts:
                repo_folder = relative.parts[0]
                return cache_dir / repo_folder

        except ValueError:
            # Path is not within cache_dir
            pass

        # For non-cached paths, walk up to find bundle root
        bundle_root = self._find_bundle_root(active_path)
        if bundle_root:
            return bundle_root

        # Fallback: use the path itself (or parent directory if it's a file)
        if active_path.is_file():
            return active_path.parent
        return active_path

    def _find_bundle_root(self, path: Path) -> Path | None:
        """Walk up from path to find the nearest bundle.md or bundle.yaml.

        This enables local behavior bundles (e.g., /repo/behaviors/foo.yaml)
        to properly resolve @namespace:path references to files in the parent
        bundle (e.g., /repo/agents/agent.md).

        Args:
            path: Starting path (file or directory)

        Returns:
            Directory containing bundle.md/yaml, or None if not found
        """
        current = path.resolve()
        if current.is_file():
            current = current.parent

        # Don't search above home directory or filesystem root
        stop = Path.home()

        while current >= stop and current != current.parent:
            if (current / "bundle.md").exists() or (current / "bundle.yaml").exists():
                return current
            current = current.parent

        return None
