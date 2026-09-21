"""Prepared bundles must not retain paths to evicted module checkouts."""

import asyncio
import shutil
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest
from amplifier_core.loader import ModuleLoader
from amplifier_core.module_sources import ModuleNotFoundError as CoreModuleNotFoundError

from amplifier_foundation.bundle import BundleModuleResolver
from amplifier_foundation.modules.activator import ModuleActivator


@pytest.fixture
def module_origin(tmp_path):
    origin = tmp_path / "origin"
    package = origin / "amplifier_module_loop_live"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# fixture module\n")
    (origin / "pyproject.toml").write_text(
        '[project]\nname="fixture"\nversion="0.1.0"\n'
    )
    subprocess.run(
        ["git", "init", "-b", "main", str(origin)], check=True, capture_output=True
    )
    subprocess.run(["git", "add", "."], cwd=origin, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
        cwd=origin,
        check=True,
        capture_output=True,
    )
    return origin


@pytest.mark.asyncio
async def test_reactivates_evicted_checkout_before_package_validation(
    tmp_path, module_origin, monkeypatch
):
    """Use the real activator and a local Git origin, with no network or installs."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    source = f"git+{module_origin.as_uri()}@main"
    activator = ModuleActivator(cache_dir=tmp_path / "cache", install_deps=False)
    cached = await activator.activate("loop-live", source)
    resolver = BundleModuleResolver({"loop-live": cached}, activator)
    shutil.rmtree(cached)

    restored = (await resolver.async_resolve("loop-live", source_hint=source)).resolve()

    assert restored == cached
    assert (
        ModuleLoader._find_package_dir(None, "loop-live", restored)
        == restored / "amplifier_module_loop_live"
    )
    assert (
        restored / "amplifier_module_loop_live/__init__.py"
    ).read_text() == "# fixture module\n"


@pytest.mark.asyncio
async def test_concurrent_recovery_activates_once(tmp_path):
    missing = tmp_path / "evicted"

    async def activate(*args):
        await asyncio.sleep(0)
        missing.mkdir()
        return missing

    activator = AsyncMock()
    activator.activate.side_effect = activate
    resolver = BundleModuleResolver({"tool-test": missing}, activator)
    results = await asyncio.gather(
        *(
            resolver.async_resolve("tool-test", source_hint="declared-source")
            for _ in range(4)
        )
    )
    assert all(result.resolve() == missing for result in results)
    activator.activate.assert_awaited_once_with("tool-test", "declared-source")


def test_sync_missing_path_raises_core_exception(tmp_path):
    resolver = BundleModuleResolver({"tool-test": tmp_path / "evicted"})
    with pytest.raises(CoreModuleNotFoundError, match="async_resolve"):
        resolver.resolve("tool-test")


@pytest.mark.asyncio
async def test_present_paths_do_not_reactivate(tmp_path):
    activator = AsyncMock()
    resolver = BundleModuleResolver({"tool-test": tmp_path}, activator)
    assert resolver.resolve("tool-test").resolve() == tmp_path
    assert (
        await resolver.async_resolve("tool-test", source_hint="source")
    ).resolve() == tmp_path
    activator.activate.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_activator", [False, True])
async def test_missing_path_without_recovery_inputs_fails_clearly(
    tmp_path, with_activator
):
    resolver = BundleModuleResolver(
        {"tool-test": tmp_path / "evicted"},
        AsyncMock() if with_activator else None,
    )
    with pytest.raises(CoreModuleNotFoundError):
        await resolver.async_resolve("tool-test")
