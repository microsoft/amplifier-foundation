"""Tests for ModuleActivator._install_dependencies guard.

Specifically tests the find_spec guard that prevents editable source builds
for packages already installed from PyPI wheels (e.g. amplifier-core).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from amplifier_foundation.modules.activator import ModuleActivator


class TestInstallDependenciesWheelGuard:
    """Tests for the 'already installed from wheels' guard in _install_dependencies.

    The guard prevents uv pip install -e (editable source build) for packages
    that are already importable in the current environment.  This matters because
    bundles like amplifier-core use maturin/Rust as their build backend, so an
    editable install would trigger a multi-minute Rust compilation even though
    the pre-built PyPI wheel is already present.
    """

    @pytest.mark.asyncio
    async def test_skips_install_when_package_already_importable(self) -> None:
        """_install_dependencies returns early if the package is already importable.

        When importlib.util.find_spec returns a non-None result for the package
        named in pyproject.toml, uv pip install must NOT be called.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir)
            pyproject = module_path / "pyproject.toml"
            pyproject.write_text('[project]\nname = "my-test-pkg"\nversion = "1.0.0"\n')

            activator = ModuleActivator(cache_dir=module_path / "cache")

            # Pretend the package is already importable (wheel is installed).
            fake_spec = MagicMock()
            with (
                patch(
                    "amplifier_foundation.modules.activator.find_spec",
                    return_value=fake_spec,
                ) as mock_find_spec,
                patch("subprocess.run") as mock_subprocess,
            ):
                await activator._install_dependencies(module_path)

                # find_spec was called with the normalised package name
                mock_find_spec.assert_called_once_with("my_test_pkg")
                # subprocess.run (i.e. uv pip install) must NOT have been called
                mock_subprocess.assert_not_called()

    @pytest.mark.asyncio
    async def test_proceeds_when_package_not_importable(self) -> None:
        """_install_dependencies runs uv pip install when find_spec returns None.

        If the package is not yet importable (fresh environment, first install),
        the editable install should proceed as normal.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir)
            pyproject = module_path / "pyproject.toml"
            pyproject.write_text('[project]\nname = "new-pkg"\nversion = "0.1.0"\n')

            activator = ModuleActivator(cache_dir=module_path / "cache")

            mock_completed = MagicMock(returncode=0)
            with (
                patch(
                    "amplifier_foundation.modules.activator.find_spec",
                    return_value=None,  # package NOT importable yet
                ),
                patch("subprocess.run", return_value=mock_completed) as mock_subprocess,
            ):
                await activator._install_dependencies(module_path)

                # subprocess.run should have been called (uv pip install -e)
                mock_subprocess.assert_called_once()
                call_args = mock_subprocess.call_args[0][0]
                assert "uv" in call_args
                assert "install" in call_args
                assert "-e" in call_args

    @pytest.mark.asyncio
    async def test_force_bypasses_wheel_guard(self) -> None:
        """force=True skips the find_spec check and always runs uv pip install.

        Even when the package is already importable, force=True should trigger
        a reinstall from source — e.g. to pick up local dev changes.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir)
            pyproject = module_path / "pyproject.toml"
            pyproject.write_text('[project]\nname = "forced-pkg"\nversion = "1.0.0"\n')

            activator = ModuleActivator(cache_dir=module_path / "cache")

            fake_spec = MagicMock()
            mock_completed = MagicMock(returncode=0)
            with (
                patch(
                    "amplifier_foundation.modules.activator.find_spec",
                    return_value=fake_spec,  # package IS importable ...
                ) as mock_find_spec,
                patch("subprocess.run", return_value=mock_completed) as mock_subprocess,
            ):
                await activator._install_dependencies(module_path, force=True)

                # find_spec should NOT have been consulted (guard bypassed)
                mock_find_spec.assert_not_called()
                # uv pip install should still have been called
                mock_subprocess.assert_called_once()

    @pytest.mark.asyncio
    async def test_hyphen_to_underscore_normalisation(self) -> None:
        """Package names with hyphens are normalised to underscores for find_spec.

        PyPI name 'amplifier-core' → importable as 'amplifier_core'.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir)
            pyproject = module_path / "pyproject.toml"
            pyproject.write_text(
                '[project]\nname = "amplifier-core"\nversion = "1.2.3"\n'
            )

            activator = ModuleActivator(cache_dir=module_path / "cache")

            fake_spec = MagicMock()
            with (
                patch(
                    "amplifier_foundation.modules.activator.find_spec",
                    return_value=fake_spec,
                ) as mock_find_spec,
                patch("subprocess.run") as mock_subprocess,
            ):
                await activator._install_dependencies(module_path)

                # Must use underscore form when calling find_spec
                mock_find_spec.assert_called_once_with("amplifier_core")
                mock_subprocess.assert_not_called()

    @pytest.mark.asyncio
    async def test_guard_proceeds_when_pyproject_has_no_project_name(self) -> None:
        """Falls through to install when pyproject.toml has no [project] name.

        Some pyproject.toml files only contain [tool.*] config (e.g. ruff/pyright).
        The guard must not block the install in this case.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir)
            pyproject = module_path / "pyproject.toml"
            pyproject.write_text("[tool.ruff]\nline-length = 100\n")

            activator = ModuleActivator(cache_dir=module_path / "cache")

            mock_completed = MagicMock(returncode=0)
            with (
                patch(
                    "amplifier_foundation.modules.activator.find_spec",
                    return_value=MagicMock(),  # would skip if name were found
                ) as mock_find_spec,
                patch("subprocess.run", return_value=mock_completed) as mock_subprocess,
            ):
                await activator._install_dependencies(module_path)

                # find_spec should NOT have been called (no package name to check)
                mock_find_spec.assert_not_called()
                # install should proceed (requirements/pyproject still processed)
                mock_subprocess.assert_called_once()

    @pytest.mark.asyncio
    async def test_guard_proceeds_when_no_pyproject(self) -> None:
        """Falls through when there is no pyproject.toml at all (requirements.txt path)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir)
            # No pyproject.toml — only requirements.txt
            req = module_path / "requirements.txt"
            req.write_text("requests>=2.0\n")

            activator = ModuleActivator(cache_dir=module_path / "cache")

            mock_completed = MagicMock(returncode=0)
            with (
                patch(
                    "amplifier_foundation.modules.activator.find_spec",
                    return_value=MagicMock(),
                ) as mock_find_spec,
                patch("subprocess.run", return_value=mock_completed) as mock_subprocess,
            ):
                await activator._install_dependencies(module_path)

                mock_find_spec.assert_not_called()
                mock_subprocess.assert_called_once()
