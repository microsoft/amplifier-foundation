"""Host-owned tool:post witness over the existing Core hook execution.

Core 2 exposes a read-only registry. Install before initialization: decorate the
public loader's Python mount callbacks, and use the returned registration view
for host registrations. This is not an interceptor for arbitrary native hooks.
"""

from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
import inspect
import json
from typing import Any
from uuid import uuid4

from amplifier_core import HookResult

_KEY = "session.media.tool_post"
_POST = "tool:post"


def _json_value(value: Any) -> None:
    """Refuse values Core's forgiving serializer would coerce or stringify."""
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("non-string JSON key")
        for item in value.values():
            _json_value(item)
    elif isinstance(value, list):
        for item in value:
            _json_value(item)
    elif value is not None and type(value) not in (str, int, float, bool):
        raise ValueError("not an exact JSON value")
    json.dumps(value, allow_nan=False)


@dataclass(eq=False)
class _Registration:
    event: str
    name: str
    priority: int
    order: int
    active: bool = True


@dataclass
class _Witness:
    bridge: Any
    effective: Any
    expected: list[_Registration]
    seen: list[_Registration] = field(default_factory=list)
    complete: bool = True
    active: bool = True
    denied: bool = False


_current: ContextVar[_Witness | None] = ContextVar("media_tool_post_witness", default=None)


class _CoordinatorView:
    def __init__(self, bridge: MediaHookBridge) -> None:
        object.__setattr__(self, "_bridge", bridge)

    @property
    def hooks(self) -> Any:
        return self._bridge.hooks

    def get(self, mount_point: str, name: str | None = None) -> Any:
        if mount_point == "hooks" and name is None:
            return self.hooks
        return self._bridge._coordinator.get(mount_point, name)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._bridge._coordinator, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._bridge._coordinator, name, value)


class _HooksView:
    def __init__(self, bridge: MediaHookBridge) -> None:
        self._bridge = bridge

    def __getattr__(self, name: str) -> Any:
        return getattr(self._bridge._registry, name)

    def register(self, event: str, handler: Any, priority: int = 0,
                 name: str | None = None) -> Any:
        bridge = self._bridge
        registration = _Registration(event, name if name is not None else
                                     f"_media_hook_{uuid4().hex}", priority,
                                     len(bridge._registrations))

        async def observed(actual_event: str, data: dict[str, Any]) -> Any:
            witness = _current.get()
            if witness is None or witness.bridge is not bridge or not witness.active:
                witness = None
            if witness is not None:
                witness.seen.append(registration)
            try:
                before = deepcopy(data) if witness is not None else None
                result = handler(actual_event, data)
                if inspect.isawaitable(result):
                    result = await result
                if witness is not None:
                    if not isinstance(result, HookResult):
                        witness.complete = False
                    else:
                        try:
                            _json_value(result.model_dump())
                        except (ValueError, TypeError):
                            witness.complete = False
                    if not isinstance(result, HookResult):
                        pass
                    elif result.action == "deny":
                        witness.denied = True
                        witness.effective = None
                    elif isinstance(result.data, dict) and "result" in result.data:
                        witness.effective = deepcopy(result.data["result"])
                    elif result.action == "modify":
                        witness.effective = None
                    elif data != before:
                        # Core serializes each callback's input separately: an
                        # in-place-only change is otherwise irretrievably lost.
                        if ("result" in data) != ("result" in before) or data.get("result") != before.get("result"):
                            witness.effective = deepcopy(data.get("result"))
                            if result.action == "continue":
                                # Carry the already-performed edit through the
                                # real registry's existing Modify mechanism.
                                result = result.model_copy(update={"action": "modify", "data": data})
                            else:
                                # A single Core action cannot both carry this
                                # edit and inject/ask. Preserve that action and
                                # refuse media rather than reorder its effects.
                                witness.complete = False
                    try:
                        if isinstance(result, HookResult):
                            _json_value(result.model_dump())
                        _json_value(witness.effective)
                    except (ValueError, TypeError):
                        witness.complete = False
                return result
            except BaseException:
                if witness is not None:
                    witness.complete = False
                raise  # Keep Core's existing swallow/log/cancellation semantics.

        unregister = bridge._registry.register(
            event, observed if event == _POST else handler,
            priority=priority, name=registration.name)
        bridge._registrations.append(registration)
        bridge._revision += 1

        def remove() -> None:
            unregister()
            if registration.active:
                registration.active = False
                bridge._revision += 1

        return remove

    on = register

    def unregister(self, name: str) -> None:
        self._bridge._registry.unregister(name)
        for item in reversed(self._bridge._registrations):
            if item.active and item.name == name:
                item.active = False
                self._bridge._revision += 1
                break

    async def emit(self, event: str, data: dict[str, Any]) -> Any:
        # An ordinary nested emission is not part of its caller's witness.
        token = _current.set(None)
        try:
            return await self._bridge._registry.emit(event, data)
        finally:
            _current.reset(token)

    async def emit_and_collect(self, *args: Any, **kwargs: Any) -> Any:
        token = _current.set(None)
        try:
            return await self._bridge._registry.emit_and_collect(*args, **kwargs)
        finally:
            _current.reset(token)


class _LoaderView:
    def __init__(self, bridge: MediaHookBridge, loader: Any) -> None:
        self._bridge = bridge
        self._loader = loader

    def __getattr__(self, name: str) -> Any:
        return getattr(self._loader, name)

    def _ready(self, callback: Any) -> Any:
        async def ready(coordinator: Any) -> Any:
            return await callback(self._bridge.coordinator)
        return ready

    async def load(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["coordinator"] = self._bridge.coordinator
        mount = await self._loader.load(*args, **kwargs)

        async def mounted(coordinator: Any) -> Any:
            return await mount(self._bridge.coordinator)

        if ready := getattr(mount, "__on_session_ready__", None):
            mounted.__on_session_ready__ = (ready[0], self._ready(ready[1]))
        return mounted

    def enqueue_on_session_ready(self, module_id: str, callback: Any) -> None:
        self._loader.enqueue_on_session_ready(module_id, self._ready(callback))

    def get_on_session_ready_queue(self) -> Any:
        # Also cover callbacks enqueued inside the decorated loader itself.
        return [(name, self._ready(callback)) for name, callback in
                self._loader.get_on_session_ready_queue()]


class MediaHookBridge:
    """Per-coordinator registration view and structural single-execution callback.

    The host must qualify the Python-loader/explicit-host registration graph.
    Public names are a consistency check, not proof of arbitrary native coverage.
    """

    def __init__(self, coordinator: Any) -> None:
        self._coordinator = coordinator
        self._registry = coordinator.hooks
        self._registrations: list[_Registration] = []
        self._revision = 0
        self.hooks = _HooksView(self)
        self.coordinator = _CoordinatorView(self)
        self._loader: Any = None

    def _expected(self) -> list[_Registration]:
        return sorted((r for r in self._registrations if r.active and r.event == _POST),
                      key=lambda r: (r.priority, r.order))

    def readiness(self) -> dict[str, Any]:
        expected = self._expected()
        consistent = self._registry.list_handlers(_POST).get(_POST, []) == [r.name for r in expected]
        return {"contract": 1, "registration_consistent": consistent and self._coordinator.loader is self._loader,
                "authoritative": False,
                "scope": "python-loader-and-explicit-host", "native_coverage": False}

    async def __call__(self, event: dict[str, Any], *, hooks: Any = None,
                       coordinator: Any = None) -> dict[str, Any]:
        observed = await self._observe(event, hooks=hooks, coordinator=coordinator)
        # Public Core 2 cannot expose/fence transient raw registrations in its
        # execution snapshot. A covered-callback witness is necessary but not a
        # full-chain proof. Keep activation closed pending the owning contract.
        return {"hook_result": observed["hook_result"], "result": None,
                "complete": False}

    async def _observe(self, event: dict[str, Any], *, hooks: Any = None,
                       coordinator: Any = None) -> dict[str, Any]:
        """Internal partial callback witness, NOT a media acceptance API."""
        current_coordinator = self._coordinator if coordinator is None else coordinator
        if (current_coordinator.session_id != self._coordinator.session_id or
                current_coordinator.get_capability(_KEY) is not self):
            raise ValueError("tool:post coordinator does not belong to this bridge")
        if not isinstance(event, dict) or not isinstance(event.get("tool_name"), str):
            raise ValueError("tool:post requires the actual tool event/name")
        emitter = self._registry if hooks is None else hooks
        # Our own registration facade suppresses witnesses for ordinary emit;
        # the structural call explicitly opts into the single owned emission.
        if emitter is self.hooks:
            emitter = self._registry
        expected = self._expected()
        revision = self._revision
        witness = _Witness(self, deepcopy(event.get("result")), expected,
                           complete=self.readiness()["registration_consistent"])
        try:
            _json_value(event)
        except (ValueError, TypeError):
            witness.complete = False
        if not expected and emitter is not self._registry:
            # No callback can attest that a supplied wrapper delegated to this
            # registry. Empty-chain foreign emitters cannot self-certify.
            witness.complete = False
        token = _current.set(witness)
        try:
            hook_result = await emitter.emit(_POST, event)
            if not isinstance(hook_result, HookResult):
                witness.complete = False
                before_process = None
            else:
                before_process = deepcopy(hook_result.model_dump())
                # Core's native aggregate never returns Modify. A supplied
                # emitter's explicit final replacement must not be ignored.
                if hook_result.action == "modify":
                    witness.effective = deepcopy((hook_result.data or {}).get("result"))
                elif hook_result.action == "deny":
                    witness.effective = None
            # Preserve the caller's attribution/approval wrapper and exact order.
            processed = await current_coordinator.process_hook_result(
                hook_result, _POST, event["tool_name"])
            if not isinstance(processed, HookResult):
                witness.complete = False
            elif processed.action == "deny":
                witness.effective = None
            elif before_process is not None and (
                    processed.data != before_process["data"] or
                    processed.action != before_process["action"]):
                if isinstance(processed.data, dict) and "result" in processed.data:
                    witness.effective = deepcopy(processed.data["result"])
                elif processed.action == "modify":
                    witness.effective = None
            try:
                if isinstance(processed, HookResult):
                    _json_value(processed.model_dump())
                _json_value(witness.effective)
            except (ValueError, TypeError):
                witness.complete = False
            seen = witness.seen
            expected_seen = expected[:len(seen)] if witness.denied else expected
            witness.complete = (witness.complete and seen == expected_seen and
                                len(seen) <= len(expected) and
                                self.readiness()["registration_consistent"] and
                                self._revision == revision and
                                self._expected() == expected)
            return {"hook_result": processed,
                    "result": witness.effective if witness.complete else None,
                    "complete": witness.complete}
        finally:
            witness.active = False
            _current.reset(token)


def install_media_hook_bridge(coordinator: Any) -> MediaHookBridge:
    """Install before modules or host tool:post handlers register.

    Return the explicit registration facade; never assign Core's read-only hooks.
    Existing handlers cannot be retroactively qualified from public names alone.
    """
    existing = coordinator.get_capability(_KEY)
    if existing is not None:
        if isinstance(existing, MediaHookBridge) and existing._coordinator is coordinator:
            return existing
        raise ValueError("tool:post capability already installed by another owner")
    if coordinator.hooks.list_handlers(_POST).get(_POST):
        raise ValueError("install media hook bridge before tool:post registrations")
    bridge = MediaHookBridge(coordinator)
    loader = coordinator.loader
    if loader is None:
        from amplifier_core.loader import ModuleLoader
        loader = ModuleLoader(coordinator=bridge.coordinator)
    bridge._loader = _LoaderView(bridge, loader)
    coordinator.loader = bridge._loader
    coordinator.register_capability(_KEY, bridge)
    return bridge
