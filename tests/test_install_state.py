"""Tests for InstallStateManager and related functions."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


from amplifier_foundation.modules.install_state import (
    InstallStateManager,
    _check_dependency_installed,
    _extract_dependencies_from_pyproject,
)


class TestExtractDependenciesFromPyproject:
    """Tests for _extract_dependencies_from_pyproject helper function."""

    def test_nonexistent_file_returns_empty(self, tmp_path: Path) -> None:
        """Returns empty list when pyproject.toml doesn't exist."""
        result = _extract_dependencies_from_pyproject(tmp_path / "pyproject.toml")
        assert result == []

    def test_extracts_simple_dependencies(self, tmp_path: Path) -> None:
        """Extracts simple dependency names without version specifiers."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""
[project]
dependencies = [
    "aiohttp",
    "requests",
]
""")
        result = _extract_dependencies_from_pyproject(pyproject)
        assert result == ["aiohttp", "requests"]

    def test_extracts_dependencies_with_version_specifiers(
        self, tmp_path: Path
    ) -> None:
        """Extracts dependency names, stripping version specifiers."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""
[project]
dependencies = [
    "aiohttp>=3.8",
    "requests>=2.28,<3.0",
    "pydantic~=2.0",
]
""")
        result = _extract_dependencies_from_pyproject(pyproject)
        assert result == ["aiohttp", "requests", "pydantic"]

    def test_extracts_dependencies_with_extras(self, tmp_path: Path) -> None:
        """Extracts dependency names, stripping extras."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""
[project]
dependencies = [
    "requests[security]",
    "httpx[http2]>=0.24",
]
""")
        result = _extract_dependencies_from_pyproject(pyproject)
        assert result == ["requests", "httpx"]

    def test_extracts_namespace_packages(self, tmp_path: Path) -> None:
        """Extracts namespace package names with dots."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""
[project]
dependencies = [
    "zope.interface>=5.0",
    "ruamel.yaml",
]
""")
        result = _extract_dependencies_from_pyproject(pyproject)
        assert result == ["zope.interface", "ruamel.yaml"]

    def test_empty_dependencies_returns_empty(self, tmp_path: Path) -> None:
        """Returns empty list when no dependencies declared."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""
[project]
name = "test-package"
""")
        result = _extract_dependencies_from_pyproject(pyproject)
        assert result == []

    def test_invalid_toml_returns_empty(self, tmp_path: Path) -> None:
        """Returns empty list when TOML is invalid."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("this is not valid toml {{{{")
        result = _extract_dependencies_from_pyproject(pyproject)
        assert result == []


class TestCheckDependencyInstalled:
    """Tests for _check_dependency_installed helper function."""

    def test_installed_package_returns_true(self) -> None:
        """Returns True for packages that are installed."""
        # pytest is definitely installed since we're running tests
        assert _check_dependency_installed("pytest") is True

    def test_uninstalled_package_returns_false(self) -> None:
        """Returns False for packages that are not installed."""
        assert _check_dependency_installed("definitely-not-a-real-package-xyz") is False

    def test_case_insensitive_matching(self) -> None:
        """Package names are matched case-insensitively."""
        # PyTest vs pytest
        assert _check_dependency_installed("PyTest") is True

    def test_hyphen_underscore_normalization(self) -> None:
        """Hyphens and underscores are treated as equivalent."""
        # Test with a package we know is installed
        # amplifier-core uses hyphens but installs as amplifier_core
        with patch("importlib.metadata.distribution") as mock_dist:
            # First call with exact name fails, second with normalized succeeds
            mock_dist.side_effect = [
                __import__("importlib.metadata").metadata.PackageNotFoundError(),
                None,  # Success on normalized name
            ]
            result = _check_dependency_installed("some-package")
            assert result is True


class TestInstallStateManager:
    """Tests for InstallStateManager class."""

    def test_creates_fresh_state_when_no_file(self, tmp_path: Path) -> None:
        """Creates fresh state when install-state.json doesn't exist."""
        mgr = InstallStateManager(tmp_path)
        assert mgr._state["version"] == 1
        assert mgr._state["modules"] == {}

    def test_loads_existing_state(self, tmp_path: Path) -> None:
        """Loads existing state from disk."""
        import sys

        state_file = tmp_path / "install-state.json"
        state_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "python": sys.executable,
                    "modules": {"/some/path": {"pyproject_hash": "sha256:abc123"}},
                }
            )
        )
        mgr = InstallStateManager(tmp_path)
        assert "/some/path" in mgr._state["modules"]

    def test_creates_fresh_state_on_version_mismatch(self, tmp_path: Path) -> None:
        """Creates fresh state when version doesn't match."""
        state_file = tmp_path / "install-state.json"
        state_file.write_text(
            json.dumps(
                {
                    "version": 999,  # Wrong version
                    "python": "/usr/bin/python",
                    "modules": {"/some/path": {"pyproject_hash": "sha256:abc123"}},
                }
            )
        )
        mgr = InstallStateManager(tmp_path)
        assert mgr._state["modules"] == {}

    def test_creates_fresh_state_on_python_change(self, tmp_path: Path) -> None:
        """Creates fresh state when Python executable changed."""
        state_file = tmp_path / "install-state.json"
        state_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "python": "/different/python",  # Different Python
                    "modules": {"/some/path": {"pyproject_hash": "sha256:abc123"}},
                }
            )
        )
        mgr = InstallStateManager(tmp_path)
        assert mgr._state["modules"] == {}

    def test_creates_fresh_state_on_invalid_json(self, tmp_path: Path) -> None:
        """Creates fresh state when JSON is invalid."""
        state_file = tmp_path / "install-state.json"
        state_file.write_text("not valid json {{{")
        mgr = InstallStateManager(tmp_path)
        assert mgr._state["modules"] == {}


class TestInstallStateManagerIsInstalled:
    """Tests for InstallStateManager.is_installed method."""

    def test_returns_false_when_not_tracked(self, tmp_path: Path) -> None:
        """Returns False when module is not in state."""
        mgr = InstallStateManager(tmp_path)
        module_path = tmp_path / "some-module"
        module_path.mkdir()
        assert mgr.is_installed(module_path) is False

    def test_returns_true_when_fingerprint_matches(self, tmp_path: Path) -> None:
        """Returns True when fingerprint matches and no deps to check."""
        module_path = tmp_path / "some-module"
        module_path.mkdir()
        # No pyproject.toml means no deps to check

        mgr = InstallStateManager(tmp_path)
        mgr.mark_installed(module_path)

        assert mgr.is_installed(module_path) is True

    def test_returns_false_when_fingerprint_changes(self, tmp_path: Path) -> None:
        """Returns False when pyproject.toml content changed."""
        module_path = tmp_path / "some-module"
        module_path.mkdir()

        pyproject = module_path / "pyproject.toml"
        pyproject.write_text("[project]\nname = 'test'\n")

        mgr = InstallStateManager(tmp_path)
        mgr.mark_installed(module_path)

        # Change the pyproject.toml
        pyproject.write_text("[project]\nname = 'test'\nversion = '2.0'\n")

        assert mgr.is_installed(module_path) is False

    def test_returns_false_when_dependency_missing(self, tmp_path: Path) -> None:
        """Returns False when a declared dependency is not installed."""
        module_path = tmp_path / "some-module"
        module_path.mkdir()

        pyproject = module_path / "pyproject.toml"
        pyproject.write_text("""
[project]
name = "test"
dependencies = ["definitely-not-installed-package-xyz"]
""")

        mgr = InstallStateManager(tmp_path)
        mgr.mark_installed(module_path)

        # Should return False because dependency is missing
        assert mgr.is_installed(module_path) is False

    def test_returns_true_when_all_dependencies_present(self, tmp_path: Path) -> None:
        """Returns True when all declared dependencies are installed."""
        module_path = tmp_path / "some-module"
        module_path.mkdir()

        pyproject = module_path / "pyproject.toml"
        pyproject.write_text("""
[project]
name = "test"
dependencies = ["pytest"]
""")

        mgr = InstallStateManager(tmp_path)
        mgr.mark_installed(module_path)

        # Should return True because pytest is installed
        assert mgr.is_installed(module_path) is True

    def test_invalidates_entry_when_dependency_missing(self, tmp_path: Path) -> None:
        """Invalidates the state entry when dependency is missing."""
        module_path = tmp_path / "some-module"
        module_path.mkdir()

        pyproject = module_path / "pyproject.toml"
        pyproject.write_text("""
[project]
name = "test"
dependencies = ["definitely-not-installed-xyz"]
""")

        mgr = InstallStateManager(tmp_path)
        mgr.mark_installed(module_path)
        path_key = str(module_path.resolve())

        # Verify it's tracked
        assert path_key in mgr._state["modules"]

        # Check is_installed (should return False and invalidate)
        assert mgr.is_installed(module_path) is False

        # Verify it was invalidated
        assert path_key not in mgr._state["modules"]


class TestInstallStateManagerMarkInstalled:
    """Tests for InstallStateManager.mark_installed method."""

    def test_marks_module_as_installed(self, tmp_path: Path) -> None:
        """Records module as installed with fingerprint."""
        module_path = tmp_path / "some-module"
        module_path.mkdir()

        mgr = InstallStateManager(tmp_path)
        mgr.mark_installed(module_path)

        path_key = str(module_path.resolve())
        assert path_key in mgr._state["modules"]
        assert "pyproject_hash" in mgr._state["modules"][path_key]

    def test_computes_fingerprint_from_pyproject(self, tmp_path: Path) -> None:
        """Computes fingerprint from pyproject.toml content."""
        module_path = tmp_path / "some-module"
        module_path.mkdir()
        (module_path / "pyproject.toml").write_text("[project]\nname = 'test'\n")

        mgr = InstallStateManager(tmp_path)
        mgr.mark_installed(module_path)

        path_key = str(module_path.resolve())
        assert mgr._state["modules"][path_key]["pyproject_hash"].startswith("sha256:")


class TestInstallStateManagerSave:
    """Tests for InstallStateManager.save method."""

    def test_saves_state_to_disk(self, tmp_path: Path) -> None:
        """Persists state to disk."""
        module_path = tmp_path / "some-module"
        module_path.mkdir()

        mgr = InstallStateManager(tmp_path)
        mgr.mark_installed(module_path)
        mgr.save()

        state_file = tmp_path / "install-state.json"
        assert state_file.exists()

        data = json.loads(state_file.read_text())
        assert str(module_path.resolve()) in data["modules"]

    def test_no_op_when_not_dirty(self, tmp_path: Path) -> None:
        """Does nothing when state hasn't changed."""
        state_file = tmp_path / "install-state.json"

        mgr = InstallStateManager(tmp_path)
        mgr._dirty = False
        mgr.save()

        # File might exist from init, but check it wasn't modified
        # by verifying a second save also does nothing
        if state_file.exists():
            mtime = state_file.stat().st_mtime
            mgr.save()
            assert state_file.stat().st_mtime == mtime


class TestInstallStateManagerInvalidate:
    """Tests for InstallStateManager.invalidate method."""

    def test_invalidates_specific_module(self, tmp_path: Path) -> None:
        """Removes state for a specific module."""
        module1 = tmp_path / "module1"
        module1.mkdir()
        module2 = tmp_path / "module2"
        module2.mkdir()

        mgr = InstallStateManager(tmp_path)
        mgr.mark_installed(module1)
        mgr.mark_installed(module2)

        mgr.invalidate(module1)

        assert str(module1.resolve()) not in mgr._state["modules"]
        assert str(module2.resolve()) in mgr._state["modules"]

    def test_invalidates_all_modules_when_none(self, tmp_path: Path) -> None:
        """Removes state for all modules when path is None."""
        module1 = tmp_path / "module1"
        module1.mkdir()
        module2 = tmp_path / "module2"
        module2.mkdir()

        mgr = InstallStateManager(tmp_path)
        mgr.mark_installed(module1)
        mgr.mark_installed(module2)

        mgr.invalidate(None)

        assert mgr._state["modules"] == {}


class TestInstallStateManagerClear:
    """Tests for InstallStateManager.clear method."""

    def test_clears_all_modules(self, tmp_path: Path) -> None:
        """Clears all module state."""
        module1 = tmp_path / "module1"
        module1.mkdir()
        module2 = tmp_path / "module2"
        module2.mkdir()

        mgr = InstallStateManager(tmp_path)
        mgr.mark_installed(module1)
        mgr.mark_installed(module2)

        mgr.clear()

        assert mgr._state["modules"] == {}
        assert mgr._dirty is True


class TestInstallStateManagerInvalidateModulesWithMissingDeps:
    """Tests for InstallStateManager.invalidate_modules_with_missing_deps method."""

    def test_returns_zero_when_no_modules(self, tmp_path: Path) -> None:
        """Returns (0, 0) when no modules tracked."""
        mgr = InstallStateManager(tmp_path)
        checked, invalidated = mgr.invalidate_modules_with_missing_deps()
        assert checked == 0
        assert invalidated == 0

    def test_invalidates_module_with_missing_dep(self, tmp_path: Path) -> None:
        """Invalidates module when dependency is missing."""
        module_path = tmp_path / "some-module"
        module_path.mkdir()
        (module_path / "pyproject.toml").write_text("""
[project]
name = "test"
dependencies = ["definitely-not-installed-xyz"]
""")

        mgr = InstallStateManager(tmp_path)
        mgr.mark_installed(module_path)
        mgr.save()

        checked, invalidated = mgr.invalidate_modules_with_missing_deps()

        assert checked == 1
        assert invalidated == 1
        assert str(module_path.resolve()) not in mgr._state["modules"]

    def test_keeps_module_with_all_deps_present(self, tmp_path: Path) -> None:
        """Keeps module when all dependencies are present."""
        module_path = tmp_path / "some-module"
        module_path.mkdir()
        (module_path / "pyproject.toml").write_text("""
[project]
name = "test"
dependencies = ["pytest"]
""")

        mgr = InstallStateManager(tmp_path)
        mgr.mark_installed(module_path)
        mgr.save()

        checked, invalidated = mgr.invalidate_modules_with_missing_deps()

        assert checked == 1
        assert invalidated == 0
        assert str(module_path.resolve()) in mgr._state["modules"]

    def test_invalidates_module_with_nonexistent_path(self, tmp_path: Path) -> None:
        """Invalidates module when its directory no longer exists."""
        import sys

        # Directly inject a state entry for a non-existent path
        mgr = InstallStateManager(tmp_path)
        mgr._state["modules"]["/nonexistent/path"] = {"pyproject_hash": "sha256:abc"}
        mgr._dirty = True
        mgr.save()

        # Reload and check
        mgr2 = InstallStateManager(tmp_path)
        # Fix the python path to match current
        mgr2._state["python"] = sys.executable
        checked, invalidated = mgr2.invalidate_modules_with_missing_deps()

        assert invalidated == 1
        assert "/nonexistent/path" not in mgr2._state["modules"]

    def test_persists_changes_immediately(self, tmp_path: Path) -> None:
        """Saves changes to disk immediately after invalidation."""
        module_path = tmp_path / "some-module"
        module_path.mkdir()
        (module_path / "pyproject.toml").write_text("""
[project]
name = "test"
dependencies = ["not-installed-xyz"]
""")

        mgr = InstallStateManager(tmp_path)
        mgr.mark_installed(module_path)
        mgr.save()

        mgr.invalidate_modules_with_missing_deps()

        # Reload from disk and verify change was persisted
        mgr2 = InstallStateManager(tmp_path)
        assert str(module_path.resolve()) not in mgr2._state["modules"]

    def test_handles_mixed_modules(self, tmp_path: Path) -> None:
        """Correctly handles mix of valid and invalid modules."""
        # Module with missing dep
        bad_module = tmp_path / "bad-module"
        bad_module.mkdir()
        (bad_module / "pyproject.toml").write_text("""
[project]
dependencies = ["not-installed-xyz"]
""")

        # Module with present dep
        good_module = tmp_path / "good-module"
        good_module.mkdir()
        (good_module / "pyproject.toml").write_text("""
[project]
dependencies = ["pytest"]
""")

        # Module with no deps
        nodeps_module = tmp_path / "nodeps-module"
        nodeps_module.mkdir()

        mgr = InstallStateManager(tmp_path)
        mgr.mark_installed(bad_module)
        mgr.mark_installed(good_module)
        mgr.mark_installed(nodeps_module)
        mgr.save()

        checked, invalidated = mgr.invalidate_modules_with_missing_deps()

        assert checked == 3
        assert invalidated == 1
        assert str(bad_module.resolve()) not in mgr._state["modules"]
        assert str(good_module.resolve()) in mgr._state["modules"]
        assert str(nodeps_module.resolve()) in mgr._state["modules"]
