"""ModuleActivator.activate_bundle_package -- install a bundle root's Python
package only when a declared module actually lives there, and fail by name.

Field-reported shape this guards: an APPLICATION repo (root ``pyproject.toml``
with ``[project]`` + ``requires-python >= 3.13``) ships a skills-only behavior
whose single module is ``tool-skills`` fetched from ANOTHER repo. Adding that
behavior with ``amplifier bundle add ... --app`` used to editable-install the
application into the Amplifier environment on every session start; on a Python
3.12 host the install fails and every bundle load on the machine fails with it,
attributed to whichever bundle happened to be preparing.
"""

from __future__ import annotations

import logging
import platform
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from amplifier_foundation.bundle import Bundle
from amplifier_foundation.modules.activator import (
    BundlePackageInstallError,
    ModuleActivator,
    bundle_root_declares_module,
)
from amplifier_foundation.paths.resolution import parse_uri
from amplifier_foundation.sources.git import GitSourceHandler

OTHER_REPO_MODULE = "git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=modules/tool-skills"
SAME_REPO = "git+https://github.com/example-org/example-bundle@main"
SAME_REPO_MODULE = SAME_REPO + "#subdirectory=modules/tool-example"
UNSATISFIABLE = ">=3.99"


def _write_root(
    root: Path, *, requires_python: str | None = None, name: str = "app-pkg"
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    rp = f'requires-python = "{requires_python}"\n' if requires_python else ""
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1.0"\n{rp}'
        '\n[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
    )
    return root


def _cache_root_for(cache_dir: Path, uri: str) -> Path:
    """The directory the git handler would clone ``uri`` into -- the real placement
    computation, not a re-implementation of it."""
    return GitSourceHandler()._get_cache_path(parse_uri(uri), cache_dir)


def _activator(tmp_path: Path) -> ModuleActivator:
    return ModuleActivator(cache_dir=tmp_path / "amplifier-home", install_deps=True)


# ---------------------------------------------------------------------------
# bundle_root_declares_module -- the inference, in isolation
# ---------------------------------------------------------------------------


class TestBundleRootDeclaresModule:
    def test_other_repo_git_module_is_not_ours(self, tmp_path: Path) -> None:
        root = _write_root(tmp_path / "cache" / "app-abc")
        assert bundle_root_declares_module(root, [OTHER_REPO_MODULE]) is False

    def test_local_path_inside_root_is_ours(self, tmp_path: Path) -> None:
        root = _write_root(tmp_path / "bundle")
        # Relative ./ and ../ sources are rewritten to absolute paths at load
        # time, so this is the shape prepare() actually sees.
        assert (
            bundle_root_declares_module(root, [str(root / "modules" / "tool-x")])
            is True
        )

    def test_local_path_outside_root_is_not_ours(self, tmp_path: Path) -> None:
        root = _write_root(tmp_path / "bundle")
        other = tmp_path / "elsewhere" / "modules" / "tool-x"
        assert bundle_root_declares_module(root, [str(other)]) is False

    def test_same_repo_git_module_is_ours(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        root = _write_root(_cache_root_for(cache, SAME_REPO))
        assert bundle_root_declares_module(root, [SAME_REPO_MODULE]) is True

    def test_same_repo_different_ref_is_not_ours(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        root = _write_root(_cache_root_for(cache, SAME_REPO))
        other_ref = (
            SAME_REPO.replace("@main", "@v9") + "#subdirectory=modules/tool-example"
        )
        assert bundle_root_declares_module(root, [other_ref]) is False

    def test_garbage_sources_are_conservatively_not_ours(self, tmp_path: Path) -> None:
        root = _write_root(tmp_path / "bundle")
        assert (
            bundle_root_declares_module(root, ["", None, "@ns:thing", "::not a uri::"])
            is False
        )  # type: ignore[list-item]

    def test_empty_source_list_is_not_ours(self, tmp_path: Path) -> None:
        root = _write_root(tmp_path / "bundle")
        assert bundle_root_declares_module(root, []) is False


# ---------------------------------------------------------------------------
# activate_bundle_package -- the install decision + attribution
# ---------------------------------------------------------------------------


class TestActivateBundlePackage:
    @pytest.mark.asyncio
    async def test_app_repo_shape_skips_install_entirely(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The field-reported shape: unsatisfiable requires-python, and the only
        declared module comes from another repo. Nothing may be installed and
        nothing may raise -- the application is not a module dependency."""
        root = _write_root(
            tmp_path / "cache" / "app-abc", requires_python=UNSATISFIABLE
        )
        act = _activator(tmp_path)
        install = AsyncMock()
        monkeypatch.setattr(act, "_install_dependencies", install)

        await act.activate_bundle_package(root, module_sources=[OTHER_REPO_MODULE])

        install.assert_not_called()

    @pytest.mark.asyncio
    async def test_local_self_sourced_module_installs(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        root = _write_root(tmp_path / "bundle")
        act = _activator(tmp_path)
        install = AsyncMock()
        monkeypatch.setattr(act, "_install_dependencies", install)

        await act.activate_bundle_package(
            root, module_sources=[str(root / "modules" / "tool-x")]
        )

        install.assert_awaited_once_with(root)

    @pytest.mark.asyncio
    async def test_same_repo_git_module_installs(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        cache = tmp_path / "cache"
        root = _write_root(_cache_root_for(cache, SAME_REPO))
        act = _activator(tmp_path)
        install = AsyncMock()
        monkeypatch.setattr(act, "_install_dependencies", install)

        await act.activate_bundle_package(root, module_sources=[SAME_REPO_MODULE])

        install.assert_awaited_once_with(root)

    @pytest.mark.asyncio
    async def test_none_module_sources_keeps_legacy_behavior(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Callers that cannot supply the declared modules get the historical
        rule: a pyproject with [project] is installed."""
        root = _write_root(tmp_path / "bundle")
        act = _activator(tmp_path)
        install = AsyncMock()
        monkeypatch.setattr(act, "_install_dependencies", install)

        await act.activate_bundle_package(root)

        install.assert_awaited_once_with(root)

    @pytest.mark.asyncio
    async def test_requires_python_mismatch_raises_by_name_before_uv(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        root = _write_root(
            tmp_path / "bundle", requires_python=UNSATISFIABLE, name="needs-future"
        )
        act = _activator(tmp_path)
        install = AsyncMock()
        monkeypatch.setattr(act, "_install_dependencies", install)

        with pytest.raises(BundlePackageInstallError) as excinfo:
            await act.activate_bundle_package(
                root, module_sources=[str(root / "modules" / "tool-x")]
            )

        err = excinfo.value
        assert err.bundle_path == root
        assert err.package == "needs-future"
        msg = str(err)
        assert str(root) in msg
        assert "needs-future" in msg
        assert UNSATISFIABLE in msg
        assert platform.python_version() in msg
        assert "bundle remove" in msg
        install.assert_not_called()

    @pytest.mark.asyncio
    async def test_install_failure_is_attributed_to_owning_bundle(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        root = _write_root(tmp_path / "bundle", name="owner-pkg")
        act = _activator(tmp_path)
        boom = subprocess.CalledProcessError(
            1, ["uv", "pip", "install"], output="", stderr="No solution found"
        )
        monkeypatch.setattr(act, "_install_dependencies", AsyncMock(side_effect=boom))

        with pytest.raises(BundlePackageInstallError) as excinfo:
            await act.activate_bundle_package(
                root, module_sources=[str(root / "modules" / "tool-x")]
            )

        err = excinfo.value
        assert err.bundle_path == root
        assert err.package == "owner-pkg"
        assert "exited 1" in str(err)
        assert "No solution found" in str(err)
        assert isinstance(err.__cause__, subprocess.CalledProcessError)

    @pytest.mark.asyncio
    async def test_no_pyproject_is_still_a_noop(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        root = tmp_path / "bundle"
        root.mkdir()
        act = _activator(tmp_path)
        install = AsyncMock()
        monkeypatch.setattr(act, "_install_dependencies", install)

        await act.activate_bundle_package(root, module_sources=[str(root / "m")])

        install.assert_not_called()


# ---------------------------------------------------------------------------
# Bundle.prepare() -- the production call site, end to end
# ---------------------------------------------------------------------------


def _quiet_activation(monkeypatch) -> AsyncMock:
    """Stub module activation + state persistence so prepare() exercises ONLY
    the package-install decision. Returns the _install_dependencies mock."""
    install = AsyncMock()
    monkeypatch.setattr(ModuleActivator, "_install_dependencies", install)
    monkeypatch.setattr(ModuleActivator, "activate_all", AsyncMock(return_value={}))
    monkeypatch.setattr(ModuleActivator, "finalize", lambda self: None)
    return install


class TestPrepareCallSite:
    @pytest.mark.asyncio
    async def test_included_app_repo_package_is_never_installed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Exact production shape: the user's bundle declares tool-skills from the
        skills repo; an --app include contributes an application repo root whose
        package cannot install here. Even under strict=True, prepare() must
        complete without touching uv."""
        install = _quiet_activation(monkeypatch)
        user_root = tmp_path / "user"
        user_root.mkdir()
        app_root = _write_root(
            tmp_path / "cache" / "app-abc",
            requires_python=UNSATISFIABLE,
            name="the-app",
        )
        bundle = Bundle(
            name="user",
            base_path=user_root,
            tools=[{"module": "tool-skills", "source": OTHER_REPO_MODULE}],
            source_base_paths={"user": user_root, "the-app": app_root},
        )

        prepared = await bundle.prepare(install_deps=True, strict=True)

        assert prepared is not None
        install.assert_not_called()

    @pytest.mark.asyncio
    async def test_included_self_sourced_package_is_installed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A genuine bundle repo (module declared inside its own root) still gets
        its package installed -- the behavior the heuristic exists for."""
        install = _quiet_activation(monkeypatch)
        user_root = tmp_path / "user"
        user_root.mkdir()
        lib_root = _write_root(tmp_path / "cache" / "lib-abc", name="lib-pkg")
        bundle = Bundle(
            name="user",
            base_path=user_root,
            tools=[
                {"module": "tool-lib", "source": str(lib_root / "modules" / "tool-lib")}
            ],
            source_base_paths={"user": user_root, "lib": lib_root},
        )

        await bundle.prepare(install_deps=True, strict=True)

        install.assert_awaited_once_with(lib_root)

    @pytest.mark.asyncio
    async def test_included_package_failure_raises_under_strict(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _quiet_activation(monkeypatch)
        user_root = tmp_path / "user"
        user_root.mkdir()
        lib_root = _write_root(
            tmp_path / "cache" / "lib-abc",
            requires_python=UNSATISFIABLE,
            name="lib-pkg",
        )
        bundle = Bundle(
            name="user",
            base_path=user_root,
            tools=[
                {"module": "tool-lib", "source": str(lib_root / "modules" / "tool-lib")}
            ],
            source_base_paths={"user": user_root, "lib": lib_root},
        )

        with pytest.raises(BundlePackageInstallError) as excinfo:
            await bundle.prepare(install_deps=True, strict=True)
        assert excinfo.value.bundle_path == lib_root

    @pytest.mark.asyncio
    async def test_included_package_failure_is_skipped_with_warning_when_not_strict(
        self, tmp_path: Path, monkeypatch, caplog
    ) -> None:
        _quiet_activation(monkeypatch)
        user_root = tmp_path / "user"
        user_root.mkdir()
        lib_root = _write_root(
            tmp_path / "cache" / "lib-abc",
            requires_python=UNSATISFIABLE,
            name="lib-pkg",
        )
        bundle = Bundle(
            name="user",
            base_path=user_root,
            tools=[
                {"module": "tool-lib", "source": str(lib_root / "modules" / "tool-lib")}
            ],
            source_base_paths={"user": user_root, "lib": lib_root},
        )

        with caplog.at_level(
            logging.WARNING, logger="amplifier_foundation.bundle._dataclass"
        ):
            prepared = await bundle.prepare(install_deps=True, strict=False)

        assert prepared is not None
        assert any(
            "Included bundle 'lib' package skipped" in r.getMessage()
            and "lib-pkg" in r.getMessage()
            for r in caplog.records
        ), [r.getMessage() for r in caplog.records]

    @pytest.mark.asyncio
    async def test_own_root_package_failure_propagates(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The bundle being prepared cannot have its own package quietly skipped."""
        _quiet_activation(monkeypatch)
        own_root = _write_root(
            tmp_path / "own", requires_python=UNSATISFIABLE, name="own-pkg"
        )
        bundle = Bundle(
            name="own",
            base_path=own_root,
            tools=[
                {"module": "tool-own", "source": str(own_root / "modules" / "tool-own")}
            ],
        )

        with pytest.raises(BundlePackageInstallError) as excinfo:
            await bundle.prepare(install_deps=True, strict=False)
        assert excinfo.value.bundle_path == own_root

    @pytest.mark.asyncio
    async def test_install_deps_false_never_installs(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        install = _quiet_activation(monkeypatch)
        own_root = _write_root(tmp_path / "own")
        bundle = Bundle(
            name="own",
            base_path=own_root,
            tools=[
                {"module": "tool-own", "source": str(own_root / "modules" / "tool-own")}
            ],
        )

        await bundle.prepare(install_deps=False)

        install.assert_not_called()
