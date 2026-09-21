"""A selected source owns its self-references even when bundle names collide."""

import asyncio
from urllib.parse import parse_qs

import pytest
import yaml

from amplifier_foundation.exceptions import BundleDependencyError, BundleNotFoundError
from amplifier_foundation.paths.resolution import ResolvedSource
from amplifier_foundation.registry import BundleRegistry

OLD = "git+https://github.com/bkrabach/amplifier-bundle-work@main"
NEW = "git+https://github.com/microsoft/amplifier-bundle-work@main"
EXTERNAL = "git+https://github.com/example/shared@selected-ref"


def manifest(root, relative, name, **fields):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_dump({"bundle": {"name": name}, **fields})
    path.write_text("---\n" + data + "---\n" if path.suffix == ".md" else data)
    return path


def work_sources(tmp_path):
    old, new = (tmp_path / "legacy").resolve(), (tmp_path / "current").resolve()
    # Match the old/new Work layouts: the loop moved out of the behavior and
    # into a standalone session bundle, while both roots retained name: work.
    for root, source in [(old, "legacy"), (new, "current")]:
        session = {"context": {"module": "context-managed"}}
        if source == "legacy":
            session["orchestrator"] = {"module": "loop-live"}
        manifest(
            root,
            "behaviors/work-local.yaml",
            "work-local",
            session=session,
            tools=[{"module": "tool-marker", "config": {"source": source}}],
        )
    manifest(old, "bundle.md", "work", includes=["work:behaviors/work-local.yaml"])
    manifest(new, "bundle.md", "work", includes=["work:bundles/work-session.yaml"])
    manifest(
        new,
        "bundles/work-session.yaml",
        "work-session",
        includes=["work:behaviors/work-local.yaml"],
        session={"orchestrator": {"module": "loop-live"}},
    )
    return {OLD: old, NEW: new}


def registry_for(tmp_path, roots, monkeypatch, *, strict=True, **kwargs):
    registry = BundleRegistry(tmp_path / "registry", strict=strict, **kwargs)
    calls = []

    async def resolve(uri):
        calls.append(uri)
        # Yield on every resolution so gather() exercises overlapping loads.
        await asyncio.sleep(0)
        origin, _, fragment = uri.partition("#")
        root = roots[origin]
        relative = parse_qs(fragment).get("subdirectory", ["bundle.md"])[0]
        return ResolvedSource(active_path=root / relative, source_root=root)

    monkeypatch.setattr(registry._source_resolver, "resolve", resolve)
    registry.register(
        {
            "Work": OLD + "#subdirectory=bundle.md",
            "work": NEW + "#subdirectory=bundle.md",
        }
    )
    return registry, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["alias", "uri"])
@pytest.mark.parametrize("order", ["legacy-first", "current-first", "concurrent"])
async def test_work_sources_remain_independent(tmp_path, monkeypatch, selection, order):
    roots = work_sources(tmp_path)
    registry, calls = registry_for(tmp_path, roots, monkeypatch)
    selected = ["Work", "work"] if selection == "alias" else [OLD, NEW]
    if order == "concurrent":
        bundles = await asyncio.gather(*(registry.load(name) for name in selected))
    else:
        indexes = [0, 1] if order == "legacy-first" else [1, 0]
        loaded = {index: await registry.load(selected[index]) for index in indexes}
        bundles = [loaded[0], loaded[1]]
    for bundle, source, origin in zip(bundles, ["legacy", "current"], [OLD, NEW]):
        assert bundle.session["orchestrator"]["module"] == "loop-live"
        assert bundle.tools[0]["config"]["source"] == source
        assert bundle._source_uri.split("#")[0] == origin
        assert bundle.source_base_paths["work"] == roots[origin]
        assert {origin.bundle for origin in bundle.origins["tool:tool-marker"]} == (
            {"work", "work-local"}
            if source == "legacy"
            else {"work", "work-local", "work-session"}
        )
    assert registry.get_state("Work").uri == OLD + "#subdirectory=bundle.md"
    assert registry.get_state("work").uri == NEW + "#subdirectory=bundle.md"
    if selection == "alias":
        assert registry.get_state("Work").includes == ["work-local"]
        assert registry.get_state("work").includes == ["work-session"]


@pytest.mark.asyncio
async def test_self_namespace_does_not_preload_competing_registration(
    tmp_path, monkeypatch
):
    roots = work_sources(tmp_path)
    registry, calls = registry_for(tmp_path, {OLD: roots[OLD]}, monkeypatch)
    bundle = await registry.load("Work")
    assert bundle.session["orchestrator"]["module"] == "loop-live"
    assert all(uri.startswith(OLD) for uri in calls)


@pytest.mark.asyncio
async def test_explicit_override_wins_over_self_binding(tmp_path, monkeypatch):
    roots = work_sources(tmp_path)
    external = (tmp_path / "override").resolve()
    manifest(
        external,
        "bundle.md",
        "override",
        session={"orchestrator": {"module": "loop-override"}},
    )
    roots[EXTERNAL] = external
    registry, calls = registry_for(
        tmp_path,
        roots,
        monkeypatch,
        include_source_resolver=lambda source: (
            EXTERNAL if source == "work:behaviors/work-local.yaml" else None
        ),
    )
    bundle = await registry.load("Work")
    assert bundle.session["orchestrator"]["module"] == "loop-override"
    assert not any(uri.startswith(NEW) for uri in calls)
    assert not any("behaviors/work-local.yaml" in uri for uri in calls)


@pytest.mark.asyncio
async def test_nested_root_and_namespace_root_use_selected_source(
    tmp_path, monkeypatch
):
    roots = work_sources(tmp_path)
    old = roots[OLD]
    path = manifest(
        old,
        "bundles/profile.yaml",
        "profile",
        includes=["work:behaviors/root-tool.yaml", "profile:behaviors/own-tool.yaml"],
    )
    data = yaml.safe_load(path.read_text())
    data["bundle"]["namespace_root"] = ".."
    path.write_text(yaml.safe_dump(data))
    manifest(
        old, "behaviors/root-tool.yaml", "root-tool", tools=[{"module": "tool-root"}]
    )
    manifest(old, "behaviors/own-tool.yaml", "own-tool", tools=[{"module": "tool-own"}])
    registry, calls = registry_for(tmp_path, {OLD: old}, monkeypatch)
    registry.register({"profile": NEW + "#subdirectory=bundles/profile.yaml"})
    selected = OLD + "#subdirectory=bundles/profile.yaml"
    bundle = await registry.load(selected)
    assert {tool["module"] for tool in bundle.tools} == {"tool-root", "tool-own"}
    assert bundle.source_base_paths["profile"] == old
    assert bundle._source_uri == selected
    assert all(uri.startswith(OLD) for uri in calls)
    assert OLD + "#subdirectory=behaviors/own-tool.yaml" in calls


@pytest.mark.asyncio
async def test_external_namespace_keeps_registered_resolution(tmp_path, monkeypatch):
    roots = work_sources(tmp_path)
    external = (tmp_path / "external").resolve()
    manifest(external, "bundle.md", "shared")
    manifest(
        external,
        "behavior.yaml",
        "shared-behavior",
        tools=[{"module": "tool-external"}],
    )
    roots[EXTERNAL] = external
    manifest(
        roots[OLD],
        "bundle.md",
        "work",
        includes=["work:behaviors/work-local.yaml", "shared:behavior.yaml"],
    )
    registry, calls = registry_for(tmp_path, roots, monkeypatch)
    registry.register({"shared": EXTERNAL})
    bundle = await registry.load("Work")
    assert {tool["module"] for tool in bundle.tools} == {"tool-marker", "tool-external"}
    assert EXTERNAL + "#subdirectory=behavior.yaml" in calls


@pytest.mark.asyncio
async def test_external_override_skips_unavailable_registration(tmp_path, monkeypatch):
    roots = work_sources(tmp_path)
    manifest(roots[OLD], "bundle.md", "work", includes=["shared:behavior.yaml"])
    registry, calls = registry_for(
        tmp_path,
        roots,
        monkeypatch,
        include_source_resolver=lambda source: (
            OLD + "#subdirectory=behaviors/work-local.yaml"
            if source == "shared:behavior.yaml"
            else None
        ),
    )
    registry.register(
        {"shared": EXTERNAL}
    )  # unavailable; the override is authoritative
    bundle = await registry.load("Work")
    assert bundle.session["orchestrator"]["module"] == "loop-live"
    assert not any(uri.startswith(EXTERNAL) for uri in calls)


@pytest.mark.asyncio
async def test_local_file_alias_keeps_own_includes(tmp_path):
    roots = work_sources(tmp_path)
    registry = BundleRegistry(tmp_path / "registry", strict=True)
    registry.register(
        {
            "Work": (roots[OLD] / "bundle.md").as_uri(),
            "work": (roots[NEW] / "bundle.md").as_uri(),
        }
    )
    bundle = await registry.load("Work")
    assert bundle.session["orchestrator"]["module"] == "loop-live"
    assert bundle.tools[0]["config"]["source"] == "legacy"
    assert bundle.source_base_paths["work"] == roots[OLD]


@pytest.mark.asyncio
async def test_selected_revision_is_retained_for_self_includes(tmp_path, monkeypatch):
    roots = work_sources(tmp_path)
    revision = OLD.replace("@main", "@selected-ref")
    roots[revision] = roots.pop(OLD)
    registry, calls = registry_for(tmp_path, roots, monkeypatch)
    registry.register({"Work": revision + "#subdirectory=bundle.md"})
    bundle = await registry.load("Work")
    assert bundle.session["orchestrator"]["module"] == "loop-live"
    assert all(uri.startswith(revision) for uri in calls)
    assert revision + "#subdirectory=behaviors/work-local.yaml" in calls


@pytest.mark.asyncio
async def test_missing_self_include_does_not_fall_back_to_other_source(
    tmp_path, monkeypatch
):
    roots = work_sources(tmp_path)
    (roots[OLD] / "behaviors/work-local.yaml").unlink()
    registry, calls = registry_for(tmp_path, roots, monkeypatch)
    with pytest.raises(BundleDependencyError, match="Include resolution failed"):
        await registry.load("Work")
    assert not any(uri.startswith(NEW) for uri in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("strict", [False, True])
async def test_override_not_found_keeps_strictness_policy(
    tmp_path, monkeypatch, strict
):
    roots = work_sources(tmp_path)

    def unavailable(source):
        raise BundleNotFoundError("Unavailable explicit override")

    registry, calls = registry_for(
        tmp_path, roots, monkeypatch, strict=strict, include_source_resolver=unavailable
    )
    if strict:
        with pytest.raises(BundleDependencyError, match="Include not found"):
            await registry.load("Work")
    else:
        bundle = await registry.load("Work")
        assert bundle.tools == []
    assert not any(uri.startswith(NEW) for uri in calls)
