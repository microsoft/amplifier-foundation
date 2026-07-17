"""Tests for ModuleActivator._install_dependencies guard.

Specifically tests the _distribution_installed guard that prevents editable
source builds for packages already installed (e.g. amplifier-core), and the
regression coverage for issue #326 (name-guessing via find_spec produced
false "not installed" results for bundles whose distribution name differs
from their import name, or that ship no import package at all).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from amplifier_foundation.modules.activator import (
    ModuleActivator,
    _distribution_installed,
)


class TestInstallDependenciesWheelGuard:
    """Tests for the 'already installed' guard in _install_dependencies.

    The guard prevents uv pip install -e (editable source build) for packages
    that are already installed in the current environment.  This matters because
    bundles like amplifier-core use maturin/Rust as their build backend, so an
    editable install would trigger a multi-minute Rust compilation even though
    the pre-built PyPI wheel is already present.

    Detection is keyed on the distribution name via
    ``amplifier_foundation.modules.activator._distribution_installed``
    (importlib.metadata), not a guessed import name. See issue #326.
    """

    @pytest.mark.asyncio
    async def test_skips_install_when_package_already_importable(self) -> None:
        """_install_dependencies returns early if the distribution is already installed.

        When _distribution_installed returns True for the package named in
        pyproject.toml, uv pip install must NOT be called.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir)
            pyproject = module_path / "pyproject.toml"
            pyproject.write_text('[project]\nname = "my-test-pkg"\nversion = "1.0.0"\n')

            activator = ModuleActivator(cache_dir=module_path / "cache")

            # Pretend the package is already installed (wheel or editable install present).
            with (
                patch(
                    "amplifier_foundation.modules.activator._distribution_installed",
                    return_value=True,
                ) as mock_dist_installed,
                patch("subprocess.run") as mock_subprocess,
            ):
                await activator._install_dependencies(module_path)

                mock_dist_installed.assert_called_once()
                # subprocess.run (i.e. uv pip install) must NOT have been called
                mock_subprocess.assert_not_called()

    @pytest.mark.asyncio
    async def test_proceeds_when_package_not_importable(self) -> None:
        """_install_dependencies runs uv pip install when the distribution isn't installed.

        If the package is not yet installed (fresh environment, first install),
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
                    "amplifier_foundation.modules.activator._distribution_installed",
                    return_value=False,  # distribution NOT installed yet
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
        """force=True skips the distribution check and always runs uv pip install.

        Even when the package is already installed, force=True should trigger
        a reinstall from source — e.g. to pick up local dev changes.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir)
            pyproject = module_path / "pyproject.toml"
            pyproject.write_text('[project]\nname = "forced-pkg"\nversion = "1.0.0"\n')

            activator = ModuleActivator(cache_dir=module_path / "cache")

            mock_completed = MagicMock(returncode=0)
            with (
                patch(
                    "amplifier_foundation.modules.activator._distribution_installed",
                    return_value=True,  # package IS installed ...
                ) as mock_dist_installed,
                patch("subprocess.run", return_value=mock_completed) as mock_subprocess,
            ):
                await activator._install_dependencies(module_path, force=True)

                # _distribution_installed should NOT have been consulted (guard bypassed)
                mock_dist_installed.assert_not_called()
                # uv pip install should still have been called
                mock_subprocess.assert_called_once()

    @pytest.mark.asyncio
    async def test_guard_uses_raw_distribution_name(self) -> None:
        """The guard checks the RAW distribution name — no import-name guessing.

        Prior to issue #326's fix, the guard normalised the name via
        ``pkg_name.replace("-", "_")`` and called ``importlib.util.find_spec``,
        which mis-detected bundles whose distribution name differs from their
        import name (e.g. ``amplifier-bundle-evaluation`` -> ``amplifier_evaluation``)
        as "not installed". The fix keys on the distribution name directly via
        ``importlib.metadata``, so the guard must receive the hyphenated name
        unmodified.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir)
            pyproject = module_path / "pyproject.toml"
            pyproject.write_text(
                '[project]\nname = "amplifier-core"\nversion = "1.2.3"\n'
            )

            activator = ModuleActivator(cache_dir=module_path / "cache")

            with (
                patch(
                    "amplifier_foundation.modules.activator._distribution_installed",
                    return_value=True,
                ) as mock_dist_installed,
                patch("subprocess.run") as mock_subprocess,
            ):
                await activator._install_dependencies(module_path)

                # Must use the RAW hyphenated name — no name-guessing.
                mock_dist_installed.assert_called_once_with("amplifier-core")
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
                    "amplifier_foundation.modules.activator._distribution_installed",
                    return_value=True,  # would skip if name were found
                ) as mock_dist_installed,
                patch("subprocess.run", return_value=mock_completed) as mock_subprocess,
            ):
                await activator._install_dependencies(module_path)

                # _distribution_installed should NOT have been called (no package name to check)
                mock_dist_installed.assert_not_called()
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
                    "amplifier_foundation.modules.activator._distribution_installed",
                    return_value=True,
                ) as mock_dist_installed,
                patch("subprocess.run", return_value=mock_completed) as mock_subprocess,
            ):
                await activator._install_dependencies(module_path)

                mock_dist_installed.assert_not_called()
                mock_subprocess.assert_called_once()


class TestPolyglotTransportSkip:
    """Tests for the polyglot transport guard in _install_dependencies.

    When a module declares a non-Python transport in amplifier.toml (e.g.
    transport = "rust", "wasm", or "grpc"), _install_dependencies must skip
    pip/uv installation entirely — those modules ship pre-built binaries and
    don't have a Python build step.

    Contract:
    - transport = "rust"   → skip uv pip install
    - transport = "wasm"   → skip uv pip install
    - transport = "grpc"   → skip uv pip install
    - transport = "python" → proceed with uv pip install (normal path)
    - no amplifier.toml    → proceed with uv pip install (default Python path)
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("transport", ["rust", "wasm", "grpc"])
    async def test_skips_install_for_non_python_transport(self, transport: str) -> None:
        """_install_dependencies does NOT call subprocess.run for non-Python transports.

        When amplifier.toml declares transport = "rust", "wasm", or "grpc", the
        module ships a pre-built binary and has no Python build step — uv pip
        install must not be invoked.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir)
            amplifier_toml = module_path / "amplifier.toml"
            amplifier_toml.write_text(f'[module]\ntransport = "{transport}"\n')

            activator = ModuleActivator(cache_dir=module_path / "cache")

            with patch("subprocess.run") as mock_subprocess:
                await activator._install_dependencies(module_path)

                mock_subprocess.assert_not_called()

    @pytest.mark.asyncio
    async def test_proceeds_for_python_transport(self) -> None:
        """_install_dependencies calls subprocess.run for python transport.

        When amplifier.toml explicitly declares transport = "python", the normal
        Python install path should proceed — uv pip install must be called.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir)
            amplifier_toml = module_path / "amplifier.toml"
            amplifier_toml.write_text('[module]\ntransport = "python"\n')
            pyproject = module_path / "pyproject.toml"
            pyproject.write_text(
                '[project]\nname = "my-python-module"\nversion = "0.1.0"\n'
            )

            activator = ModuleActivator(cache_dir=module_path / "cache")

            mock_completed = MagicMock(returncode=0)
            with (
                patch(
                    "amplifier_foundation.modules.activator._distribution_installed",
                    return_value=False,  # distribution not yet installed
                ),
                patch("subprocess.run", return_value=mock_completed) as mock_subprocess,
            ):
                await activator._install_dependencies(module_path)

                mock_subprocess.assert_called_once()

    @pytest.mark.asyncio
    async def test_proceeds_when_no_amplifier_toml(self) -> None:
        """_install_dependencies calls subprocess.run when no amplifier.toml exists.

        When there is no amplifier.toml, the module is assumed to be a standard
        Python module — uv pip install must proceed as normal.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir)
            # Only pyproject.toml — no amplifier.toml
            pyproject = module_path / "pyproject.toml"
            pyproject.write_text(
                '[project]\nname = "plain-python-pkg"\nversion = "1.0.0"\n'
            )

            activator = ModuleActivator(cache_dir=module_path / "cache")

            mock_completed = MagicMock(returncode=0)
            with (
                patch(
                    "amplifier_foundation.modules.activator._distribution_installed",
                    return_value=False,  # distribution not yet installed
                ),
                patch("subprocess.run", return_value=mock_completed) as mock_subprocess,
            ):
                await activator._install_dependencies(module_path)

                mock_subprocess.assert_called_once()


class TestDistributionGuard326:
    """Regression tests for issue #326.

    #326: three guard sites guessed a package's import name via
    ``pkg_name.replace("-", "_")`` and called ``importlib.util.find_spec(...)``.
    When the distribution name and import name differ (e.g. dist
    ``amplifier-bundle-evaluation`` -> import ``amplifier_evaluation``) or the
    bundle ships no import package (``packages=[]``), the guess never resolved,
    so a valid install was treated as missing and reinstalled on every process.

    The fix replaces the guess with ``_distribution_installed()``, which keys
    on the distribution name via ``importlib.metadata`` — the same metadata
    that ``uv sync`` removes when it uninstalls an editable install, which is
    what keeps the pre-existing #147 stale-cache cross-check correct.
    """

    def test_distribution_installed_true_for_real_dist(self) -> None:
        """_distribution_installed reflects real importlib.metadata state.

        Direct unit test of the helper — no mocking. ``pytest`` is guaranteed
        to be installed in the test environment; a nonsense distribution name
        is guaranteed not to be.
        """
        assert _distribution_installed("pytest") is True
        assert _distribution_installed("no-such-distribution-xyz") is False

    @pytest.mark.asyncio
    async def test_name_mismatch_bundle_skips_reinstall(self) -> None:
        """A bundle whose import name differs from its distribution name is not reinstalled.

        Core #326 scenario: pyproject declares ``amplifier-bundle-evaluation``,
        whose import package would be guessed as ``amplifier_evaluation`` — a
        name that never matches what's actually importable. With the fix,
        the guard keys on the distribution name directly, so an installed
        distribution is correctly detected and the reinstall is skipped.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir)
            pyproject = module_path / "pyproject.toml"
            pyproject.write_text(
                '[project]\nname = "amplifier-bundle-evaluation"\nversion = "1.0.0"\n'
            )

            activator = ModuleActivator(cache_dir=module_path / "cache")

            with (
                patch(
                    "amplifier_foundation.modules.activator._distribution_installed",
                    return_value=True,
                ),
                patch("subprocess.run") as mock_subprocess,
            ):
                await activator._install_dependencies(module_path)

                mock_subprocess.assert_not_called()

    @pytest.mark.asyncio
    async def test_packageless_bundle_skips_reinstall(self) -> None:
        """A bundle that ships no import package (packages=[]) is not reinstalled.

        Same #326 mechanic as the name-mismatch case: a bundle with
        ``packages=[]`` has no importable module at all, so the old
        find_spec-based guess could never succeed. The distribution-based
        guard correctly detects the install regardless.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir)
            pyproject = module_path / "pyproject.toml"
            pyproject.write_text(
                '[project]\nname = "amplifier-bundle-attractor"\nversion = "1.0.0"\n'
            )

            activator = ModuleActivator(cache_dir=module_path / "cache")

            with (
                patch(
                    "amplifier_foundation.modules.activator._distribution_installed",
                    return_value=True,
                ),
                patch("subprocess.run") as mock_subprocess,
            ):
                await activator._install_dependencies(module_path)

                mock_subprocess.assert_not_called()

    @pytest.mark.asyncio
    async def test_147_not_regressed_stale_state_triggers_reinstall(self) -> None:
        """Stale install-state cache (issue #147) still triggers a reinstall.

        Simulates ``uv sync`` removing an editable install without touching the
        install-state fingerprint: ``is_installed()`` reports True (the cache
        believes it's installed) but ``_distribution_installed()`` reports
        False (the distribution metadata is actually gone). The stale-state
        cross-check in ``_install_dependencies`` must invalidate the cache
        entry and proceed with the reinstall — the #326 fix must not regress
        this #147 behavior.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir)
            pyproject = module_path / "pyproject.toml"
            pyproject.write_text(
                '[project]\nname = "amplifier-bundle-evaluation"\nversion = "1.0.0"\n'
            )

            activator = ModuleActivator(cache_dir=module_path / "cache")

            mock_completed = MagicMock(returncode=0)
            with (
                patch.object(
                    activator._install_state, "is_installed", return_value=True
                ),
                patch.object(activator._install_state, "invalidate") as mock_invalidate,
                patch(
                    "amplifier_foundation.modules.activator._distribution_installed",
                    return_value=False,
                ),
                patch("subprocess.run", return_value=mock_completed) as mock_subprocess,
            ):
                await activator._install_dependencies(module_path)

                mock_invalidate.assert_called_once_with(module_path)
                mock_subprocess.assert_called_once()

    @pytest.mark.asyncio
    async def test_147_healthy_state_skips(self) -> None:
        """A healthy cached install state, confirmed by real metadata, is not invalidated.

        ``is_installed()`` reports True and ``_distribution_installed()``
        confirms the distribution is genuinely present — no invalidation and
        no reinstall should occur.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir)
            pyproject = module_path / "pyproject.toml"
            pyproject.write_text(
                '[project]\nname = "amplifier-bundle-evaluation"\nversion = "1.0.0"\n'
            )

            activator = ModuleActivator(cache_dir=module_path / "cache")

            with (
                patch.object(
                    activator._install_state, "is_installed", return_value=True
                ),
                patch.object(activator._install_state, "invalidate") as mock_invalidate,
                patch(
                    "amplifier_foundation.modules.activator._distribution_installed",
                    return_value=True,
                ),
                patch("subprocess.run") as mock_subprocess,
            ):
                await activator._install_dependencies(module_path)

                mock_invalidate.assert_not_called()
                mock_subprocess.assert_not_called()
