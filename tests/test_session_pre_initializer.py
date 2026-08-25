"""Tests for the transient PreparedBundle pre-initialization callback."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from amplifier_core.hooks import HookRegistry
from amplifier_core.loader import ModuleLoader

from amplifier_foundation.bundle import Bundle, PreparedBundle
from amplifier_foundation.bundle._prepared import BundleModuleResolver
from amplifier_foundation.session.capabilities import WORKING_DIR_CAPABILITY
from amplifier_foundation.session.store import load_metadata, write_metadata
from amplifier_foundation.subprocess_runner import (
    deserialize_subprocess_config,
    serialize_subprocess_config,
)


def _session(session_id: str) -> MagicMock:
    session = MagicMock()
    session.session_id = session_id
    session.coordinator.mount = AsyncMock()
    session.coordinator.register_capability = MagicMock()
    session.coordinator.get_capability = MagicMock(return_value=None)
    session.coordinator.get = MagicMock(return_value=None)
    session.coordinator.hooks = HookRegistry()
    session.initialize = AsyncMock()
    session.execute = AsyncMock(return_value="complete")
    session.cleanup = AsyncMock()
    session.metadata = {}
    session.config = {}
    return session


def _prepared() -> PreparedBundle:
    return PreparedBundle(
        mount_plan={"providers": [{"module": "provider-test"}]},
        resolver=MagicMock(),
        bundle=Bundle(name="root", version="1.0"),
        bundle_package_paths=["/bundle/src"],
    )


def _capabilities(session: MagicMock) -> dict[str, object]:
    return {
        call.args[0]: call.args[1]
        for call in session.coordinator.register_capability.call_args_list
    }


def _contains(value: object, target: object) -> bool:
    if value is target:
        return True
    if isinstance(value, dict):
        return any(_contains(item, target) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains(item, target) for item in value)
    return False


@dataclass
class _ControlledSessionSetup:
    lifecycle: list[str] = field(default_factory=list)
    executions: list[str] = field(default_factory=list)
    provider: object = field(default_factory=object)


def _install_controlled_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> _ControlledSessionSetup:
    setup = _ControlledSessionSetup()

    class ControlledOrchestrator:
        async def execute(self, **_kwargs: Any) -> str:
            setup.executions.append("execute")
            return "controlled result"

    async def controlled_load(
        _loader: ModuleLoader,
        module_id: str,
        config: dict[str, Any] | None = None,
        source_hint: str | dict[str, Any] | None = None,
        coordinator: Any = None,
    ) -> Any:
        del config, source_hint
        setup.lifecycle.append(f"load:{module_id}")

        async def mount(module_coordinator: Any) -> None:
            setup.lifecycle.append(f"mount:{module_id}")
            if module_id == "controlled-orchestrator":
                await module_coordinator.mount("orchestrator", ControlledOrchestrator())
            elif module_id == "controlled-context":
                await module_coordinator.mount("context", object())
            elif module_id == "controlled-provider":
                await module_coordinator.mount(
                    "providers", setup.provider, name="controlled"
                )

        assert coordinator is not None
        return mount

    monkeypatch.setattr(ModuleLoader, "load", controlled_load)
    return setup


def _controlled_bundle(name: str) -> Bundle:
    return Bundle(
        name=name,
        version="1.0",
        session={
            "orchestrator": "controlled-orchestrator",
            "context": "controlled-context",
            "metadata": {"surface": "session metadata"},
        },
        providers=[{"module": "controlled-provider"}],
    )


def _controlled_prepared() -> PreparedBundle:
    root_bundle = _controlled_bundle("root")
    return PreparedBundle(
        mount_plan=root_bundle.to_mount_plan(),
        resolver=BundleModuleResolver({}),
        bundle=root_bundle,
    )


@pytest.mark.asyncio
async def test_root_callback_runs_after_static_capabilities_before_provider_mount():
    prepared = _prepared()
    session = _session("root-id")
    order: list[str] = []

    async def initialize() -> None:
        order.append("initialize")

    async def before_initialize(callback_session: Any) -> None:
        order.append("callback")
        assert callback_session is session
        assert session.coordinator.mount.await_args.args == (
            "module-source-resolver",
            prepared.resolver,
        )
        capabilities = _capabilities(session)
        assert capabilities["bundle_package_paths"] == ["/bundle/src"]
        assert capabilities[WORKING_DIR_CAPABILITY] == str(Path.cwd().resolve())

    session.initialize.side_effect = initialize

    with patch("amplifier_core.AmplifierSession", return_value=session):
        await prepared.create_session(before_initialize=before_initialize)

    assert order == ["callback", "initialize"]


@pytest.mark.asyncio
async def test_child_callback_runs_before_provider_mount_with_actual_lineage():
    prepared = _prepared()
    parent = _session("root-id")
    child = _session("child-id")
    child_bundle = Bundle(name="child", version="1.0")
    order: list[str] = []

    async def initialize() -> None:
        order.append("initialize")

    async def before_initialize(callback_session: Any) -> None:
        order.append("callback")
        assert callback_session is child
        assert callback_session.session_id == "child-id"
        assert callback_session.parent_id == "root-id"
        assert child.coordinator.mount.await_args.args == (
            "module-source-resolver",
            prepared.resolver,
        )
        capabilities = _capabilities(child)
        assert capabilities["bundle_package_paths"] == ["/bundle/src"]
        assert capabilities[WORKING_DIR_CAPABILITY] == str(Path.cwd().resolve())

    child.initialize.side_effect = initialize

    def create_child_session(*_args: object, **kwargs: object) -> MagicMock:
        child.parent_id = kwargs["parent_id"]
        return child

    with patch(
        "amplifier_core.AmplifierSession", side_effect=create_child_session
    ) as session_type:
        await prepared.spawn(
            child_bundle,
            "task",
            compose=False,
            parent_session=parent,
            before_initialize=before_initialize,
        )

    assert order == ["callback", "initialize"]
    assert session_type.call_args.kwargs["parent_id"] == "root-id"


@pytest.mark.asyncio
async def test_callback_failure_aborts_real_root_and_child_before_module_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = _install_controlled_loader(monkeypatch)
    prepared = _controlled_prepared()
    callback_error = RuntimeError("callback failure must propagate unchanged")

    async def before_initialize(_session: Any) -> None:
        raise callback_error

    with pytest.raises(RuntimeError) as root_error:
        await prepared.create_session(before_initialize=before_initialize)

    assert root_error.value is callback_error
    assert setup.lifecycle == []
    assert setup.executions == []

    parent = await prepared.create_session(session_id="parent-id")
    setup.lifecycle.clear()
    setup.executions.clear()

    with pytest.raises(RuntimeError) as child_error:
        await prepared.spawn(
            _controlled_bundle("child"),
            "task",
            compose=False,
            parent_session=parent,
            before_initialize=before_initialize,
        )

    assert child_error.value is callback_error
    assert setup.lifecycle == []
    assert setup.executions == []

    await parent.cleanup()


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_mask_callback_failure() -> None:
    prepared = _prepared()
    callback_error = RuntimeError("callback failed")
    cleanup_error = RuntimeError("cleanup failed")
    session = _session("root-id")
    session.cleanup.side_effect = cleanup_error

    async def before_initialize(_session: Any) -> None:
        raise callback_error

    with (
        patch("amplifier_core.AmplifierSession", return_value=session),
        pytest.raises(RuntimeError) as actual_error,
    ):
        await prepared.create_session(before_initialize=before_initialize)

    assert actual_error.value is callback_error
    session.initialize.assert_not_awaited()
    session.cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_callback_is_transient_and_never_leaks_into_session_visible_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Real root/child sessions run the callback before controlled provider mounts."""

    sentinel = "never-persist-this-callback"
    setup = _install_controlled_loader(monkeypatch)
    callback_sessions: list[Any] = []

    class TransientCallback:
        def __repr__(self) -> str:
            return f"<{sentinel}>"

        async def __call__(self, session: Any) -> None:
            callback_sessions.append(session)
            setup.lifecycle.append(f"callback:{session.session_id}")
            assert session.coordinator.get("providers") == {}

    before_initialize = TransientCallback()
    prepared = _controlled_prepared()

    root = await prepared.create_session(
        session_id="root-id", before_initialize=before_initialize
    )
    child_bundle = _controlled_bundle("child")
    spawn_result = await prepared.spawn(
        child_bundle,
        "task",
        compose=False,
        parent_session=root,
        before_initialize=before_initialize,
    )
    child = callback_sessions[1]

    assert callback_sessions == [root, child]
    assert child.session_id == spawn_result["session_id"]
    assert child.session_id != root.session_id
    assert child.parent_id == root.session_id
    assert root.coordinator.get("providers") == {"controlled": setup.provider}
    assert child.coordinator.get("providers") == {"controlled": setup.provider}
    assert setup.lifecycle.index(f"callback:{root.session_id}") < setup.lifecycle.index(
        "mount:controlled-provider"
    )
    assert setup.lifecycle.index(
        f"callback:{child.session_id}"
    ) < setup.lifecycle.index(
        "mount:controlled-provider",
        setup.lifecycle.index(f"callback:{child.session_id}"),
    )

    session_metadata = child.config["session"]["metadata"]
    serialized_config = serialize_subprocess_config(
        config=child.config,
        prompt="task",
        parent_id=child.parent_id,
        project_path=str(tmp_path),
        session_id=child.session_id,
    )
    subprocess_config = deserialize_subprocess_config(serialized_config)
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    write_metadata(
        session_dir,
        {
            "session_id": child.session_id,
            "parent_id": child.parent_id,
            "config": child.config,
            "metadata": session_metadata,
        },
    )
    persisted_session = load_metadata(session_dir)

    for surface in (
        prepared.mount_plan,
        child_bundle.to_mount_plan(),
        root.config,
        child.config,
        session_metadata,
        subprocess_config,
        persisted_session,
        repr(prepared),
        repr(root),
        repr(child),
    ):
        assert not _contains(surface, before_initialize)
        assert sentinel not in repr(surface)
    assert sentinel not in serialized_config
    assert sentinel not in (session_dir / "metadata.json").read_text(encoding="utf-8")

    await root.cleanup()


@pytest.mark.asyncio
async def test_omitting_callback_preserves_root_and_child_execution():
    prepared = _prepared()
    root = _session("root-id")
    child = _session("child-id")

    with patch("amplifier_core.AmplifierSession", return_value=root):
        assert await prepared.create_session() is root

    with patch("amplifier_core.AmplifierSession", return_value=child):
        result = await prepared.spawn(
            Bundle(name="child", version="1.0"), "task", compose=False
        )

    assert result["output"] == "complete"
    root.initialize.assert_awaited_once()
    child.initialize.assert_awaited_once()


@pytest.mark.asyncio
async def test_same_callback_can_be_reused_for_root_and_child_sessions():
    prepared = _prepared()
    root = _session("root-id")
    child = _session("child-id")
    seen: list[str] = []

    async def before_initialize(callback_session: Any) -> None:
        seen.append(callback_session.session_id)

    with patch("amplifier_core.AmplifierSession", return_value=root):
        await prepared.create_session(before_initialize=before_initialize)

    with patch("amplifier_core.AmplifierSession", return_value=child):
        await prepared.spawn(
            Bundle(name="child", version="1.0"),
            "task",
            compose=False,
            before_initialize=before_initialize,
        )

    assert seen == ["root-id", "child-id"]


def test_session_pre_initializer_is_publicly_exported():
    from amplifier_foundation import SessionPreInitializer as top_level
    from amplifier_foundation.bundle import SessionPreInitializer as bundle_level
    from amplifier_foundation.bundle._prepared import (
        SessionPreInitializer as implementation,
    )

    assert top_level is implementation
    assert bundle_level is implementation
