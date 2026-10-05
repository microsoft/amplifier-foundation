"""Portable local resource roots must not depend on HOME path ordering."""
from unittest.mock import patch

import pytest
from amplifier_foundation import BundleRegistry
from amplifier_foundation.bundle._prepared import PreparedBundle, BundleModuleResolver
from amplifier_foundation.mentions import BaseMentionResolver
from amplifier_foundation.sources.file import FileSourceHandler
from tests.test_eager_mention_resolver_capability import _FakeSession


def fixture(root, declared=True, containing="different"):
    (root / "behaviors").mkdir(parents=True)
    for folder in ("skills", "context", "agents"):
        (root / folder).mkdir()
    (root / "context/info.md").write_text("package context")
    (root / "agents/worker.md").write_text("---\nmeta:\n  description: fixture\n---\nworker")
    if containing != "absent":
        name = "library" if containing == "same" else "package"
        (root / "bundle.yaml").write_text(f"bundle:\n  name: {name}\n  version: 1.0.0\n")
    path = root / "behaviors/skills.yaml"
    path.write_text("bundle:\n  name: library\n  version: 1.0.0\n" +
                    ("  namespace_root: ..\n" if declared else ""))
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize("home_name", ["a-home", "z-home"])
@pytest.mark.parametrize("source_kind", ["path", "file", "fragment"])
@pytest.mark.parametrize("containing", ["same", "different", "absent"])
@pytest.mark.parametrize("declared", [True, False])
async def test_resource_roots_outside_home(tmp_path, monkeypatch, home_name,
                                         source_kind, containing, declared):
    root = (tmp_path / "m-package").resolve()
    path = fixture(root, declared, containing)
    home = tmp_path / home_name; home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    uri = str(path) if source_kind == "path" else path.as_uri()
    if source_kind == "fragment":
        uri = root.as_uri() + "#subdirectory=behaviors/skills.yaml"
    bundle = await BundleRegistry(home=tmp_path / "registry", persist=False,
                                  read_persisted=False, strict=True).load(uri)
    prepared = PreparedBundle({}, BundleModuleResolver(module_paths={}), bundle)
    resolver = BaseMentionResolver(bundles=prepared._build_bundles_for_resolver(bundle))
    # Same-name containing root retains its existing root binding when undeclared.
    expected = root if declared or containing == "same" else path.parent
    assert resolver.bundles["library"].base_path == expected
    assert resolver.resolve("@library:skills") == (root / "skills" if expected == root else None)
    if declared:
        assert resolver.resolve("@library:context/info.md") == root / "context/info.md"
        assert resolver.resolve("@library:agents/worker.md") == root / "agents/worker.md"


def test_search_boundaries_are_ancestry_not_order(tmp_path, monkeypatch):
    home=tmp_path/"home";home.mkdir();monkeypatch.setenv("HOME",str(home))
    root=tmp_path/"z-decoy";root.mkdir();(root/"bundle.yaml").write_text("bundle: {}")
    registry=BundleRegistry(home=home/"registry",persist=False,read_persisted=False)
    assert registry._find_nearest_bundle_file(root, home) is None
    inside=home/"nested";inside.mkdir();(tmp_path/"bundle.yaml").write_text("bundle: {}")
    assert FileSourceHandler()._find_bundle_root(inside) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("compose", [True, False])
async def test_declared_roots_visible_before_root_and_delegated_mount(tmp_path, monkeypatch, compose):
    root=(tmp_path/"package").resolve();path=fixture(root, containing="absent")
    home=tmp_path/"isolated-home";home.mkdir();monkeypatch.setenv("HOME",str(home))
    bundle=await BundleRegistry(home=home/"registry",persist=False,read_persisted=False).load(str(path))
    prepared=PreparedBundle({},BundleModuleResolver(module_paths={}),bundle)
    session=_FakeSession()
    with patch("amplifier_core.AmplifierSession",return_value=session):
        await prepared.create_session()
    assert session.observed_resolver_during_init.resolve("@library:skills") == root/"skills"
    child=_FakeSession()
    with patch("amplifier_core.AmplifierSession",return_value=child):
        await prepared.spawn(bundle,"fixture instruction",compose=compose)
    assert child.observed_resolver_during_init.resolve("@library:skills") == root/"skills"


@pytest.mark.asyncio
@pytest.mark.parametrize("source_kind", ["file", "git", "zip"])
async def test_admitted_source_keeps_declared_resource_root(tmp_path, monkeypatch, source_kind):
    from amplifier_foundation.sources import source_resolution_policy, SourceResolutionDenied
    from amplifier_foundation.paths.resolution import ResolvedSource
    root=(tmp_path/"package").resolve();path=fixture(root, containing="absent")
    home=tmp_path/"z-home";home.mkdir();monkeypatch.setenv("HOME",str(home))
    uri={"file":path.as_uri(),"git":"git+https://example.test/library@main#subdirectory=behaviors/skills.yaml",
         "zip":"zip+https://example.test/library.zip#subdirectory=behaviors/skills.yaml"}[source_kind]
    def admit(requested, *, base_path):
        if requested != uri: raise SourceResolutionDenied("unqualified fixture")
        return ResolvedSource(active_path=path, source_root=root)
    registry=BundleRegistry(home=home/"registry",persist=False,read_persisted=False)
    with source_resolution_policy(admit):
        bundle=await registry.load(uri)
        assert bundle.source_base_paths["library"] == root
        with pytest.raises(SourceResolutionDenied):
            await registry.load("git+https://example.test/unqualified@main")
