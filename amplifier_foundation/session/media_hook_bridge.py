"""Host-owned tool:post witness over the existing Core hook execution.

Core 2 exposes a read-only registry. Install before initialization: decorate the
public loader's Python mount callbacks, and use the returned registration view
for host registrations. This is not an interceptor for arbitrary native hooks.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import inspect
from importlib.metadata import version as package_version
import json
from typing import Any
from uuid import uuid4

from amplifier_core import AmplifierSession, HookRegistry, HookResult, ModuleCoordinator

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
    participant: str | None = None
    original: Any = None
    observed: Any = None
    identity: Any = field(default_factory=object)


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
    receipt: Any = None


_current: ContextVar[_Witness | None] = ContextVar("media_tool_post_witness", default=None)
_route_scopes: ContextVar[tuple[Any, ...]] = ContextVar("media_route_scopes", default=())
_mounting: ContextVar[Any] = ContextVar("media_participating_mount", default=None)


class _ConstructionReceipt:
    """Identity token only. All facts stay in the construction owner's record."""
    __slots__ = ()


class _SuspensionToken:
    """Opaque issuer lookup; no caller-supplied identity/configuration fields."""
    __slots__ = ()


def _load_plan(config: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    result = []
    session = config.get("session", {})
    for kind, default in (("orchestrator", "loop-basic"), ("context", "context-simple")):
        spec = session.get(kind, default)
        if isinstance(spec, str):
            result.append((spec, config.get(kind, {}).get("config", {}),
                           session.get(f"{kind}_source")))
        else:
            result.append((spec["module"], spec.get("config", {}), spec.get("source")))
    for kind in ("providers", "tools", "hooks"):
        for spec in config.get(kind, []):
            if spec.get("module"):
                result.append((spec["module"], spec.get("config", {}), spec.get("source")))
    return deepcopy(result)


def _occurrence_plan(config: dict[str, Any]) -> list[tuple[Any, ...]]:
    """Core's ordered configured occurrences, including provider remap identity."""
    identities = [("orchestrator", 0, None), ("context", 0, None)]
    for kind in ("providers", "tools", "hooks"):
        identities.extend((kind, index, spec.get("instance_id") if kind == "providers" else None)
                          for index, spec in enumerate(config.get(kind, [])) if spec.get("module"))
    return [(identity, plan) for identity, plan in zip(identities, _load_plan(config))]


def participating_module_ids(session: Any) -> tuple[str, ...]:
    """Inspect Core's effective structural plan, NOT approve configured sources."""
    return tuple(dict.fromkeys(item[0] for item in _load_plan(session.config)))


class ParticipatingHookGraph:
    """One actual fresh-session construction, under a cooperative host contract.

    This records actual participating callbacks and lifecycle, not independent
    source approval, arbitrary-Python isolation or authorization to retain media.
    """

    def __init__(self, bridge: MediaHookBridge, session: Any, owner: str,
                 participants: tuple[str, ...]) -> None:
        self._bridge, self._session = bridge, session
        self.owner, self.participants = owner, participants
        self._state = "open"
        self._reason: str | None = None
        self._config = deepcopy(session.config)
        self._plan = _load_plan(self._config)
        self._loads: list[dict[str, Any]] = []
        self._phase_loads: list[Any] = []
        self._views: dict[str, Any] = {}
        self._seen: set[str] = set()
        self._ready: list[list[Any]] = []
        self._wrapped_ready: list[Any] = []
        self._ready_wrappers: list[tuple[Any, str, Any, Any]] = []
        self._receipt: Any = None
        self._record: Any = None
        self._mount_records: list[tuple[Any, Any, str]] = []
        self._slot_sources: dict[tuple[str, Any], Any] = {}
        self._suspended: dict[tuple[str, Any], Any] = {}
        self._adapter_mounts: list[Any] = []
        self._generation = uuid4().hex
        self._core_abi = package_version("amplifier-core")
        self._transition_parent: Any = None

    def _invalidate(self, reason: str) -> None:
        self._state, self._reason = "invalid", reason

    def _mutation(self, reason: str) -> None:
        if self._state == "sealed":
            self._invalidate(reason)

    def participant(self, name: str) -> Any:
        if type(name) is not str or name not in self.participants:
            self._invalidate("undeclared participating owner")
            raise ValueError("owner is not a declared graph participant")
        self._seen.add(name)
        if name not in self._views:
            self._views[name] = _CoordinatorView(self._bridge, name)
        return self._views[name]

    def participants_evidence(self) -> dict[str, Any]:
        """Host-only source-join inspection; callable refs are never serialized.

        Container metadata is detached. Actual strong callable/registration
        identities are intentionally returned for the host's reviewed manifest.
        Inspection is not source approval, sealing, publication or a verifier.
        """
        return {
            "owner": self.owner, "generation": self._generation,
            "state": self._state, "participants": tuple(self.participants),
            "loads": tuple({"module": item["plan"][0],
                            "config": deepcopy(item["plan"][1]),
                            "source_hint": deepcopy(item["plan"][2]),
                            "occurrence": item["occurrence"],
                            "slots": tuple(sorted(item["slots"])),
                            "mount": item["mount"], "mounted": item["mounted"]}
                           for item in self._loads),
            "ready": tuple({"callback": item[0], "participant": item[1],
                            "completed": item[2]} for item in self._ready),
            "registrations": tuple({
                "identity": item.identity, "participant": item.participant,
                "original": item.original, "observed": item.observed,
                "name": item.name, "priority": item.priority, "order": item.order,
            } for item in self._bridge._expected()),
            "adapters": tuple({"slot": slot, "previous": previous, "installed": installed,
                               "participant": owner} for slot, previous, installed, owner
                              in self._adapter_mounts),
            "suspended": tuple({"slot": slot, "module": record["module"],
                                "participant": record["owner"],
                                "state": record["state"],
                                "occurrence": (record["source"]["occurrence"]
                                               if record["source"] is not None else None)}
                               for slot, record in self._suspended.items()),
        }

    def _loading(self, module_id: str, config: Any, source_hint: Any) -> dict[str, Any]:
        self._mutation("additional module loading after seal")
        occurrence = len(self._loads)
        actual = deepcopy((module_id, config or {}, source_hint))
        if (self._state != "open" or module_id not in self.participants or
                (self._transition_parent is None and
                 (occurrence >= len(self._plan) or self._plan[occurrence] != actual))):
            self._invalidate("module load does not match participating construction plan")
        candidates = [(identity, plan) for identity, plan in _occurrence_plan(self._session.config)
                      if plan == actual and identity not in self._phase_loads]
        if self._transition_parent is not None:
            # Saved settings may load only a newly added account, not rebuild
            # the entire repeated-module plan. Prefer occurrences that lack
            # matching actual construction over already complete accounts.
            needed = [(identity, plan) for identity, plan in candidates
                      if not any(item["occurrence"] == identity and item["plan"] == plan
                                 and item["mounted"] for item in self._loads)]
            if needed:
                candidates = needed
            elif len(candidates) > 1:
                # load(module, config, source) has no account argument. An
                # ambiguous partial reload must not guess another account.
                candidates = []
        identity = candidates[0][0] if candidates else None
        if identity is None:
            self._invalidate("module load has no current configured occurrence")
        self._phase_loads.append(identity)
        record = {"plan": actual, "occurrence": identity, "mount": None,
                  "mounted": False, "slots": set(),
                  "kinds": [identity[0]] if identity is not None and identity[0] != "hooks" else []}
        self._loads.append(record)
        return record

    def _ready_callback(self, callback: Any, participant: str, attempt: Any = None) -> Any:
        for original, owner, recorded_attempt, wrapped in self._ready_wrappers:
            if original is callback and owner == participant and recorded_attempt is attempt:
                return wrapped
        self._mutation("ready callback added after seal")
        view = self.participant(participant)
        record: list[Any] = [callback, participant, False]
        self._ready.append(record)

        async def ready(coordinator: Any) -> Any:
            if self._bridge._graph is not self:
                self._bridge._graph._invalidate("stale ready callback generation")
                raise ValueError("ready callback belongs to a stale graph generation")
            self._mutation("ready callback after seal")
            if record[2]:
                self._invalidate("ready callback repeated")
            try:
                value = await callback(view)
                record[2] = True
                return value
            except BaseException:
                self._invalidate("ready callback failed")
                raise
        self._wrapped_ready.append(ready)
        self._ready_wrappers.append((callback, participant, attempt, ready))
        return ready

    def _mounts(self) -> tuple[Any, ...]:
        coordinator = self._bridge._coordinator
        result = []
        for kind in ("orchestrator", "context", "providers", "tools"):
            value = coordinator.get(kind)
            if isinstance(value, dict):
                result.append((kind, tuple(sorted(value.items()))))
            else:
                result.append((kind, value))
        return tuple(result)

    def _mounts_owned(self) -> bool:
        for kind, value in self._mounts():
            slots = list(value) if kind in ("providers", "tools") else (
                [] if value is None else [(None, value)])
            if any(not any(record_slot == (kind, name) and recorded is module
                           for record_slot, recorded, _ in self._mount_records)
                   for name, module in slots):
                return False
        return True

    def _ready_queue_empty(self) -> bool:
        try:
            return not self._bridge._loader.get_on_session_ready_queue()
        except Exception:
            self._invalidate("unaccounted ready work")
            return False

    def _callback_snapshot(self) -> tuple[Any, ...]:
        return tuple((item, item.original, item.observed, item.participant,
                      item.name, item.priority, item.order)
                     for item in self._bridge._expected())

    @staticmethod
    def _same_callbacks(first: tuple[Any, ...], second: tuple[Any, ...]) -> bool:
        return len(first) == len(second) and all(
            left[0] is right[0] and left[1] is right[1] and left[2] is right[2]
            and left[3:] == right[3:] for left, right in zip(first, second))

    @staticmethod
    def _same_mounts(left: tuple[Any, ...], right: tuple[Any, ...]) -> bool:
        for (kind, first), (_, second) in zip(left, right):
            if kind in ("providers", "tools"):
                if len(first) != len(second) or any(
                        a != b or x is not y for (a, x), (b, y) in zip(first, second)):
                    return False
            elif first is not second:
                return False
        return True

    def seal(self) -> Any:
        if self._state != "open":
            raise ValueError("graph cannot seal again; construct a fresh session")
        bridge = self._bridge
        if (not self._ready_queue_empty() or
                self._state != "open" or not self._mounts_owned() or
                not self._session.initialized or self._session.coordinator is not bridge._coordinator or
                self._session.config != self._config or
                self._seen != set(self.participants) or
                not self._plan_complete() or
                any(not item["mounted"] for item in self._loads) or
                any(not item[2] for item in self._ready) or
                not bridge.readiness()["registration_consistent"] or
                any(item.participant not in self.participants for item in bridge._expected())):
            self._invalidate("incomplete or inconsistent participating construction")
            raise ValueError("participating construction is incomplete or inconsistent")
        self._receipt = _ConstructionReceipt()
        self._record = (bridge._revision, self._callback_snapshot(), bridge._loader,
                        bridge._native_emit, bridge._native_process, self._mounts())
        self._state = "sealed"
        if not self._valid(self._receipt):
            raise ValueError("participating construction changed during seal")
        return self._receipt

    @contextmanager
    def configure(self):
        """Track actual saved-settings construction after init, before first seal."""
        bridge = self._bridge
        if (self._state != "open" or not self._session.initialized or
                self._receipt is not None or bridge._transition is not None or
                bridge._active_boundaries or bridge._active_requests or bridge._processing or
                any(lease._active for lease in bridge._routes) or
                not self._ready_queue_empty() or not self._plan_complete()):
            raise ValueError("configuration phase requires complete idle open construction")
        self._transition_parent = self._transition_parent or object()
        self._phase_loads = []
        bridge._transition = self
        try:
            yield self
            self._config = deepcopy(self._session.config)
            self._plan = _load_plan(self._config)
            if (not {item[0] for item in self._plan} <= set(self.participants) or
                    not self._plan_complete() or not self._ready_queue_empty()):
                raise ValueError("configured modules did not complete actual construction")
        except BaseException:
            self._invalidate("participating saved-settings construction failed")
            raise
        finally:
            bridge._transition = None

    def _plan_complete(self) -> bool:
        if self._transition_parent is None and len(self._loads) != len(self._plan):
            return False
        current_plan = dict(_occurrence_plan(self._config))
        for slot, record in self._suspended.items():
            source = record["source"]
            if (record["state"] != "suspended" or self._bridge._coordinator.get(*slot) is not None or
                    (source is not None and (current_plan.get(source["occurrence"]) != source["plan"] or
                                            not self._slot_complete(slot, source)))):
                return False
        # Latest per configured occurrence, NOT per module: two accounts may
        # share a module/config/class while retaining distinct actual objects.
        available = []
        for item in reversed(self._loads):
            if not any(other["occurrence"] == item["occurrence"] for other in available):
                available.append(item)
        for identity, planned in _occurrence_plan(self._config):
            match = next((item for item in available
                          if item["occurrence"] == identity and
                          item["plan"] == planned and item["mounted"] and
                          all(self._slot_complete(slot, item) for slot in item["slots"]) and
                          all(any(slot[0] == kind and self._bridge._coordinator.get(
                              slot[0], slot[1]) is not None or
                              slot[0] == kind and slot in self._suspended
                              for slot in item["slots"])
                              for kind in item["kinds"])), None)
            if match is None:
                return False
            available.remove(match)
        return True

    def _slot_complete(self, slot: Any, item: Any) -> bool:
        current = self._bridge._coordinator.get(*slot)
        source = self._slot_sources.get(slot, (None, None))
        suspended = self._suspended.get(slot)
        if suspended is not None:
            return (suspended["state"] == "suspended" and current is None and suspended["source"] is item and
                    source[0] is item and source[1] is suspended["module"])
        return source[0] is item and current is source[1]

    @contextmanager
    def transition(self, *, participants: tuple[str, ...] | None = None):
        """Issuer-owned idle same-session generation around the REAL host edit."""
        bridge = self._bridge
        if (not self._valid(self._receipt) or bridge._active_boundaries or
                bridge._active_requests or bridge._processing or bridge._transition is not None or
                any(lease._active for lease in bridge._routes)):
            raise ValueError("transition requires a valid idle graph without active leases")
        selected = self.participants if participants is None else participants
        bridge._validate_participants(selected)
        if not set(self.participants) <= set(selected):
            raise ValueError("transition cannot discard existing participant provenance")
        successor = ParticipatingHookGraph(bridge, self._session, self.owner, selected)
        successor._transition_parent = self
        successor._loads = list(self._loads)
        successor._seen = set(self._seen)
        successor._views = dict(self._views)
        successor._mount_records = list(self._mount_records)
        successor._slot_sources = dict(self._slot_sources)
        successor._suspended = dict(self._suspended)
        successor._adapter_mounts = list(self._adapter_mounts)
        successor._ready = [list(item) for item in self._ready]
        # Completed ready records are provenance history. Never reuse wrappers
        # closing over the old issuer: fresh enqueue/execution must be tracked
        # by this successor, including failures swallowed by Core initialization.
        successor._wrapped_ready = []
        successor._ready_wrappers = []
        self._invalidate("issuer began same-session reconstruction")
        bridge._graph, bridge._transition = successor, successor
        try:
            yield successor
            if bridge._active_boundaries or bridge._active_requests or bridge._processing or any(
                    lease._active for lease in bridge._routes):
                raise ValueError("transition acquired active execution")
            successor._config = deepcopy(self._session.config)
            successor._plan = _load_plan(successor._config)
            if not {entry[0] for entry in successor._plan} <= set(selected):
                raise ValueError("transition selected an undeclared module")
            successor.seal()
        except BaseException:
            successor._invalidate("participating transition failed or was cancelled")
            raise
        finally:
            bridge._transition = None

    def reconstruct(self, new_session: Any, *, participants: tuple[str, ...]) -> ParticipatingHookGraph:
        """Issuer-owned fresh generation for a real host mode/settings rebuild.

        Never replays tools or seals an existing coordinator under a new label.
        The host constructs and restores the new session through ordinary owners.
        """
        if (type(new_session) is not AmplifierSession or new_session is self._session or
                new_session.coordinator is self._bridge._coordinator or new_session.initialized):
            raise ValueError("reconstruction requires a distinct fresh actual session")
        self._invalidate("issuer reconstructed into a new session generation")
        return install_media_hook_bridge(new_session.coordinator).begin_participating_graph(
            new_session, owner=self.owner, participants=participants)

    def _valid(self, receipt: Any) -> bool:
        if self._state != "sealed" or receipt is not self._receipt:
            return False
        bridge = self._bridge
        revision, registrations, loader, emit, process, mounts = self._record
        coordinator = bridge._coordinator
        current_process = coordinator.process_hook_result
        if (not self._ready_queue_empty() or self._state != "sealed" or
                bridge._graph is not self or
                self._session.coordinator is not coordinator or not self._session.initialized or
                package_version("amplifier-core") != self._core_abi or self._core_abi != "2.0.2" or
                self._session.config != self._config or coordinator.config != self._config or
                coordinator.hooks is not bridge._registry or coordinator.loader is not loader or
                bridge._native_emit is not emit or bridge._native_process is not process or
                not inspect.isbuiltin(current_process) or
                current_process.__self__ is not coordinator or
                current_process.__name__ != "process_hook_result" or
                coordinator.get_capability(_KEY) is not bridge or
                bridge._revision != revision or
                not self._same_callbacks(self._callback_snapshot(), registrations) or
                not self._same_mounts(self._mounts(), mounts) or
                not bridge.readiness()["registration_consistent"]):
            self._invalidate("sealed participating graph changed")
            return False
        return True


@dataclass(eq=False, frozen=True)
class _AttributedRoute:
    hooks: Any
    coordinator: Any
    hook_parent: Any
    coordinator_parent: Any
    source: dict[str, Any]
    observation: dict[str, Any]
    generation: Any = None


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
        witness = _current.get()
        if witness is not None and witness.receipt is not None:
            if not self.bridge.validate_receipt(witness.receipt):
                witness.complete = False
        enriched = dict(data)
        for lease in self.routes:
            source = lease._route.source
            enriched = {**enriched, "parent_tool_call_id": source["parent_tool_call_id"],
                        "dispatch_source": deepcopy(source)}
        result = await self.bridge._native_emit(name, enriched)
        witness = _current.get()
        if witness is not None and witness.bridge is self.bridge:
            if (not self.bridge._routes_valid(self.all_routes) or
                    not self.bridge._witness_consistent(witness) or
                    (witness.receipt is not None and not self.bridge.validate_receipt(witness.receipt))):
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
        witness = _current.get()
        if witness is not None and witness.receipt is not None:
            if not self.bridge.validate_receipt(witness.receipt):
                witness.complete = False
        try:
            # The native call can raise synchronously before an awaitable exists.
            operation = asyncio.ensure_future(self.bridge._native_process(result, event, name))
            self.bridge._processing.add(operation)
            def settled(task: Any) -> None:
                self.bridge._processing.discard(task)
                if not task.cancelled():
                    task.exception()  # consume unobserved errors after caller cancellation
            operation.add_done_callback(settled)
            # Native approval may outlive a cancelled waiter. Track its actual
            # settlement and keep transitions fenced, without cancelling/replay.
            processed = await asyncio.shield(operation)
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
            if (not self.bridge._routes_valid(self.all_routes) or
                    (witness.receipt is not None and not self.bridge.validate_receipt(witness.receipt))):
                witness.complete = False
        return processed


class _CoordinatorView:
    def __init__(self, bridge: MediaHookBridge, participant: str | None = None) -> None:
        object.__setattr__(self, "_bridge", bridge)
        object.__setattr__(self, "_participant", participant)
        object.__setattr__(self, "_hooks", _HooksView(bridge, participant))

    @property
    def hooks(self) -> Any:
        return self._hooks if self._participant is not None else self._bridge.hooks

    async def mount(self, *args: Any, **kwargs: Any) -> Any:
        graph = self._bridge._graph
        kind = args[0] if args else kwargs["mount_point"]
        module = args[1] if len(args) > 1 else kwargs["module"]
        before = dict(self._bridge._coordinator.get(kind) or {}) if kind in ("providers", "tools") else {}
        if graph is not None:
            graph._mutation("mounted module changed after seal")
            if self._participant not in graph.participants:
                graph._invalidate("unowned module mount")
        value = await self._bridge._coordinator.mount(*args, **kwargs)
        if graph is not None and self._participant in graph.participants:
            name = args[2] if len(args) > 2 else kwargs.get("name")
            if kind in ("orchestrator", "context"):
                name = None  # Core ignores names for singleton mount points.
            loading = _mounting.get()
            names = [name]
            if kind in ("providers", "tools"):
                after = self._bridge._coordinator.get(kind) or {}
                # Resolve Core's auto-name from actual newly installed slots,
                # never from the module's claimed interface or an old object.
                names = [key for key, installed in after.items()
                         if installed is module and (key not in before or before[key] is not module)]
                if loading is None or loading[0] is not graph:
                    names = [key for key, installed in after.items()
                             if installed is module and (name is None or key == name)]
            for resolved in names:
                slot = (kind, resolved)
                if loading is not None and loading[0] is graph:
                    record = loading[1]
                    occurrence = record["occurrence"]
                    if (kind == "providers" and occurrence is not None and occurrence[2] and
                            resolved == record["plan"][0].removeprefix("provider-")):
                        # Core remaps AFTER the returned mount callback. Record
                        # only its prescribed final slot; seal verifies the exact
                        # strongly retained object really arrived there.
                        slot = (kind, occurrence[2])
                    record["slots"].add(slot)
                    graph._slot_sources[slot] = (record, module)
                else:
                    # Direct remounts cannot inherit historical load provenance.
                    graph._slot_sources.pop(slot, None)
                graph._mount_records.append((slot, module, self._participant))
        return value

    async def mount_adapter(self, mount_point: str, module: Any, *,
                            previous: Any, name: str | None = None) -> Any:
        """Record an explicit host-owned adapter edge over an actual live slot.

        This proves construction identity/order, not transparent behavior. The
        named composition owner reviews the adapter's actual source semantics.
        """
        graph = self._bridge._graph
        slot = (mount_point, name)
        if graph is None or self._participant not in graph.participants:
            raise ValueError("adapter mount requires an actual graph participant")
        graph._mutation("adapter mount after seal")
        source = graph._slot_sources.get(slot)
        if (source is None or source[1] is not previous or
                self._bridge._coordinator.get(mount_point, name) is not previous):
            graph._invalidate("adapter does not match actual prior construction")
            raise ValueError("adapter prior endpoint does not match actual loaded slot")
        try:
            result = await self._bridge._coordinator.mount(mount_point, module, name)
        except BaseException:
            graph._invalidate("participating adapter mount failed")
            raise
        graph._mount_records.append((slot, module, self._participant))
        graph._adapter_mounts.append((slot, previous, module, self._participant))
        graph._slot_sources[slot] = (source[0], module)
        return result

    async def unmount(self, *args: Any, **kwargs: Any) -> Any:
        graph = self._bridge._graph
        if graph is not None:
            graph._mutation("module unmounted after seal")
            if self._participant not in graph.participants:
                graph._invalidate("unowned module unmount")
            kind = args[0] if args else kwargs["mount_point"]
            name = args[1] if len(args) > 1 else kwargs.get("name")
            graph._slot_sources.pop((kind, name), None)
        return await self._bridge._coordinator.unmount(*args, **kwargs)

    def get(self, mount_point: str, name: str | None = None) -> Any:
        if mount_point == "hooks" and name is None:
            return self.hooks
        return self._bridge._coordinator.get(mount_point, name)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._bridge._coordinator, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if self._bridge._graph is not None and name in (
                "loader", "process_hook_result", "session", "config"):
            self._bridge._graph._mutation(f"participating endpoint assignment: {name}")
        setattr(self._bridge._coordinator, name, value)


class MediaMountLifecycle:
    """Explicit opt-in to issuer-owned suspension, not arbitrary duck typing."""

    def __init__(self, view: _CoordinatorView) -> None:
        self._view = view

    def _graph(self) -> Any:
        view = self._view
        bridge = view._bridge
        graph = bridge._graph
        if (graph is None or graph._state != "open" or bridge._transition is not graph or
                not graph._session.initialized or view._participant not in graph.participants or
                bridge._active_boundaries or bridge._active_requests or bridge._processing or
                any(lease._active for lease in bridge._routes)):
            raise ValueError("suspension requires owned idle configure/transition")
        return graph

    @staticmethod
    def _source_current(graph: Any, slot: Any, source: Any) -> bool:
        current = dict(_occurrence_plan(graph._session.config))
        latest = next((item for item in reversed(graph._loads)
                       if item["occurrence"] == source["occurrence"]), None)
        return (current.get(source["occurrence"]) == source["plan"] and latest is source and
                graph._slot_sources.get(slot, (None, None))[0] is source)

    @staticmethod
    async def _settle(graph: Any, operation: Any) -> Any:
        task = asyncio.ensure_future(operation)
        graph._bridge._processing.add(task)
        def settled(actual: Any) -> None:
            graph._bridge._processing.discard(actual)
            if not actual.cancelled():
                actual.exception()
        task.add_done_callback(settled)
        return await asyncio.shield(task)

    async def suspend(self, mount_point: str, *, name: str) -> Any:
        graph = self._graph()
        slot = (mount_point, name)
        if mount_point not in ("providers", "tools") or type(name) is not str or not name:
            raise ValueError("suspension requires an exact named provider/tool slot")
        module = graph._bridge._coordinator.get(*slot)
        if (module is None or slot in graph._suspended or
                not any(record_slot == slot and recorded is module
                        for record_slot, recorded, _ in graph._mount_records)):
            raise ValueError("suspension requires an actual participating installed slot")
        source, installed = graph._slot_sources.get(slot, (None, module))
        if installed is not module or (source is not None and (
                not self._source_current(graph, slot, source) or not graph._slot_complete(slot, source))):
            raise ValueError("suspension source no longer matches installed slot")
        token = _SuspensionToken()
        record = {"token": token, "owner": self._view._participant, "module": module,
                  "source": source, "generation": graph._generation, "state": "pending"}
        # Retain BEFORE dispatch. Cancellation can occur after the native slot
        # disappears but before the Python await resumes and returns a token.
        graph._suspended[slot] = record
        try:
            await self._settle(graph, graph._bridge._coordinator.unmount(mount_point, name=name))
            if graph._bridge._coordinator.get(*slot) is not None:
                raise ValueError("suspension did not remove actual slot")
        except BaseException:
            record["state"] = "interrupted"
            graph._invalidate("participating suspension failed")
            raise
        record["state"] = "suspended"
        return token

    async def restore(self, token: Any) -> Any:
        graph = self._graph()
        # Never call hash/equality/attributes on an arbitrary supplied object.
        match = next(((slot, record) for slot, record in graph._suspended.items()
                      if record["token"] is token), None)
        if match is None:
            raise ValueError("unknown, foreign or consumed suspension token")
        slot, record = match
        source = record["source"]
        if record["owner"] != self._view._participant:
            raise ValueError("suspension belongs to another participating owner")
        if record["state"] != "suspended":
            raise ValueError("suspension operation has not successfully settled")
        if graph._bridge._coordinator.get(*slot) is not None:
            raise ValueError("suspended destination is no longer empty")
        if source is not None:
            if not self._source_current(graph, slot, source):
                raise ValueError("suspended configured occurrence changed")
        record["state"] = "restoring"
        try:
            await self._settle(graph, graph._bridge._coordinator.mount(slot[0], record["module"], name=slot[1]))
            if graph._bridge._coordinator.get(*slot) is not record["module"]:
                raise ValueError("restore did not install actual suspended object")
        except BaseException:
            record["state"] = "interrupted"
            # Keep token/evidence until actual success; an uncertain effect never
            # authorizes retry or revives the old receipt.
            graph._invalidate("participating restoration failed")
            raise
        graph._mount_records.append((slot, record["module"], self._view._participant))
        del graph._suspended[slot]
        return record["module"]


def media_mount_lifecycle(coordinator: Any) -> MediaMountLifecycle | None:
    """Only an actual bound participant view opts into suspension semantics.

    Generic configurators can use this without probing arbitrary coordinators,
    mocks or proxy attributes. Native/unqualified coordinators return None.
    """
    if (type(coordinator) is _CoordinatorView and coordinator._participant is not None and
            coordinator._bridge._graph is not None and
            coordinator._participant in coordinator._bridge._graph.participants):
        return MediaMountLifecycle(coordinator)
    return None


class _HooksView:
    def __init__(self, bridge: MediaHookBridge, participant: str | None = None) -> None:
        self._bridge = bridge
        self._participant = participant

    def __getattr__(self, name: str) -> Any:
        return getattr(self._bridge._registry, name)

    def register(self, event: str, handler: Any, priority: int = 0,
                 name: str | None = None) -> Any:
        bridge = self._bridge
        if event == _POST and bridge._graph is not None:
            bridge._graph._mutation("tool:post registration changed")
            if self._participant not in bridge._graph.participants:
                bridge._graph._invalidate("unowned tool:post registration")
        registration = _Registration(event, name if name is not None else
                                     f"_media_hook_{uuid4().hex}", priority,
                                     len(bridge._registrations),
                                     participant=self._participant, original=handler)

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
                        if result.action == "continue":
                            # Core chains only Modify. A declared result field
                            # must reach later callbacks in this same dispatch.
                            result = result.model_copy(update={"action": "modify"})
                        elif result.action != "modify":
                            # Injection/approval cannot also carry a replacement
                            # through Core's one-action boundary without loss.
                            witness.complete = False
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
        registration.observed = observed if event == _POST else handler
        if event == _POST:
            bridge._revision += 1

        def remove() -> None:
            if event == _POST and bridge._graph is not None:
                bridge._graph._mutation("tool:post registration removed")
            unregister()
            if registration.active:
                registration.active = False
                if event == _POST:
                    bridge._revision += 1

        return remove

    on = register

    def unregister(self, name: str) -> None:
        candidate = next((item for item in reversed(self._bridge._registrations)
                          if item.active and item.name == name), None)
        if candidate is not None and candidate.event == _POST and self._bridge._graph is not None:
            self._bridge._graph._mutation("tool:post registration removed")
        self._bridge._registry.unregister(name)
        for item in reversed(self._bridge._registrations):
            if item.active and item.name == name:
                item.active = False
                if item.event == _POST:
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
        graph = self._bridge._graph
        module_id = args[0] if args else kwargs["module_id"]
        config = args[1] if len(args) > 1 else kwargs.get("config")
        record = graph._loading(module_id, config, kwargs.get("source_hint")) if graph else None
        view = graph.participant(module_id) if graph else self._bridge.coordinator
        kwargs["coordinator"] = view
        try:
            mount = await self._loader.load(*args, **kwargs)
            if record is not None:
                record["mount"] = mount
        except BaseException:
            if graph:
                graph._invalidate("participating module load failed")
            raise

        async def mounted(coordinator: Any) -> Any:
            if graph:
                if self._bridge._graph is not graph:
                    self._bridge._graph._invalidate("stale module mount generation")
                    raise ValueError("mount callback belongs to a stale graph generation")
                graph._mutation("module mounted after seal")
                if record["mounted"]:
                    graph._invalidate("module mounted repeatedly")
            token = _mounting.set((graph, record)) if graph else None
            try:
                value = await mount(view)
                if record is not None:
                    record["mounted"] = True
                return value
            except BaseException:
                if graph:
                    graph._invalidate("participating module mount failed")
                raise
            finally:
                if token is not None:
                    _mounting.reset(token)

        if ready := getattr(mount, "__on_session_ready__", None):
            callback = graph._ready_callback(ready[1], module_id, record) if graph else self._ready(ready[1])
            mounted.__on_session_ready__ = (ready[0], callback)
        return mounted

    def enqueue_on_session_ready(self, module_id: str, callback: Any) -> None:
        # Returned mount metadata already owns the wrapped callback. Queue it
        # unchanged; retrieval wraps direct underlying-loader enqueues separately.
        graph = self._bridge._graph
        if graph:
            graph._mutation("ready callback enqueued after seal")
            graph.participant(module_id)
            if not any(callback is item for item in graph._wrapped_ready):
                callback = graph._ready_callback(callback, module_id)
        self._loader.enqueue_on_session_ready(module_id, callback)

    def get_on_session_ready_queue(self) -> Any:
        # Also cover callbacks enqueued inside the decorated loader itself.
        graph = self._bridge._graph
        result = []
        for name, callback in self._loader.get_on_session_ready_queue():
            if graph:
                if not any(callback is item for item in graph._wrapped_ready):
                    callback = graph._ready_callback(callback, name)
                result.append((name, callback))
            else:
                result.append((name, self._ready(callback)))
        return result


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
        self._graph: ParticipatingHookGraph | None = None
        self._transition: ParticipatingHookGraph | None = None
        self._active_boundaries = 0
        self._active_requests = 0
        self._processing: set[Any] = set()
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

    def begin_participating_graph(self, session: Any, *, owner: str,
                                  participants: tuple[str, ...]) -> ParticipatingHookGraph:
        """Start the sole construction record before actual session initialization.

        Explicit participant selection is a cooperative host obligation, not a
        claim of source approval or runtime isolation of arbitrary Python.
        """
        if self._graph is not None or self._registrations:
            raise ValueError("construction must begin before registrations; use a fresh session")
        if (type(session) is not AmplifierSession or session.initialized or
                session.coordinator is not self._coordinator):
            raise ValueError("construction requires its fresh actual Core session")
        if package_version("amplifier-core") != "2.0.2":
            raise ValueError("positive participating graph requires qualified Core ABI 2.0.2")
        if type(owner) is not str or not owner:
            raise ValueError("construction requires a named host owner")
        self._validate_participants(participants)
        if not {entry[0] for entry in _load_plan(session.config)} <= set(participants):
            raise ValueError("all configured modules must participate")
        self._graph = ParticipatingHookGraph(self, session, owner, participants)
        if any(value for _, value in self._graph._mounts()):
            self._graph._invalidate("preexisting unexplained module mounts")
            raise ValueError("participating construction requires unmounted fresh modules")
        return self._graph

    @staticmethod
    def _validate_participants(participants: tuple[str, ...]) -> None:
        if (type(participants) is not tuple or not participants or
                any(type(name) is not str or not name for name in participants) or
                len(set(participants)) != len(participants)):
            raise ValueError("construction requires distinct explicit participants")

    @contextmanager
    def request_scope(self):
        """Host preparation/refit/send owner prevents a concurrent graph change."""
        if self._transition is not None:
            raise ValueError("request cannot start during participating reconstruction")
        receipt = self.graph_receipt
        active = True
        def validate() -> bool:
            return active and self.validate_receipt(receipt)
        self._active_requests += 1
        try:
            yield validate
        finally:
            active = False
            self._active_requests -= 1

    def participant(self, name: str) -> Any:
        if self._graph is None:
            raise ValueError("no participating construction")
        return self._graph.participant(name)

    @property
    def graph_receipt(self) -> Any:
        return self._graph._receipt if self._graph is not None else None

    def validate_receipt(self, receipt: Any) -> bool:
        """Inspect this issuer's exact opaque construction receipt, never a report."""
        return self._graph is not None and self._graph._valid(receipt)

    def receipt_status(self, receipt: Any) -> dict[str, Any]:
        valid = self.validate_receipt(receipt)
        graph = self._graph
        reason = None if valid else (
            "unknown_receipt" if graph is None or receipt is not graph._receipt else
            graph._reason or "construction_not_sealed")
        return {"valid": valid, "reason": reason,
                "core_abi": package_version("amplifier-core"),
                "owner": graph.owner if graph is not None else None,
                "generation": graph._generation if graph is not None else None,
                "guarantee": "participating-cooperative-chain"}

    async def __call__(self, event: dict[str, Any], *, hooks: Any = None,
                       coordinator: Any = None) -> dict[str, Any]:
        if self._transition is not None:
            raise ValueError("tool:post cannot start during participating reconstruction")
        emitter, processor = self._bound_route(event, hooks, coordinator)
        receipt = self.graph_receipt
        qualified = self.validate_receipt(receipt)
        self._active_boundaries += 1
        try:
            observed = await self._observe(event, hooks=emitter, coordinator=processor)
        finally:
            self._active_boundaries -= 1
        complete = qualified and self.validate_receipt(receipt) and observed["complete"]
        return {"hook_result": observed["hook_result"],
                "result": observed["result"] if complete else None,
                "complete": complete}

    def _root_hooks(self, hooks: Any) -> bool:
        return (hooks is None or hooks is self._registry or hooks is self.hooks or
                (self._graph is not None and any(hooks is view.hooks for view in self._graph._views.values())))

    def _root_coordinator(self, coordinator: Any) -> bool:
        return (coordinator is None or coordinator is self._coordinator
                or coordinator is self.coordinator or
                (self._graph is not None and any(coordinator is view for view in self._graph._views.values())))

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

    def _routes_valid(self, routes: tuple[AttributedRouteLease, ...]) -> bool:
        scopes = _route_scopes.get()
        return all(item._active and item._bridge is self and
                   item._route.generation is self._graph and
                   any(scope is item for scope in scopes) for item in routes)

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
                                 deepcopy(source), observation, self._graph)
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
        witness.receipt = self.graph_receipt
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
