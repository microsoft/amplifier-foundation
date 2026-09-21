"""App module caches must not be confused with shared configuration/history."""

import os
import subprocess
import sys

import pytest

from amplifier_foundation.bundle import Bundle
from amplifier_foundation.sources.git import rmtree_robust


def git(path, *args):
    return subprocess.check_output(
        ["git", "-C", str(path), *args], stderr=subprocess.DEVNULL, text=True
    ).strip()


@pytest.fixture
def origin(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "shared"))
    path = tmp_path / "origin"
    package = path / "amplifier_module_tool_cache_probe"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VERSION = 'old'\n")
    (path / "pyproject.toml").write_text(
        '[project]\nname="cache-probe"\nversion="0.0.1"\n'
    )
    git(path, "init", "-b", "main")
    git(path, "add", ".")
    git(
        path,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "old",
    )
    return path


def source(origin):
    return f"git+{origin.as_uri()}@main"


def spec(origin):
    return {"module": "tool-cache-probe", "source": source(origin)}


@pytest.mark.asyncio
async def test_explicit_cache_uses_validated_revision_without_touching_shared_cache(
    tmp_path, origin
):
    bundle = Bundle(name="fixture", tools=[spec(origin)])
    old = await bundle.prepare(install_deps=False, strict=True)
    old_path = old.resolver.resolve("tool-cache-probe").resolve()
    assert old_path.is_relative_to(tmp_path / "shared/cache")
    old_head = git(old_path, "rev-parse", "HEAD")

    (origin / "amplifier_module_tool_cache_probe/__init__.py").write_text(
        "VERSION = 'fixed'\n"
    )
    git(origin, "add", ".")
    git(
        origin,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "fixed",
    )
    app_cache = tmp_path / "app/releases/validated/foundation/cache"
    prepared = await bundle.prepare(
        install_deps=False, strict=True, cache_dir=app_cache
    )
    path = prepared.resolver.resolve("tool-cache-probe").resolve()
    assert path.is_relative_to(app_cache)
    assert git(path, "rev-parse", "HEAD") == git(origin, "rev-parse", "HEAD")
    assert git(old_path, "rev-parse", "HEAD") == old_head
    assert (
        old_path / "amplifier_module_tool_cache_probe/__init__.py"
    ).read_text() == "VERSION = 'old'\n"
    assert os.environ["AMPLIFIER_HOME"] == str(tmp_path / "shared")


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", ["agent", "lazy"])
async def test_child_and_lazy_resolution_keep_explicit_cache_after_eviction(
    tmp_path, origin, initial
):
    bundle = Bundle(
        name="fixture",
        agents={"worker": {"tools": [spec(origin)]}} if initial == "agent" else {},
    )
    app_cache = tmp_path / "app/cache"
    prepared = await bundle.prepare(install_deps=False, cache_dir=app_cache)
    resolver = prepared.resolver
    first = (
        await resolver.async_resolve("tool-cache-probe", source_hint=source(origin))
    ).resolve()
    assert first.is_relative_to(app_cache)
    rmtree_robust(first)
    restored = (
        await resolver.async_resolve("tool-cache-probe", source_hint=source(origin))
    ).resolve()
    assert restored == first and restored.is_dir()
    assert not (tmp_path / "shared/cache").exists()


@pytest.mark.asyncio
async def test_source_overrides_keep_explicit_cache(tmp_path, origin):
    bundle = Bundle(
        name="fixture",
        tools=[{"module": "tool-cache-probe", "source": "not-the-selected-source"}],
    )
    cache = tmp_path / "app/cache"
    prepared = await bundle.prepare(
        install_deps=False,
        cache_dir=cache,
        source_resolver=lambda module, declared: source(origin),
    )
    assert prepared.resolver.resolve("tool-cache-probe").resolve().is_relative_to(cache)
    assert bundle.tools[0]["source"] == "not-the-selected-source"
