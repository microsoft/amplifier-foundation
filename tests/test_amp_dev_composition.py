"""Real registry composition of the portable capability and flat host roots."""
from pathlib import Path

import pytest
import yaml

from amplifier_foundation import Bundle, BundleRegistry
from amplifier_foundation.bundle._prepared import PreparedBundle, BundleModuleResolver
from amplifier_foundation.mentions import BaseMentionResolver

ROOT = Path(__file__).resolve().parents[1]
FOUNDATION = "git+https://github.com/microsoft/amplifier-foundation@main"


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
    assert set(behavior.agents) == {
        "amp-dev:amplifier-dev-expert", "amplifier-tester:setup-digital-twin",
        "amplifier-tester:validator", "digital-twin-universe:dtu-profile-builder"}
    resources = resolver(behavior)
    assert resources.resolve("@amp-dev:agents/amplifier-dev-expert.md") == ROOT / "agents/amplifier-dev-expert.md"
    assert resources.resolve("@foundation:context/amplifier-dev/ecosystem-map.md") == ROOT / "context/amplifier-dev/ecosystem-map.md"
    assert resources.resolve("@foundation:bundles/anchors/context/agent-baseline.md") == ROOT / "bundles/anchors/context/agent-baseline.md"
    assert resources.resolve("@amp-dev:context/amplifier-dev/amplifier-ecosystem.md") == ROOT / "context/amplifier-dev/amplifier-ecosystem.md"
    expert = behavior.agents["amp-dev:amplifier-dev-expert"]
    assert "@anchors:" not in (ROOT / "agents/amplifier-dev-expert.md").read_text()
    assert "provider_preferences" not in expert
    skills = next(row for row in behavior.tools if row["module"] == "tool-skills")["config"]["skills"]
    assert any("amplifier-bundle-gitea@" in path for path in skills)
    assert any("amplifier-bundle-digital-twin-universe@" in path for path in skills)


@pytest.mark.asyncio
async def test_flat_roots_and_compatibility_entrypoints_resolve_equally(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    loader = registry(tmp_path / "registry")
    anchors = await loader.load(str(ROOT / "bundles/anchors.md"))
    amp_dev = await loader.load(str(ROOT / "bundles/anchors-amp-dev.md"))
    assert anchors.session == amp_dev.session
    assert anchors.hooks == amp_dev.hooks
    assert set(amp_dev.agents) - set(anchors.agents) == {
        "amp-dev:amplifier-dev-expert", "amplifier-tester:setup-digital-twin",
        "amplifier-tester:validator", "digital-twin-universe:dtu-profile-builder"}
    assert anchors.instruction == amp_dev.instruction
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
    for name, expected in (("anchors", anchors), ("anchors-amp-dev", amp_dev)):
        old = await loader.load(str(ROOT / f"bundles/{name}/bundle.md"))
        assert old.to_mount_plan() == expected.to_mount_plan()
        assert old.instruction == expected.instruction
        assert resolver(old).resolve("@anchors:context/system.md") == ROOT / "bundles/anchors/context/system.md"


def test_behavior_contains_entire_delta_without_root_inclusion():
    behavior = yaml.safe_load((ROOT / "behaviors/amp-dev.yaml").read_text())
    assert not {"session", "providers"} & behavior.keys()
    assert behavior["includes"] == [{"bundle":
        "git+https://github.com/microsoft/amplifier-bundle-amplifier-tester@main#subdirectory=behaviors/amplifier-tester.yaml"}]
    assert behavior["agents"]["include"] == ["amp-dev:amplifier-dev-expert"]
    assert behavior["context"]["include"] == ["amp-dev:context/amplifier-dev/amplifier-ecosystem.md"]