"""Real resolver consumers must not reach source handlers under admission."""

import asyncio
import sys
from pathlib import Path

import pytest

from amplifier_foundation import Bundle
from amplifier_foundation.bundle._prepared import (
    BundleModuleResolver,
    BundleModuleSource,
)
from amplifier_foundation.modules.activator import ModuleActivator
from amplifier_foundation.paths.resolution import ResolvedSource
from amplifier_foundation.registry import BundleRegistry
from amplifier_foundation.sources import (
    SOURCE_RESOLUTION_POLICY_VERSION,
    SimpleSourceResolver,
    SourceResolutionDenied,
    source_resolution_policy,
)

URI = "git+https://example.test/provider@main"


@pytest.fixture
def no_handlers(monkeypatch):
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Handler resolution bypassed admission")

    for name in ("file", "git", "http", "zip"):
        module = __import__("amplifier_foundation.sources." + name, fromlist=["x"])
        cls = getattr(
            module,
            {
                "file": "FileSourceHandler",
                "git": "GitSourceHandler",
                "http": "HttpSourceHandler",
                "zip": "ZipSourceHandler",
            }[name],
        )
        monkeypatch.setattr(cls, "resolve", forbidden)
    return calls


def admission(root, *, bindings=None):
    bindings = bindings or {URI: root, str(root): root}

    def admit(uri, *, base_path):
        if uri not in bindings:
            raise SourceResolutionDenied("Unbound source")
        return ResolvedSource(active_path=bindings[uri], source_root=root)

    return admit


@pytest.mark.asyncio
async def test_real_registry_and_activation_zero_handler_cache_writes(
    tmp_path, no_handlers
):
    root = tmp_path / "approved"
    root.mkdir()
    (root / "bundle.yaml").write_text("bundle:\n  name: approved\n")
    cache = tmp_path / "absent-cache"
    admit = admission(root)
    registry = BundleRegistry(tmp_path / "registry", strict=True, persist=False)
    activator = ModuleActivator(cache_dir=cache, install_deps=False)
    before = set(tmp_path.rglob("*"))
    previous_path = list(sys.path)
    try:
        with source_resolution_policy(admit):
            bundle = await registry.load(URI)
            assert bundle.name == "approved"
            assert await activator.activate("provider-test", URI) == root
            assert await activator.activate("provider-test", URI) == root
            assert await registry.load(URI) is bundle
            assert await asyncio.create_task(
                SimpleSourceResolver(cache).resolve(URI)
            ) == ResolvedSource(root, root)
            prepared = await Bundle.from_dict(
                {
                    "bundle": {"name": "consumer"},
                    "tools": [{"module": "tool-test", "source": URI}],
                }
            ).prepare(install_deps=False, strict=True, cache_dir=cache)
            assert prepared.resolver.resolve("tool-test", URI).resolve() == root
            assert ("tool-test", None, root) in prepared.resolver.prepared_sources()
    finally:
        sys.path[:] = previous_path
    assert not no_handlers
    assert not cache.exists()
    assert set(tmp_path.rglob("*")) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "uri",
    [
        URI,
        "https://example.test/source",
        "zip+https://example.test/source.zip",
        "file:///unbound",
        "../unbound",
        "malformed-source",
    ],
)
async def test_unbound_all_schemes_no_fallback(tmp_path, no_handlers, uri):
    def refuse(uri, *, base_path):
        raise SourceResolutionDenied("Unbound source")

    cache = tmp_path / "cache"
    with source_resolution_policy(refuse):
        with pytest.raises(SourceResolutionDenied):
            await SimpleSourceResolver(cache).resolve(uri)
    assert not cache.exists()
    assert not no_handlers


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["none", "relative", "missing", "escape", "symlink"])
async def test_invalid_admitted_results_refused(tmp_path, no_handlers, kind):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("external")
    link = root / "link"
    link.symlink_to(outside)
    results = {
        "none": None,
        "relative": ResolvedSource(Path("relative"), root),
        "missing": ResolvedSource(root / "missing", root),
        "escape": ResolvedSource(outside, root),
        "symlink": ResolvedSource(link, root),
    }

    def admit(uri, *, base_path):
        return results[kind]

    with source_resolution_policy(admit):
        with pytest.raises(SourceResolutionDenied):
            await SimpleSourceResolver(tmp_path / "cache").resolve(URI)
    assert not no_handlers


@pytest.mark.asyncio
async def test_resource_file_and_base_path(tmp_path, no_handlers):
    root = tmp_path / "root"
    root.mkdir()
    file = root / "behavior.yaml"
    file.write_text("bundle:\n  name: resource\n")
    seen = []

    def admit(uri, *, base_path):
        seen.append((uri, base_path))
        return ResolvedSource(file, root)

    with source_resolution_policy(admit):
        assert (
            await SimpleSourceResolver(base_path=root).resolve("./behavior.yaml")
        ).active_path == file
    assert seen == [("./behavior.yaml", root)]
    assert not no_handlers


@pytest.mark.asyncio
async def test_registry_cache_disagreement_refused(tmp_path, no_handlers):
    old, new = tmp_path / "old", tmp_path / "new"
    for root in (old, new):
        root.mkdir()
        (root / "bundle.yaml").write_text("bundle:\n  name: cached\n")
    registry = BundleRegistry(tmp_path / "registry", strict=True, persist=False)
    with source_resolution_policy(admission(old)):
        await registry.load(URI)
    with source_resolution_policy(admission(new)):
        with pytest.raises(SourceResolutionDenied):
            await registry.load(URI)
    assert not no_handlers


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", [False, True])
async def test_prepared_reuse_mismatch_and_snapshot(tmp_path, variant):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    resolver = BundleModuleResolver(
        {} if variant else {"provider-test": old},
        source_paths={("provider-test", URI): old} if variant else None,
    )
    snapshot = resolver.prepared_sources()
    assert snapshot == (("provider-test", URI if variant else None, old),)
    with source_resolution_policy(admission(new)):
        with pytest.raises(SourceResolutionDenied):
            resolver.resolve("provider-test", URI)
        with pytest.raises(SourceResolutionDenied):
            await resolver.async_resolve("provider-test", URI)
        with pytest.raises(SourceResolutionDenied):
            BundleModuleSource(old).resolve()
        if not variant:
            with pytest.raises(SourceResolutionDenied):
                resolver.get_module_source("provider-test")


@pytest.mark.asyncio
async def test_policy_nested_isolation_reset_and_ordinary_behavior(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    admit = admission(root)
    other = admission(root)
    assert SOURCE_RESOLUTION_POLICY_VERSION == 1
    with source_resolution_policy(admit):
        with source_resolution_policy(admit):
            assert (await SimpleSourceResolver().resolve(URI)).active_path == root
        with pytest.raises(SourceResolutionDenied):
            with source_resolution_policy(other):
                pass
        with pytest.raises(TypeError):
            with source_resolution_policy(None):
                pass
    assert (await SimpleSourceResolver().resolve(str(root))).active_path == root

    async def task():
        with source_resolution_policy(admit):
            await asyncio.sleep(0)
            return (await SimpleSourceResolver().resolve(URI)).active_path

    active, ordinary = await asyncio.gather(
        task(), SimpleSourceResolver().resolve(str(root))
    )
    assert active == ordinary.active_path == root


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["registry", "activator"])
async def test_actual_consumer_unbound_no_handler_or_cache(
    tmp_path, no_handlers, consumer
):
    root = tmp_path / "approved"
    root.mkdir()
    cache = tmp_path / "cache"
    with source_resolution_policy(admission(root)):
        with pytest.raises(SourceResolutionDenied):
            if consumer == "registry":
                registry = BundleRegistry(
                    tmp_path / "registry", strict=True, persist=False
                )
                await registry.load(URI + "-unbound")
            else:
                await ModuleActivator(cache_dir=cache, install_deps=False).activate(
                    "provider-test", URI + "-unbound"
                )
    assert not cache.exists()
    assert not no_handlers


@pytest.mark.asyncio
async def test_direct_bundle_package_path_refused_before_effects(tmp_path, no_handlers):
    approved, outside = tmp_path / "approved", tmp_path / "outside"
    approved.mkdir()
    outside.mkdir()
    (outside / "pyproject.toml").write_text(
        '[project]\nname="unapproved"\nversion="1"\n'
    )
    with source_resolution_policy(admission(approved)):
        with pytest.raises(SourceResolutionDenied):
            await ModuleActivator(cache_dir=tmp_path / "cache").activate_bundle_package(
                outside
            )
    assert not (tmp_path / "cache").exists()
    assert not no_handlers
