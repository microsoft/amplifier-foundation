"""Session source selections must never become shared registrations."""

import asyncio

import pytest
import yaml

from amplifier_foundation.exceptions import BundleDependencyError
from amplifier_foundation.registry import BundleRegistry


def bundle(root, name="shared", *, broken=False):
    root.mkdir()
    child = root / "child.yaml"
    child.write_text(yaml.safe_dump({
        "bundle": {"name": "shared-child"},
        "tools": [{"module": "tool-marker", "config": {"value": root.name}}],
    }))
    (root / "bundle.yaml").write_text(yaml.safe_dump({
        "bundle": {"name": name},
        "includes": ["shared:child.yaml", *([str(root / "missing.yaml")] if broken else [])],
    }))
    return root.as_uri()


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent", [False, True])
async def test_scoped_aliases_and_discovered_state_never_escape(tmp_path, concurrent):
    original = bundle(tmp_path / "original")
    variants = [bundle(tmp_path / label) for label in ("first", "second")]
    home = tmp_path / "registry"
    global_registry = BundleRegistry(home, strict=True)
    global_registry.register({"shared": original})
    await global_registry.load("shared")
    before = (home / "registry.json").read_bytes()

    async def load(uri):
        scoped = BundleRegistry(home, strict=True, persist=False)
        scoped.register({"shared": uri, original: uri})
        await asyncio.sleep(0)
        loaded = await scoped.load(original)
        scoped.save()  # A host's final save must not bypass isolation.
        return loaded

    loaded = (await asyncio.gather(*(load(uri) for uri in variants)) if concurrent
              else [await load(uri) for uri in variants])
    assert [row.tools[0]["config"]["value"] for row in loaded] == ["first", "second"]
    assert (home / "registry.json").read_bytes() == before
    fresh = BundleRegistry(home, strict=True, persist=False)
    assert fresh.find(original) is None
    assert fresh.find("shared") == original
    assert (await fresh.load(original)).tools[0]["config"]["value"] == "original"
    assert (home / "registry.json").read_bytes() == before
    assert global_registry.find("shared") == original


@pytest.mark.asyncio
async def test_failed_composition_keeps_registry_bytes(tmp_path):
    original = bundle(tmp_path / "original")
    broken = bundle(tmp_path / "broken", broken=True)
    home = tmp_path / "registry"
    registry = BundleRegistry(home)
    registry.register({"shared": original})
    registry.save()
    before = (home / "registry.json").read_bytes()
    scoped = BundleRegistry(home, persist=False, strict=True)
    scoped.register({original: broken})
    with pytest.raises(BundleDependencyError):
        await scoped.load(original)
    scoped.save()
    assert (home / "registry.json").read_bytes() == before


def test_stale_path_cleanup_is_local_and_no_file_is_created(tmp_path):
    home = tmp_path / "registry"
    registry = BundleRegistry(home)
    registry.register({"missing": "file:///not-present"})
    registry.get_state("missing").local_path = str(tmp_path / "absent")
    registry.save()
    before = (home / "registry.json").read_bytes()
    scoped = BundleRegistry(home, persist=False)
    assert scoped.get_state("missing").local_path is None
    scoped.unregister("missing")
    scoped.save()
    assert (home / "registry.json").read_bytes() == before
    empty = tmp_path / "empty"
    scoped = BundleRegistry(empty, persist=False)
    scoped.register({"temporary": "file:///temporary"})
    scoped.save()
    assert not (empty / "registry.json").exists()


@pytest.mark.asyncio
async def test_explicit_global_registration_and_update_still_persist(tmp_path):
    old = bundle(tmp_path / "old")
    new = bundle(tmp_path / "new")
    home = tmp_path / "registry"
    registry = BundleRegistry(home)
    registry.register({"shared": old})
    registry.save()
    assert BundleRegistry(home).find("shared") == old
    registry.register({"shared": new})
    await registry.load("shared")
    restored = BundleRegistry(home)
    assert restored.find("shared") == new
    assert restored.get_state("shared").includes == ["shared-child"]
    assert restored.get_state("shared-child").uri == (tmp_path / "new/child.yaml").as_uri()
