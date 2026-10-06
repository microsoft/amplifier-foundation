"""Real registry composition and resources of the nested-only host roots."""
import io
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from amplifier_foundation import Bundle, BundleRegistry
from amplifier_foundation.bundle._prepared import PreparedBundle, BundleModuleResolver
from amplifier_foundation.configurator._inspector import (
    _reset_cycle_warnings_for_testing,
    walk_include_chains,
)
from amplifier_foundation.mentions import BaseMentionResolver
from amplifier_foundation.modules.activator import ModuleActivator
from amplifier_foundation.paths.resolution import ResolvedSource, parse_uri

ROOT = Path(__file__).resolve().parents[1]
FOUNDATION = "git+https://github.com/microsoft/amplifier-foundation@main"
ADDED_AGENTS = {
    "amp-dev:amplifier-dev-expert", "amplifier-tester:setup-digital-twin",
    "amplifier-tester:validator", "digital-twin-universe:dtu-profile-builder",
}


def read_utf8(path):
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def registry_home(tmp_path_factory):
    """Share downloaded sources, never registrations or loaded bundle state."""
    return tmp_path_factory.mktemp("nested-bundle-registry")


def registry(home):
    def candidate(source):
        prefix = FOUNDATION + "#subdirectory="
        if source.startswith(prefix):
            return str(ROOT / source[len(prefix):])
        return None
    return BundleRegistry(home=home, persist=False, read_persisted=False,
                          strict=True, include_source_resolver=candidate)


def resolver(bundle):
    prepared = PreparedBundle({}, BundleModuleResolver(module_paths={}), bundle)
    return BaseMentionResolver(bundles=prepared._build_bundles_for_resolver(bundle))


def entry(name, form):
    path = ROOT / "bundles" / name
    if "manifest" in form:
        path /= "bundle.md"
    if form.startswith("root-"):
        return ROOT.as_uri() + "#subdirectory=" + path.relative_to(ROOT).as_posix()
    return path.as_uri() if form.endswith("-uri") else str(path)


def assert_cycle_free(loader):
    states = {name: loader.get_state(name) for name in loader.list_registered()}
    for name, state in states.items():
        assert name not in (state.includes or []), name
        assert name not in (state.included_by or []), name

    def visit(name, chain):
        assert name not in chain, f"Registry cycle: {chain + (name,)}"
        for child in states[name].includes or []:
            if child in states:
                visit(child, chain + (name,))

    for name in states:
        visit(name, ())
    # Exercise the production configurator traversal (also used by bundle show),
    # resetting warning deduplication so an earlier test cannot hide a cycle.
    _reset_cycle_warnings_for_testing()
    for name in states:
        chains = walk_include_chains(name, states)
        assert chains, name
        for chain in chains:
            names = [step.bundle for step in chain]
            assert names[-1] == name
            assert len(names) == len(set(names)), names


async def prompt(bundle, cwd, monkeypatch):
    """Prepare context and exercise the prompt factory, not runtime activation."""
    monkeypatch.setattr(ModuleActivator, "activate_all", AsyncMock(return_value={}))
    monkeypatch.setattr(ModuleActivator, "finalize", lambda self: None)
    prepared = await bundle.prepare(install_deps=False)
    # create_session resolves these before wiring its prompt factory.
    prepared.bundle.resolve_pending_context()
    session = MagicMock()
    session.coordinator.hooks.emit = AsyncMock()
    return await prepared.create_system_prompt_factory(session, session_cwd=cwd)()


@pytest.mark.asyncio
@pytest.mark.parametrize("eol", [b"\n", b"\r\n"], ids=["LF", "CRLF"])
async def test_prompt_oracle_uses_utf8_with_cp1252_default(tmp_path, monkeypatch, eol):
    """The shared expected reader must match runtime UTF-8, not the locale."""
    system = tmp_path / "system.md"
    ecosystem = tmp_path / "ecosystem.md"
    system_text = "Host system\n"
    ecosystem_text = "# Ecosystem\ncore → foundation → modules → bundles → apps\n"
    for path, text in ((system, system_text), (ecosystem, ecosystem_text)):
        path.write_bytes(text.encode("utf-8").replace(b"\n", eol))

    bundle = Bundle(
        name="fixture", base_path=tmp_path, instruction="@fixture:system.md",
        source_base_paths={"fixture": tmp_path},
        _pending_context={
            "fixture:ecosystem.md": "fixture:ecosystem.md",
            "fixture:system.md": "fixture:system.md",
        },
    )
    bundle.resolve_pending_context()
    assert bundle.context == {
        "fixture:ecosystem.md": ecosystem, "fixture:system.md": system,
    }
    prepared = PreparedBundle({}, BundleModuleResolver(module_paths={}), bundle)
    session = MagicMock()
    session.coordinator.hooks.emit = AsyncMock()
    with monkeypatch.context() as locale_default:
        # Simulate a legacy default even when the interpreter enables UTF-8 mode.
        # Explicit codecs, including the runtime's UTF-8 reads, stay unchanged.
        locale_default.setattr(io, "text_encoding", lambda encoding, stacklevel=2:
                               "cp1252" if encoding is None else encoding)
        head = await prepared.create_system_prompt_factory(session, session_cwd=tmp_path)()
        expected_system = read_utf8(system)
        expected_ecosystem = read_utf8(ecosystem)
        assert head.count(expected_system) == 1
        assert head.count(expected_ecosystem) == 1
        assert head.index(expected_system) < head.index(expected_ecosystem)
        assert (expected_system, expected_ecosystem) == (system_text, ecosystem_text)
        implicit_ecosystem = ecosystem.read_text()
        assert implicit_ecosystem != ecosystem_text
        assert head.count(implicit_ecosystem) == 0


@pytest.mark.asyncio
async def test_behavior_is_portable_and_preserves_both_host_runtimes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    behavior = await registry(tmp_path / "registry").load(str(ROOT / "behaviors/amp-dev.yaml"))
    host = Bundle(name="host", session={
        "orchestrator": {"module": "host-loop", "config": {"keep": True}},
        "context": {"module": "host-context", "config": {"keep": True}}},
        providers=[{"module": "provider-test", "config": {"default_model": "chosen"}}],
        tools=[{"module": "host-tool"}], instruction="host instruction")
    combined = host.compose(behavior)
    assert not behavior.session and not behavior.providers and not behavior.instruction
    assert combined.session == host.session
    assert combined.providers == host.providers
    assert combined.instruction == host.instruction
    assert "host-tool" in {row["module"] for row in combined.tools}
    assert not any(key.startswith("anchors:") for key in behavior.agents)
    assert set(behavior.agents) == ADDED_AGENTS
    resources = resolver(behavior)
    assert resources.resolve("@amp-dev:agents/amplifier-dev-expert.md") == ROOT / "agents/amplifier-dev-expert.md"
    assert resources.resolve("@foundation:context/amplifier-dev/ecosystem-map.md") == ROOT / "context/amplifier-dev/ecosystem-map.md"
    assert resources.resolve("@foundation:bundles/anchors/context/agent-baseline.md") == ROOT / "bundles/anchors/context/agent-baseline.md"
    assert resources.resolve("@amp-dev:context/amplifier-dev/amplifier-ecosystem.md") == ROOT / "context/amplifier-dev/amplifier-ecosystem.md"
    expert = behavior.agents["amp-dev:amplifier-dev-expert"]
    assert "@anchors:" not in read_utf8(ROOT / "agents/amplifier-dev-expert.md")
    assert "provider_preferences" not in expert
    skills = next(row for row in behavior.tools if row["module"] == "tool-skills")["config"]["skills"]
    assert any("amplifier-bundle-gitea@" in path for path in skills)
    assert any("amplifier-bundle-digital-twin-universe@" in path for path in skills)
    head = await prompt(combined, tmp_path, monkeypatch)
    assert head.startswith(host.instruction)
    assert head.count(read_utf8(ROOT / "context/amplifier-dev/amplifier-ecosystem.md")) == 1
    assert read_utf8(ROOT / "bundles/anchors/context/system.md") not in head


@pytest.mark.asyncio
@pytest.mark.parametrize("form", [
    "directory", "manifest", "directory-uri", "manifest-uri",
    "root-directory-uri", "root-manifest-uri",
])
@pytest.mark.parametrize("order", [
    ("anchors", "anchors-amp-dev"), ("anchors-amp-dev", "anchors"),
])
@pytest.mark.parametrize("custom_aliases", [False, True], ids=["canonical", "custom-aliases"])
async def test_nested_roots_compose_source_local_without_cycles(
    tmp_path, monkeypatch, registry_home, caplog, form, order, custom_aliases
):
    monkeypatch.chdir(tmp_path)
    caplog.set_level(logging.WARNING)
    loader = registry(registry_home)
    assert loader.list_registered() == []
    names = {name: f"custom-{name}" if custom_aliases else name for name in order}
    loader.register({names[name]: entry(name, form) for name in order})
    assert loader.find("foundation") is None
    loaded = {}
    for name in order:
        loaded[name] = await loader.load(names[name])
        assert_cycle_free(loader)
    anchors = loaded["anchors"]
    amp_dev = loaded["anchors-amp-dev"]
    behavior = await loader.load(str(ROOT / "behaviors/amp-dev.yaml"))
    assert anchors.name == "anchors" and anchors.display_name == "Anchors"
    assert amp_dev.name == "anchors-amp-dev"
    assert amp_dev.display_name == "Anchors · Amplifier development"
    assert amp_dev.source_base_paths["anchors-amp-dev"] == ROOT / "bundles/anchors-amp-dev"
    assert anchors.version == amp_dev.version == "0.3.0"
    assert amp_dev.to_mount_plan() == anchors.compose(behavior).to_mount_plan()
    assert anchors.session == amp_dev.session
    assert anchors.hooks == amp_dev.hooks
    assert anchors.providers == amp_dev.providers == []
    assert set(amp_dev.agents) - set(anchors.agents) == ADDED_AGENTS
    assert all(amp_dev.agents[name] == agent for name, agent in anchors.agents.items())
    for bundle in (anchors, amp_dev, behavior):
        bundle.resolve_pending_context()
    assert amp_dev.context == {**anchors.context, **behavior.context}
    assert anchors.instruction == amp_dev.instruction == "@anchors:context/system.md"
    assert {row["module"] for row in anchors.tools} == {row["module"] for row in amp_dev.tools}
    assert [row for row in anchors.tools if row["module"] != "tool-skills"] == [
        row for row in amp_dev.tools if row["module"] != "tool-skills"]
    skills = next(row for row in amp_dev.tools if row["module"] == "tool-skills")["config"]["skills"]
    assert skills == [
        "git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main#subdirectory=skills/context-intelligence-session-navigation",
        "git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main#subdirectory=skills/transcript",
        "@routing-matrix:skills",
        "git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=skills",
        "git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=skills",
        "git+https://github.com/microsoft/amplifier-bundle-gitea@main#subdirectory=skills",
        "git+https://github.com/microsoft/amplifier-bundle-digital-twin-universe@main#subdirectory=skills"]
    assert resolver(amp_dev).resolve("@anchors:context/system.md") == ROOT / "bundles/anchors/context/system.md"
    assert resolver(anchors).resolve("@anchors:agents/builder.md") == ROOT / "bundles/anchors/agents/builder.md"
    assert resolver(amp_dev).resolve("@amp-dev:agents/amplifier-dev-expert.md") == ROOT / "agents/amplifier-dev-expert.md"
    for reference in (
        "docs/BUNDLE_GUIDE.md", "docs/AGENT_AUTHORING.md",
        "context/shared/description-authoring-principles.md",
        "context/amplifier-dev/ecosystem-map.md",
        "context/amplifier-dev/dev-workflows.md",
        "context/amplifier-dev/testing-patterns.md",
        "bundles/anchors/context/agent-baseline.md",
    ):
        assert resolver(amp_dev).resolve(f"@foundation:{reference}") == ROOT / reference
    assert not any(name.startswith("foundation:") for name in amp_dev.agents)
    assert str(ROOT) not in loader._loaded_bundles and ROOT.as_uri() not in loader._loaded_bundles
    system = read_utf8(ROOT / "bundles/anchors/context/system.md")
    ecosystem = read_utf8(ROOT / "context/amplifier-dev/amplifier-ecosystem.md")
    head = await prompt(amp_dev, tmp_path, monkeypatch)
    assert head.count(system) == 1
    assert head.count(ecosystem) == 1
    assert head.index(system) < head.index(ecosystem)
    assert read_utf8(ROOT / "bundles/anchors/context/agent-baseline.md") not in head
    assert not caplog.records, [(row.name, row.getMessage()) for row in caplog.records]


@pytest.mark.asyncio
@pytest.mark.parametrize("manifest_path", [
    "bundles/anchors-amp-dev", "bundles/anchors-amp-dev/bundle.md",
])
@pytest.mark.parametrize("ref", ["main", "qualified-candidate"])
async def test_relative_includes_normalize_children_and_preserve_selected_git_ref(
    tmp_path, monkeypatch, registry_home, caplog, manifest_path, ref
):
    """Use a local checkout binding, without fetching the synthetic selected ref."""
    monkeypatch.chdir(tmp_path)
    caplog.set_level(logging.WARNING)
    loader = registry(registry_home)
    selected = FOUNDATION.rsplit("@", 1)[0] + "@" + ref
    resolve = loader._source_resolver.resolve

    async def candidate_checkout(uri):
        if uri.startswith(selected + "#subdirectory="):
            parsed = parse_uri(uri)
            return ResolvedSource(active_path=ROOT / parsed.subpath, source_root=ROOT)
        return await resolve(uri)

    monkeypatch.setattr(loader._source_resolver, "resolve", candidate_checkout)
    root_uri = selected + "#subdirectory=" + manifest_path
    variant = await loader.load(root_uri)
    assert variant._source_uri == root_uri
    assert variant.source_base_paths["anchors-amp-dev"] == ROOT / "bundles/anchors-amp-dev"
    for name, path in (
        ("anchors", "bundles/anchors/bundle.md"),
        ("amp-dev", "behaviors/amp-dev.yaml"),
    ):
        expected = selected + "#subdirectory=" + path
        child = loader._loaded_bundles[expected]
        assert loader.get_state(name).uri == child._source_uri == expected
        assert parse_uri(child._source_uri).ref == ref
        assert loader._resolved_sources[expected].active_path == ROOT / path
        assert loader._resolved_sources[expected].source_root == ROOT
    assert_cycle_free(loader)
    assert not caplog.records, [(row.name, row.getMessage()) for row in caplog.records]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["anchors", "anchors-amp-dev"])
@pytest.mark.parametrize("custom_alias", [False, True], ids=["canonical", "custom-alias"])
async def test_nested_reload_repairs_stale_self_edges_and_preserves_unrelated_relations(
    tmp_path, monkeypatch, registry_home, caplog, name, custom_alias
):
    monkeypatch.chdir(tmp_path)
    caplog.set_level(logging.WARNING)
    loader = registry(registry_home)
    registered_name = f"custom-{name}" if custom_alias else name
    loader.register({
        registered_name: entry(name, "manifest-uri"),
        "unrelated-child": str(tmp_path / "unrelated-child"),
        "unrelated-parent": str(tmp_path / "unrelated-parent"),
    })
    state = loader.get_state(registered_name)
    state.includes = [registered_name, "unrelated-child"]
    state.included_by = [registered_name, "unrelated-parent"]
    loader.get_state("unrelated-child").included_by = [registered_name, "unrelated-parent"]
    loader.get_state("unrelated-parent").includes = [registered_name, "unrelated-child"]

    await loader.load(registered_name)
    await loader._load_single(registered_name, refresh=True)

    assert state.includes and registered_name not in state.includes
    assert "unrelated-child" not in state.includes
    assert state.included_by == ["unrelated-parent"]
    assert loader.get_state("unrelated-child").included_by == ["unrelated-parent"]
    assert loader.get_state("unrelated-parent").includes == [registered_name, "unrelated-child"]
    if name == "anchors-amp-dev":
        assert state.includes == ["anchors", "amp-dev"]
    assert_cycle_free(loader)
    assert not caplog.records, [(row.name, row.getMessage()) for row in caplog.records]


def test_behavior_contains_entire_delta_without_root_inclusion():
    behavior = yaml.safe_load(read_utf8(ROOT / "behaviors/amp-dev.yaml"))
    assert not {"session", "providers"} & behavior.keys()
    assert behavior["includes"] == [{"bundle":
        "git+https://github.com/microsoft/amplifier-bundle-amplifier-tester@main#subdirectory=behaviors/amplifier-tester.yaml"}]
    assert behavior["agents"]["include"] == ["amp-dev:amplifier-dev-expert"]
    assert behavior["context"]["include"] == ["amp-dev:context/amplifier-dev/amplifier-ecosystem.md"]