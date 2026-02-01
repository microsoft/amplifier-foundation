"""Tests for InstallStateManager.

Tests the install state tracking that enables fast module startup by
skipping redundant uv pip install calls when fingerprints match.
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from amplifier_foundation.modules.install_state import InstallStateManager


class TestInstallStateManager:
    """Tests for InstallStateManager."""

    def test_fresh_state_on_missing_file(self) -> None:
        """Creates fresh state when no state file exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            manager = InstallStateManager(cache_dir)

            # Should have fresh state with current python and mtime
            assert manager._state["version"] == InstallStateManager.VERSION
            assert manager._state["python"] == sys.executable
            assert "python_mtime" in manager._state
            assert manager._state["modules"] == {}

    def test_fresh_state_includes_python_mtime(self) -> None:
        """Fresh state includes python_mtime as integer."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            manager = InstallStateManager(cache_dir)

            mtime = manager._state["python_mtime"]
            assert mtime is not None
            assert isinstance(mtime, int)

    def test_load_existing_state(self) -> None:
        """Loads existing state file correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)

            # Create a valid state file with current python and mtime
            manager1 = InstallStateManager(cache_dir)
            manager1.save()

            # Load in a new manager
            manager2 = InstallStateManager(cache_dir)

            assert manager2._state["version"] == InstallStateManager.VERSION
            assert manager2._state["python"] == sys.executable

    def test_mtime_change_triggers_fresh_state(self) -> None:
        """Changing python mtime invalidates all entries (env recreation)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            state_file = cache_dir / "install-state.json"

            # Create state with a module entry
            state = {
                "version": InstallStateManager.VERSION,
                "python": sys.executable,
                "python_mtime": 12345,  # Old mtime
                "modules": {"/some/module/path": {"pyproject_hash": "sha256:abc123"}},
            }
            state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(state_file, "w") as f:
                json.dump(state, f)

            # Load manager - current mtime will differ from stored
            manager = InstallStateManager(cache_dir)

            # Should have fresh state - modules cleared
            assert manager._state["modules"] == {}
            # Mtime should be updated to current
            assert manager._state["python_mtime"] != 12345

    def test_missing_mtime_in_old_state_triggers_fresh(self) -> None:
        """Old state files without python_mtime trigger fresh state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            state_file = cache_dir / "install-state.json"

            # Create state without python_mtime (old format)
            state = {
                "version": InstallStateManager.VERSION,
                "python": sys.executable,
                # No python_mtime field
                "modules": {"/some/module/path": {"pyproject_hash": "sha256:abc123"}},
            }
            state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(state_file, "w") as f:
                json.dump(state, f)

            # Load manager - missing mtime triggers fresh state
            manager = InstallStateManager(cache_dir)

            # Should have fresh state - modules cleared
            assert manager._state["modules"] == {}
            # Mtime should now be present
            assert "python_mtime" in manager._state

    def test_python_change_triggers_fresh_state(self) -> None:
        """Changing python executable invalidates all entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            state_file = cache_dir / "install-state.json"

            # Create state with different python executable
            state = {
                "version": InstallStateManager.VERSION,
                "python": "/some/other/python",  # Different python
                "python_mtime": 99999,
                "modules": {"/some/module/path": {"pyproject_hash": "sha256:abc123"}},
            }
            state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(state_file, "w") as f:
                json.dump(state, f)

            # Load manager - python mismatch triggers fresh state
            manager = InstallStateManager(cache_dir)

            # Should have fresh state
            assert manager._state["python"] == sys.executable
            assert manager._state["modules"] == {}

    def test_mtime_oserror_triggers_fresh_state(self) -> None:
        """OSError when getting mtime triggers fresh state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            state_file = cache_dir / "install-state.json"

            # Create a valid state
            state = {
                "version": InstallStateManager.VERSION,
                "python": sys.executable,
                "python_mtime": 12345,
                "modules": {"/some/module/path": {"pyproject_hash": "sha256:abc123"}},
            }
            state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(state_file, "w") as f:
                json.dump(state, f)

            # Mock getmtime to raise OSError
            with patch("os.path.getmtime", side_effect=OSError("Permission denied")):
                manager = InstallStateManager(cache_dir)

                # Should have fresh state (mtime couldn't be determined)
                assert manager._state["modules"] == {}
                assert manager._state["python_mtime"] is None

    def test_mark_installed_and_is_installed(self) -> None:
        """Module can be marked installed and checked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            module_dir = Path(tmpdir) / "test-module"
            module_dir.mkdir()

            # Create a pyproject.toml
            pyproject = module_dir / "pyproject.toml"
            pyproject.write_text('[project]\nname = "test"\n')

            manager = InstallStateManager(cache_dir)

            # Not installed yet
            assert not manager.is_installed(module_dir)

            # Mark as installed
            manager.mark_installed(module_dir)

            # Now it should be installed
            assert manager.is_installed(module_dir)

    def test_fingerprint_change_invalidates(self) -> None:
        """Changing pyproject.toml invalidates module."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            module_dir = Path(tmpdir) / "test-module"
            module_dir.mkdir()

            pyproject = module_dir / "pyproject.toml"
            pyproject.write_text('[project]\nname = "test"\nversion = "1.0.0"\n')

            manager = InstallStateManager(cache_dir)
            manager.mark_installed(module_dir)

            # Should be installed
            assert manager.is_installed(module_dir)

            # Change pyproject.toml
            pyproject.write_text('[project]\nname = "test"\nversion = "2.0.0"\n')

            # Now fingerprint should not match
            assert not manager.is_installed(module_dir)

    def test_save_and_persist(self) -> None:
        """State persists across instances after save."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            module_dir = Path(tmpdir) / "test-module"
            module_dir.mkdir()
            pyproject = module_dir / "pyproject.toml"
            pyproject.write_text('[project]\nname = "test"\n')

            # First manager - mark installed and save
            manager1 = InstallStateManager(cache_dir)
            manager1.mark_installed(module_dir)
            manager1.save()

            # Second manager - should see the installed module
            manager2 = InstallStateManager(cache_dir)
            assert manager2.is_installed(module_dir)

    def test_invalidate_specific_module(self) -> None:
        """Can invalidate a specific module."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            module_dir = Path(tmpdir) / "test-module"
            module_dir.mkdir()
            pyproject = module_dir / "pyproject.toml"
            pyproject.write_text('[project]\nname = "test"\n')

            manager = InstallStateManager(cache_dir)
            manager.mark_installed(module_dir)
            assert manager.is_installed(module_dir)

            # Invalidate
            manager.invalidate(module_dir)
            assert not manager.is_installed(module_dir)

    def test_invalidate_all_modules(self) -> None:
        """Can invalidate all modules."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            module_dir = Path(tmpdir) / "test-module"
            module_dir.mkdir()
            pyproject = module_dir / "pyproject.toml"
            pyproject.write_text('[project]\nname = "test"\n')

            manager = InstallStateManager(cache_dir)
            manager.mark_installed(module_dir)
            assert manager.is_installed(module_dir)

            # Invalidate all
            manager.invalidate(None)
            assert not manager.is_installed(module_dir)
