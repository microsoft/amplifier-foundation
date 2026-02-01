"""Installation state tracking for fast module startup.

Tracks fingerprints of installed modules to skip redundant `uv pip install` calls.
When a module's pyproject.toml/requirements.txt hasn't changed, we can skip
the install step entirely, significantly speeding up startup.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class InstallStateManager:
    """Tracks module installation state for fast startup.

    Stores fingerprints (pyproject.toml hash) for installed modules.
    If fingerprint matches, we can skip `uv pip install` entirely.

    Self-healing: corrupted JSON or schema mismatch creates fresh state.
    Invalidates all entries if Python executable or its mtime changes.
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

    def _get_python_mtime(self) -> int | None:
        """Get Python executable mtime as integer seconds.

        Returns None if mtime cannot be determined (e.g., file doesn't exist).
        Integer avoids float comparison issues across JSON serialization.

        This detects when the Python environment was recreated (e.g., via
        `uv tool install --force`), which changes the executable's mtime
        even if the path stays the same.
        """
        try:
            return int(os.path.getmtime(sys.executable))
        except OSError:
            return None

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

        # Python executable mtime changed - environment was recreated
        # This catches `uv tool install --force` which recreates the venv
        current_mtime = self._get_python_mtime()
        stored_mtime = data.get("python_mtime")
        if current_mtime is None or stored_mtime != current_mtime:
            logger.debug(
                f"Clearing install state (Python mtime changed: {stored_mtime} -> {current_mtime})"
            )
            return self._fresh_state()

        return data

    def _fresh_state(self) -> dict:
        """Create a fresh empty state."""
        self._dirty = True
        return {
            "version": self.VERSION,
            "python": sys.executable,
            "python_mtime": self._get_python_mtime(),
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

        Args:
            module_path: Path to the module directory.

        Returns:
            True if module is installed and fingerprint matches.
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
