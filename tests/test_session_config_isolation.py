"""Runtime defaults must not contaminate reusable bundle configuration."""

from copy import deepcopy

import pytest
from amplifier_core import AmplifierSession

from amplifier_foundation.bundle import Bundle
from amplifier_foundation.bundle._prepared import BundleModuleResolver, PreparedBundle


@pytest.mark.parametrize(
    "section", ["session", "providers", "tools", "hooks", "agents", "spawn"]
)
def test_mount_plan_mutation_cannot_change_future_child_config(section):
    config = {"paths": ["declared"], "options": {"mode": "declared"}}
    value = {"config": config}
    if section in ("providers", "tools", "hooks"):
        value = [{"module": "example", "config": config}]
    bundle = Bundle(name="parent", **{section: value})
    before = deepcopy(value)

    plan = bundle.to_mount_plan()
    runtime = plan[section][0] if isinstance(plan[section], list) else plan[section]
    runtime["config"]["working_dir"] = "/parent"
    runtime["config"]["paths"].append("runtime")
    runtime["config"]["options"]["mode"] = "runtime"

    assert getattr(bundle, section) == before
    child_plan = bundle.compose(Bundle(name="child")).to_mount_plan()
    assert child_plan[section] == before


@pytest.mark.asyncio
async def test_reused_prepared_bundle_isolates_mount_defaults_and_runtime_mutations(
    tmp_path, monkeypatch
):
    bundle = Bundle(
        name="shared",
        session={
            "orchestrator": {"module": "loop-test"},
            "context": {"module": "context-test"},
        },
        tools=[{"module": "tool-test", "config": {"paths": ["declared"]}}],
        hooks=[
            {
                "module": "hooks-logging",
                "config": {"additional_events": ["custom:event"]},
            }
        ],
    )
    prepared = PreparedBundle(
        bundle=bundle,
        mount_plan=bundle.to_mount_plan(),
        resolver=BundleModuleResolver(module_paths={}),
    )
    before = deepcopy(prepared.mount_plan)

    async def initialize_with_mutating_module(session):
        # Use the real Core session/coordinator, with a simulated module mount
        # to exercise Foundation's ownership boundary without loading providers.
        config = session.config["tools"][0]["config"]
        config.setdefault(
            "working_dir", session.coordinator.get_capability("session.working_dir")
        )
        config["paths"].append(session.session_id)

    monkeypatch.setattr(AmplifierSession, "initialize", initialize_with_mutating_module)
    parent = await prepared.create_session(
        session_id="parent", session_cwd=tmp_path / "parent"
    )
    child = await prepared.create_session(
        session_id="child", session_cwd=tmp_path / "child"
    )

    for session, name in [(parent, "parent"), (child, "child")]:
        config = session.config["tools"][0]["config"]
        assert config["working_dir"] == str((tmp_path / name).resolve())
        assert config["paths"] == ["declared", name]
        events = session.config["hooks"][0]["config"]["additional_events"]
        assert events[0] == "custom:event"
        assert events.count("session:config") == 1

    parent.config["hooks"][0]["config"]["additional_events"].append("parent:only")
    assert "parent:only" not in child.config["hooks"][0]["config"]["additional_events"]
    assert prepared.mount_plan == before
    assert bundle.to_mount_plan() == before
