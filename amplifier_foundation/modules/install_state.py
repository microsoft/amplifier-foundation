"""Installation state tracking for fast module startup.

Tracks fingerprints of installed modules to skip redundant `uv pip install` calls.
When a module's pyproject.toml/requirements.txt hasn't changed, we can skip
the install step entirely, significantly speeding up startup.

Self-healing: Also verifies that declared dependencies are actually installed.
This catches stale state after `uv tool install --force` wipes the venv.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import re
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def _extract_dependencies_from_pyproject(pyproject_path: Path) -> list[str]:
    """Extract dependency names from a pyproject.toml file.

    Args:
        pyproject_path: Path to pyproject.toml file.

    Returns:
        List of dependency package names (without version specifiers).
    """
    if not pyproject_path.exists():
        return []

    try:
        # Use tomllib (Python 3.11+) or tomli as fallback
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore[import-not-found]
            except ImportError:
                # No TOML parser available - skip dependency check
                logger.debug("No TOML parser available, skipping dependency extraction")
                return []

        with open(pyproject_path, "rb") as f:
            config = tomllib.load(f)
    except Exception as e:
        logger.debug(f"Failed to parse {pyproject_path}: {e}")
        return []

    deps = []

    # Get dependencies from [project.dependencies]
    project_deps = config.get("project", {}).get("dependencies", [])
    for dep in project_deps:
        # Parse dependency string like "aiohttp>=3.8", "requests[security]", or "zope.interface>=5.0"
        # Extract the full package name including dots (for namespace packages)
        # Stops at: whitespace, extras [...], version specifiers [<>=!~], markers [;], URL [@]
        match = re.match(r"^([a-zA-Z0-9._-]+?)(?:\s|\[|[<>=!~;@]|$)", dep)
        if match:
            deps.append(match.group(1))

    return deps


def _check_dependency_installed(dep_name: str) -> bool:
    """Check if a dependency is installed in the current environment.

    Uses importlib.metadata to check by distribution name, which correctly
    handles packages where the import name differs from the package name
    (e.g., Pillow -> PIL, beautifulsoup4 -> bs4, scikit-learn -> sklearn).

    Args:
        dep_name: Package/distribution name (e.g., "aiohttp", "Pillow").

    Returns:
        True if the package is installed, False otherwise.
    """
    # Normalize for comparison: PEP 503 says package names are case-insensitive
    # and treats hyphens/underscores as equivalent
    normalized = dep_name.lower().replace("-", "_").replace(".", "_")

    try:
        # Try exact name first
        importlib.metadata.distribution(dep_name)
        return True
    except importlib.metadata.PackageNotFoundError:
        pass

    # Try normalized variations (handles case differences and hyphen/underscore)
    for variation in [normalized, normalized.replace("_", "-")]:
        try:
            importlib.metadata.distribution(variation)
            return True
        except importlib.metadata.PackageNotFoundError:
            continue

    return False


class InstallStateManager:
    """Tracks module installation state for fast startup.

    Stores fingerprints (pyproject.toml hash) for installed modules.
    If fingerprint matches, we can skip `uv pip install` entirely.

    Self-healing: corrupted JSON or schema mismatch creates fresh state.
    Invalidates all entries if Python executable changes.
    """

    VERSION = 1
    FILENAME = "install-state.json"

    def __init__(self, cache_dir: Path) -> None:
        """Initialize install state manager.

        Args:
            cache_dir: Directory for storing state file (e.g., ~/.amplifier).
        """
        self._state_file = cache_dir / self.FILENAME
        self._dirty = False
        self._state = self._load()

    def _load(self) -> dict:
        """Load state from disk, creating fresh state if needed."""
        if not self._state_file.exists():
            return self._fresh_state()

        try:
            with open(self._state_file) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.debug(f"Creating fresh install state (load failed: {e})")
            return self._fresh_state()

        # Version mismatch - create fresh
        if data.get("version") != self.VERSION:
            logger.debug(
                f"Creating fresh install state (version {data.get('version')} != {self.VERSION})"
            )
            return self._fresh_state()

        # Python executable changed - clear all entries
        if data.get("python") != sys.executable:
            logger.debug(
                f"Clearing install state (Python changed: {data.get('python')} -> {sys.executable})"
            )
            return self._fresh_state()

        return data

    def _fresh_state(self) -> dict:
        """Create a fresh empty state."""
        self._dirty = True
        return {
            "version": self.VERSION,
            "python": sys.executable,
            "modules": {},
        }

    def _compute_fingerprint(self, module_path: Path) -> str:
        """Compute fingerprint for a module's dependency files.

        Hashes pyproject.toml and requirements.txt if present.
        Returns "none" if no dependency files exist.
        """
        hasher = hashlib.sha256()
        files_hashed = 0

        for filename in ("pyproject.toml", "requirements.txt"):
            filepath = module_path / filename
            if filepath.exists():
                try:
                    content = filepath.read_bytes()
                    hasher.update(filename.encode())
                    hasher.update(content)
                    files_hashed += 1
                except OSError:
                    pass

        if files_hashed == 0:
            return "none"

        return f"sha256:{hasher.hexdigest()}"

    def is_installed(self, module_path: Path) -> bool:
        """Check if module is already installed with matching fingerprint.

        Also verifies that declared dependencies are actually present in the
        Python environment. This catches stale install state after operations
        like `uv tool install --force` that wipe the venv but don't clear
        the install-state.json file.

        Args:
            module_path: Path to the module directory.

        Returns:
            True if module is installed, fingerprint matches, AND all
            dependencies are actually present.
        """
        path_key = str(module_path.resolve())
        entry = self._state["modules"].get(path_key)

        if not entry:
            return False

        current_fingerprint = self._compute_fingerprint(module_path)
        stored_fingerprint = entry.get("pyproject_hash")

        if current_fingerprint != stored_fingerprint:
            logger.debug(
                f"Fingerprint mismatch for {module_path.name}: "
                f"{stored_fingerprint} -> {current_fingerprint}"
            )
            return False

        # Self-healing: Verify dependencies are actually installed
        # This catches stale state after venv wipe (e.g., uv tool install --force)
        pyproject_path = module_path / "pyproject.toml"
        deps = _extract_dependencies_from_pyproject(pyproject_path)

        missing_deps = []
        for dep in deps:
            if not _check_dependency_installed(dep):
                missing_deps.append(dep)

        if missing_deps:
            logger.info(
                f"Module {module_path.name} has missing dependencies: {missing_deps}. "
                f"Will reinstall."
            )
            # Invalidate this entry so save() will persist the change
            self.invalidate(module_path)
            return False

        return True

    def mark_installed(self, module_path: Path) -> None:
        """Record that a module was successfully installed.

        Args:
            module_path: Path to the module directory.
        """
        path_key = str(module_path.resolve())
        fingerprint = self._compute_fingerprint(module_path)

        self._state["modules"][path_key] = {"pyproject_hash": fingerprint}
        self._dirty = True

    def save(self) -> None:
        """Persist state to disk if changed.

        Uses atomic write (write to temp, rename) to avoid corruption.
        """
        if not self._dirty:
            return

        # Ensure parent directory exists
        self._state_file.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: write to temp file, then rename
        try:
            fd, temp_path = tempfile.mkstemp(
                dir=self._state_file.parent,
                prefix=".install-state-",
                suffix=".tmp",
            )
            try:
                with open(fd, "w") as f:
                    json.dump(self._state, f, indent=2)
                Path(temp_path).rename(self._state_file)
                self._dirty = False
            except Exception:
                # Clean up temp file on failure
                Path(temp_path).unlink(missing_ok=True)
                raise
        except OSError as e:
            logger.warning(f"Failed to save install state: {e}")

    def invalidate(self, module_path: Path | None = None) -> None:
        """Clear state for one module or all modules.

        Args:
            module_path: Path to specific module to invalidate,
                        or None to invalidate all modules.
        """
        if module_path is None:
            # Clear all entries
            if self._state["modules"]:
                self._state["modules"] = {}
                self._dirty = True
                logger.debug("Invalidated all module install states")
        else:
            # Clear specific entry
            path_key = str(module_path.resolve())
            if path_key in self._state["modules"]:
                del self._state["modules"][path_key]
                self._dirty = True
                logger.debug(f"Invalidated install state for {module_path.name}")

    def clear(self) -> None:
        """Clear all module install state.

        This is a convenience method equivalent to `invalidate(None)`.
        Use after operations that may have invalidated the Python environment,
        such as `amplifier reset --remove cache`.

        Changes are not persisted until `save()` is called.
        """
        self.invalidate(None)

    def invalidate_modules_with_missing_deps(self) -> tuple[int, int]:
        """Surgically invalidate only modules whose dependencies are missing.

        Checks each tracked module's declared dependencies against what's
        actually installed in the Python environment. Only invalidates entries
        for modules that have missing dependencies.

        This is useful after operations like `uv tool install --force` that
        recreate the Python environment but don't clear install-state.json.
        Modules with all dependencies still satisfied won't be reinstalled.

        Returns:
            Tuple of (modules_checked, modules_invalidated).

        Note:
            Changes are persisted immediately (calls save() internally).
        """
        modules = self._state.get("modules", {})
        if not modules:
            logger.debug("No modules in install state to check")
            return (0, 0)

        modules_checked = 0
        modules_to_invalidate = []

        for module_path_str in list(modules.keys()):
            module_path = Path(module_path_str)

            # Module directory no longer exists - mark for invalidation
            if not module_path.exists():
                modules_to_invalidate.append(module_path_str)
                continue

            pyproject_path = module_path / "pyproject.toml"
            deps = _extract_dependencies_from_pyproject(pyproject_path)
            modules_checked += 1

            # Check if all dependencies are installed
            missing_deps = []
            for dep in deps:
                if not _check_dependency_installed(dep):
                    missing_deps.append(dep)

            if missing_deps:
                logger.debug(
                    f"Module {module_path.name} has missing deps: {missing_deps}"
                )
                modules_to_invalidate.append(module_path_str)

        # Remove invalidated entries
        for path_str in modules_to_invalidate:
            del self._state["modules"][path_str]
            module_name = Path(path_str).name
            logger.info(
                f"Invalidated install state for {module_name} (missing dependencies)"
            )

        if modules_to_invalidate:
            self._dirty = True
            self.save()

        return (modules_checked, len(modules_to_invalidate))
