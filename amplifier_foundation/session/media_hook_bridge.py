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

from amplifier_core import HookRegistry, HookResult, ModuleCoordinator

_KEY = "session.media.tool_post"
_POST = "tool:post"
_ATTRIBUTED_SOURCE = {
    "component": "loop-streaming", "adapter": "attributed-v1",
    "contract": 1, "module": "amplifier_module_loop_streaming.dispatch",
}


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
    revision: int
    seen: list[_Registration] = field(default_factory=list)
    complete: bool = True
    active: bool = True
    denied: bool = False


_current: ContextVar[_Witness | None] = ContextVar("media_tool_post_witness", default=None)
_route_scopes: ContextVar[tuple[Any, ...]] = ContextVar("media_route_scopes", default=())


@dataclass(eq=False, frozen=True)
class _AttributedRoute:
    hooks: Any
    coordinator: Any
    hook_parent: Any
    coordinator_parent: Any
    source: dict[str, Any]
    observation: dict[str, Any]


class AttributedRouteLease:
    """Draft host-only route lifetime; NOT graph qualification or authority."""

    def __init__(self, bridge: MediaHookBridge, route: _AttributedRoute) -> None:
        self._bridge = bridge
        self._route = route
        self._active = True
        self._token: Any = None

    def __enter__(self) -> AttributedRouteLease:
        if not self._active or self._token is not None:
            raise ValueError("route lease is revoked or already entered")
        self._token = _route_scopes.set((*_route_scopes.get(), self))
        return self

    def revoke(self) -> None:
        self._active = False
        self._bridge._routes[:] = [item for item in self._bridge._routes if item is not self]

    def __exit__(self, *args: Any) -> None:
        self.revoke()
        if self._token is not None:
            token, self._token = self._token, None
            _route_scopes.reset(token)


class _RouteEmitter:
    """Known dispatcher behavior only; supplied wrapper methods are never called."""

    def __init__(self, bridge: MediaHookBridge, routes: tuple[AttributedRouteLease, ...],
                 all_routes: tuple[AttributedRouteLease, ...]) -> None:
        self.bridge, self.routes, self.all_routes = bridge, routes, all_routes

    async def emit(self, name: str, data: dict[str, Any]) -> Any:
        if not self.bridge._routes_valid(self.all_routes):
            raise ValueError("attributed route is not active in this execution")
        enriched = dict(data)
        for lease in self.routes:
            source = lease._route.source
            enriched = {**enriched, "parent_tool_call_id": source["parent_tool_call_id"],
                        "dispatch_source": deepcopy(source)}
        result = await self.bridge._native_emit(name, enriched)
        witness = _current.get()
        if witness is not None and witness.bridge is self.bridge:
            if (not self.bridge._routes_valid(self.all_routes) or
                    not self.bridge._witness_consistent(witness)):
                witness.complete = False
            # Same observer order as nested AttributedHooks unwinding. This
            # partial effective result cannot grant publication/readiness.
            for lease in reversed(self.routes):
                lease._route.observation["post_seen"] = True
                lease._route.observation["post_result"] = (
                    deepcopy(witness.effective) if witness.complete else None)
        return result


class _RouteProcessor:
    def __init__(self, bridge: MediaHookBridge, routes: tuple[AttributedRouteLease, ...],
                 all_routes: tuple[AttributedRouteLease, ...]) -> None:
        self.bridge, self.routes, self.all_routes = bridge, routes, all_routes

    def __getattr__(self, name: str) -> Any:
        return getattr(self.bridge._coordinator, name)

    async def process_hook_result(self, result: Any, event: str, name: str) -> Any:
        # Once emission occurred, preserve the actual processing operation even
        # if the route was revoked during await. Revocation is not rollback.
        try:
            processed = await self.bridge._native_process(result, event, name)
        except BaseException:
            for lease in self.all_routes:
                if lease._route.observation.get("post_seen"):
                    lease._route.observation["post_result"] = None
            raise
        if isinstance(processed, HookResult) and processed.action == "deny":
            for lease in reversed(self.routes):
                lease._route.observation["denied"] = True
        witness = _current.get()
        if witness is not None and witness.bridge is self.bridge:
            if not self.bridge._routes_valid(self.all_routes):
                witness.complete = False
        return processed


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
        self._native_emit = self._registry.emit
        self._native_process = coordinator.process_hook_result
        if not (
            type(self._registry) is HookRegistry
            and type(coordinator) is ModuleCoordinator
            and inspect.isbuiltin(self._native_emit)
            and self._native_emit.__self__ is self._registry
            and inspect.isbuiltin(self._native_process)
            and self._native_process.__self__ is coordinator
        ):
            raise ValueError("draft bridge requires actual bound Core Rust endpoints")
        self._registrations: list[_Registration] = []
        self._routes: list[AttributedRouteLease] = []
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
        emitter, processor = self._bound_route(event, hooks, coordinator)
        observed = await self._observe(event, hooks=emitter, coordinator=processor)
        # Public Core 2 cannot expose/fence transient raw registrations in its
        # execution snapshot. A covered-callback witness is necessary but not a
        # full-chain proof. Keep activation closed pending the owning contract.
        return {"hook_result": observed["hook_result"], "result": None,
                "complete": False}

    def _root_hooks(self, hooks: Any) -> bool:
        return hooks is None or hooks is self._registry or hooks is self.hooks

    def _root_coordinator(self, coordinator: Any) -> bool:
        return (coordinator is None or coordinator is self._coordinator
                or coordinator is self.coordinator)

    def _route_for(self, token: Any, *, hooks: bool) -> AttributedRouteLease:
        for lease in self._routes:
            actual = lease._route.hooks if hooks else lease._route.coordinator
            if token is actual:
                return lease
        raise ValueError("unregistered attributed route identity")

    def _lineage(self, token: Any, *, hooks: bool) -> tuple[AttributedRouteLease, ...]:
        leases = []
        root = self._root_hooks if hooks else self._root_coordinator
        lease = None if root(token) else self._route_for(token, hooks=hooks)
        while lease is not None:
            if any(item is lease for item in leases):
                raise ValueError("cyclic attributed route")
            leases.append(lease)
            # Bind to the actual parent generation, never dynamically re-resolve
            # tokens that another enrollment could reuse after revocation.
            lease = lease._route.hook_parent if hooks else lease._route.coordinator_parent
        return tuple(leases)

    @staticmethod
    def _routes_valid(routes: tuple[AttributedRouteLease, ...]) -> bool:
        scopes = _route_scopes.get()
        return all(item._active and any(scope is item for scope in scopes) for item in routes)

    def enroll_attributed_route(
        self, *, hooks: Any, coordinator: Any, parent_hooks: Any,
        parent_coordinator: Any, source: dict[str, Any], observation: dict[str, Any],
        source_identity: dict[str, Any],
    ) -> AttributedRouteLease:
        """Draft fixed streaming descriptor, NOT self-certified source authority.

        Only controlled source-owner fixtures should enroll until the owning
        graph contract is accepted. No wrapper attributes/methods are inspected.
        """
        if (type(source_identity) is not dict or
                type(source_identity.get("contract")) is not int or
                source_identity != _ATTRIBUTED_SOURCE):
            raise ValueError("unsupported attributed adapter descriptor")
        keys = {"session_id", "run_id", "parent_tool_call_id", "tool_call_id",
                "tool_name", "request_id"}
        if type(source) is not dict or set(source) != keys:
            raise ValueError("attributed source requires exact dispatcher fields")
        if any(type(source[key]) is not str or not source[key] for key in keys - {"request_id"}):
            raise ValueError("attributed source identities must be nonempty strings")
        if source["request_id"] is not None and (
                type(source["request_id"]) is not str or not source["request_id"]):
            raise ValueError("invalid attributed request identity")
        if source["session_id"] != self._coordinator.session_id:
            raise ValueError("attributed source belongs to another session")
        if type(observation) is not dict:
            raise ValueError("observation must be the dispatcher-owned dictionary")
        if self._root_hooks(hooks) or self._root_coordinator(coordinator):
            raise ValueError("attributed tokens must be distinct from root")
        for lease in self._routes:
            if hooks is lease._route.hooks or coordinator is lease._route.coordinator:
                raise ValueError("attributed route identity already enrolled")
        hook_parents = self._lineage(parent_hooks, hooks=True)
        coordinator_parents = self._lineage(parent_coordinator, hooks=False)
        parents = (*hook_parents, *coordinator_parents)
        if not self._routes_valid(parents):
            raise ValueError("attributed parent route is inactive")
        route = _AttributedRoute(hooks, coordinator,
                                 hook_parents[0] if hook_parents else None,
                                 coordinator_parents[0] if coordinator_parents else None,
                                 deepcopy(source), observation)
        lease = AttributedRouteLease(self, route)
        self._routes.append(lease)
        return lease

    def _bound_route(self, event: dict[str, Any], hooks: Any, coordinator: Any) -> tuple[Any, Any]:
        if type(event) is not dict or type(event.get("tool_name")) is not str:
            raise ValueError("tool:post requires the actual tool event/name")
        hook_routes = self._lineage(hooks, hooks=True)
        process_routes = self._lineage(coordinator, hooks=False)
        if bool(hook_routes) != bool(process_routes) or (
                hook_routes and hook_routes[0] is not process_routes[0]):
            raise ValueError("hooks/coordinator must name the same enrolled pair")
        all_routes = (*hook_routes, *process_routes)
        if not self._routes_valid(all_routes):
            raise ValueError("attributed route is not active in this execution")
        if hook_routes:
            source = hook_routes[0]._route.source
            if (event.get("tool_call_id") != source["tool_call_id"] or
                    event["tool_name"] != source["tool_name"]):
                raise ValueError("event does not match enrolled dispatcher call")
        return (_RouteEmitter(self, hook_routes, all_routes),
                _RouteProcessor(self, process_routes, all_routes))

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
        witness = _Witness(self, deepcopy(event.get("result")), expected, revision,
                           complete=self.readiness()["registration_consistent"])
        try:
            _json_value(event)
        except (ValueError, TypeError):
            witness.complete = False
        if not expected and emitter is not self._registry and type(emitter) is not _RouteEmitter:
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
            witness.complete = witness.complete and self._witness_consistent(witness)
            if not witness.complete and type(emitter) is _RouteEmitter:
                for lease in emitter.routes:
                    lease._route.observation["post_result"] = None
            return {"hook_result": processed,
                    "result": witness.effective if witness.complete else None,
                    "complete": witness.complete}
        finally:
            witness.active = False
            _current.reset(token)

    def _witness_consistent(self, witness: _Witness) -> bool:
        seen, expected = witness.seen, witness.expected
        expected_seen = expected[:len(seen)] if witness.denied else expected
        return (seen == expected_seen and len(seen) <= len(expected) and
                self.readiness()["registration_consistent"] and
                self._revision == witness.revision and self._expected() == expected)


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
