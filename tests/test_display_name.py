"""Display labels survive loading and caches without changing bundle identity."""

import pytest

from amplifier_foundation.bundle import Bundle
from amplifier_foundation.cache.disk import DiskCache
from amplifier_foundation.exceptions import BundleValidationError
from amplifier_foundation.registry import BundleRegistry, BundleState


def test_optional_metadata_and_positional_compatibility(tmp_path):
    legacy = Bundle("stable-id", "2.0", "Description", ["included"])
    assert legacy.display_name is None
    assert legacy.includes == ["included"]
    labeled = Bundle.from_dict(
        {
            "bundle": {
                "name": "stable-id",
                "display_name": "Friendly label",
            }
        }
    )
    assert labeled.name == "stable-id"
    assert labeled.display_name == "Friendly label"
    assert labeled.to_mount_plan() == Bundle(name="stable-id").to_mount_plan()
    cache = DiskCache(tmp_path)
    cache.set("bundle", labeled)
    assert cache.get("bundle").display_name == "Friendly label"
    assert BundleState.from_dict("old", {"uri": "file:///old"}).display_name is None


@pytest.mark.parametrize("value", ["", "  ", 42, {}, ["label"]])
def test_invalid_display_name(value):
    with pytest.raises(BundleValidationError, match="display_name"):
        Bundle.from_dict({"bundle": {"name": "test", "display_name": value}})


def test_label_follows_named_bundle_without_leaking_from_includes():
    base = Bundle(name="base", display_name="Base")
    assert base.compose().display_name == "Base"
    assert base.compose(Bundle(name="root")).display_name is None
    assert base.compose(Bundle(name="root", display_name="Root")).display_name == "Root"
    assert (
        base.compose(Bundle(name="", tools=[{"module": "tool-test"}])).display_name
        == "Base"
    )


@pytest.mark.asyncio
async def test_registry_alias_composition_persistence_and_source_change(tmp_path):
    child = tmp_path / "child.yaml"
    child.write_text("bundle:\n  name: child\n  display_name: Child label\n")
    root = tmp_path / "bundle.yaml"
    root.write_text(
        f"bundle:\n  name: root\n  display_name: Root label\nincludes:\n  - bundle: {child.as_uri()}\n"
    )
    registry = BundleRegistry(home=tmp_path / "home")
    registry.register({"alias": root.as_uri()})
    loaded = await registry.load("alias")
    assert loaded.name == "root" and loaded.display_name == "Root label"
    registry.save()
    restored = BundleRegistry(home=registry.home)
    assert restored._registry["alias"].display_name == "Root label"
    assert restored._registry["root"].display_name == "Root label"
    assert restored._registry["child"].display_name == "Child label"
    restored.register({"alias": child.as_uri()})
    assert restored._registry["alias"].display_name is None
    assert restored._registry["alias"].local_path is None
    assert (await restored.load("alias")).display_name == "Child label"
