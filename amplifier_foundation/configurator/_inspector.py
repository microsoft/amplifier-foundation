"""BundleInspector — read-only queries for SessionConfigurator.

Extracted from SessionConfigurator so that all query/view logic is in one
place, separate from state-mutation logic in BundleStateManager.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal, cast

from amplifier_foundation.configurator._provenance_utils import (
    _PROV_CATEGORY_MAP,
    _build_normalized_prov_lookup,
    _lookup_prov_origins,
)
from amplifier_foundation.configurator._types import (
    IncludeStep,
    ItemRecord,
    Origin,
)
from amplifier_foundation.dicts.navigation import get_nested

if TYPE_CHECKING:
    from amplifier_foundation.configurator._state_manager import BundleStateManager

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel for redaction
# ---------------------------------------------------------------------------

_SENSITIVE_KEY_PATTERNS = ("key", "token", "secret", "password", "api_key")


def _redact_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive values in a config dict (shallow pass)."""
    if not isinstance(cfg, dict):
        return {}
    result: dict[str, Any] = {}
    for k, v in cfg.items():
        if (
            isinstance(v, str)
            and len(v) > 20
            and any(p in k.lower() for p in _SENSITIVE_KEY_PATTERNS)
        ):
            result[k] = f"{v[:4]}...redacted"
        else:
            result[k] = v
    return result


def _as_origin_list(raw: list | None) -> list[Origin]:
    """Convert a raw list (list[str] or list[Origin]) to list[Origin].

    Handles legacy test fixtures that store plain strings instead of Origin objects.
    """
    if not raw:
        return []
    result = []
    for item in raw:
        if isinstance(item, Origin):
            result.append(item)
        elif isinstance(item, str):
            result.append(Origin(bundle=item, via_behavior=None))
    return result


def walk_include_chain(
    bundle_name: str,
    registry_dict: "dict[str, Any]",
) -> list[IncludeStep]:
    """Walk BundleRegistry._registry's included_by graph from leaf to root.

    Returns an ordered list of :class:`IncludeStep` objects from the root
    bundle down to *bundle_name* (root→leaf).  If the bundle appears in
    multiple inclusion graphs (e.g., carried by two parents), the first
    ``explicitly_requested`` or ``is_root`` parent is preferred; otherwise the
    first parent in ``included_by`` is followed.

    This is the canonical helper for building ``ItemRecord.include_path`` and
    for rendering ``bundle show <name>`` include chains (Commit 3).

    Args:
        bundle_name:   Name of the leaf bundle whose inclusion chain to trace.
        registry_dict: The ``_registry`` dict from a
            :class:`~amplifier_foundation.registry.BundleRegistry` instance.

    Returns:
        ``list[IncludeStep]`` ordered root→leaf.  Falls back to a single-entry
        list ``[IncludeStep(bundle_name, None, None)]`` when the bundle is not
        found in the registry or has no ``included_by`` data.
    """
    if not registry_dict or bundle_name not in registry_dict:
        return (
            [IncludeStep(bundle=bundle_name, version=None, uri=None)]
            if bundle_name
            else []
        )

    # Walk leaf→root following included_by links.
    chain: list[str] = []
    visited: set[str] = set()
    current: str | None = bundle_name

    while current and current not in visited:
        visited.add(current)
        chain.append(current)

        state = registry_dict.get(current)
        if state is None:
            break

        included_by: list[str] = getattr(state, "included_by", None) or []
        if not included_by:
            break

        # Prefer a parent that is the explicitly-requested root (the user's
        # active bundle) or any root bundle; fall back to the first parent.
        parent: str | None = None
        for p in included_by:
            p_state = registry_dict.get(p)
            if p_state is None:
                continue
            if getattr(p_state, "explicitly_requested", False) or getattr(
                p_state, "is_root", False
            ):
                parent = p
                break
        if parent is None:
            parent = included_by[0]

        current = parent

    # chain is leaf→root; reverse for root→leaf display.
    chain.reverse()

    return [
        IncludeStep(
            bundle=name,
            version=getattr(registry_dict.get(name), "version", None),
            uri=getattr(registry_dict.get(name), "uri", None),
        )
        for name in chain
    ]


def walk_include_chains(
    bundle_name: str,
    registry_dict: "dict[str, Any]",
    *,
    max_paths: int = 10,
) -> list[list[IncludeStep]]:
    """Walk the included_by graph and return ALL distinct root→leaf paths.

    Returns every distinct path from a root bundle down to *bundle_name*.
    A root is a bundle whose ``included_by`` list is empty or None.
    Each inner list is one path ordered root→leaf.

    The existing :func:`walk_include_chain` (singular) stays unchanged and
    returns the first path from this function.  Compact / regular views use
    the singular helper; the detailed view uses this plural version.

    Args:
        bundle_name:   Name of the leaf bundle whose inclusion chains to trace.
        registry_dict: The ``_registry`` dict from a BundleRegistry instance.
        max_paths:     Cap on the number of paths returned (default 10).

    Returns:
        ``list[list[IncludeStep]]`` — one inner list per distinct path.
        Falls back to ``[[IncludeStep(bundle_name, None, None)]]`` when the
        bundle is not found or has no registry data.
    """
    if not registry_dict or bundle_name not in registry_dict:
        if bundle_name:
            return [[IncludeStep(bundle=bundle_name, version=None, uri=None)]]
        return []

    def _make_step(name: str) -> IncludeStep:
        state = registry_dict.get(name)
        return IncludeStep(
            bundle=name,
            version=getattr(state, "version", None),
            uri=getattr(state, "uri", None),
        )

    def _all_paths_to_root(current: str, visited: frozenset[str]) -> list[list[str]]:
        """Return all root→leaf paths ending at *current* as lists of bundle names."""
        if current in visited:
            _logger.warning(
                "Cycle detected in bundle include graph at %r; breaking chain.",
                current,
            )
            return [[current]]

        state = registry_dict.get(current)
        if state is None:
            return [[current]]

        included_by: list[str] = getattr(state, "included_by", None) or []

        # No parents ⟹ this is a root node.
        if not included_by:
            return [[current]]

        new_visited = visited | {current}
        all_paths: list[list[str]] = []

        for parent in included_by:
            parent_paths = _all_paths_to_root(parent, new_visited)
            for path in parent_paths:
                all_paths.append(path + [current])
                if len(all_paths) >= max_paths:
                    return all_paths

        # If all parents were cycles / missing, treat current as a root.
        return all_paths if all_paths else [[current]]

    raw_paths = _all_paths_to_root(bundle_name, frozenset())

    return [[_make_step(name) for name in path] for path in raw_paths[:max_paths]]


def _build_include_path(
    bundle_name: str,
    registry_dict: "dict[str, Any] | None",
) -> list[IncludeStep]:
    """Build the include path for *bundle_name* from BundleRegistry state.

    Delegates to :func:`walk_include_chain` when *registry_dict* is provided.
    Falls back to a single-entry list when registry data is unavailable.

    Args:
        bundle_name:  The bundle name for the item's origin.
        registry_dict: The ``_registry`` dict from BundleRegistry, or None.

    Returns:
        list[IncludeStep] ordered root→leaf.
    """
    if not bundle_name:
        return []
    if registry_dict:
        return walk_include_chain(bundle_name, registry_dict)
    # Fallback: no registry access — return a single-node chain.
    return [IncludeStep(bundle=bundle_name, version=None, uri=None)]


def _build_include_paths(
    origins: "list[Origin]",
    registry_dict: "dict[str, Any] | None",
) -> list[list[IncludeStep]]:
    """Build all include paths for an item from its origins list.

    For items with a single claimant origin, returns the chain(s) for that
    bundle.  For items with multiple distinct claimant bundles (different
    ``Origin.bundle`` values), unions the chains across all claimants,
    de-duplicating identical paths.

    Args:
        origins:       The item's origin list (from ``ItemRecord.origins``).
        registry_dict: The ``_registry`` dict from BundleRegistry, or None.

    Returns:
        ``list[list[IncludeStep]]`` — the union of chains for all claimants.
        Empty list when *origins* is empty.
    """
    if not origins:
        return []

    # Collect distinct bundle names from origins, direct claimants first.
    seen_bundles: set[str] = set()
    claimant_bundles: list[str] = []
    for origin in origins:
        if origin.bundle and origin.bundle not in seen_bundles:
            seen_bundles.add(origin.bundle)
            claimant_bundles.append(origin.bundle)

    if not claimant_bundles:
        return []

    if not registry_dict:
        # Fallback: one single-step path per distinct bundle.
        return [
            [IncludeStep(bundle=b, version=None, uri=None)] for b in claimant_bundles
        ]

    # Walk chains for each claimant, union with de-duplication.
    all_paths: list[list[IncludeStep]] = []
    seen_path_keys: set[tuple[str, ...]] = set()

    for bundle_name in claimant_bundles:
        for path in walk_include_chains(bundle_name, registry_dict):
            key = tuple(step.bundle for step in path)
            if key not in seen_path_keys:
                seen_path_keys.add(key)
                all_paths.append(path)

    return (
        all_paths
        if all_paths
        else [[IncludeStep(bundle=b, version=None, uri=None)] for b in claimant_bundles]
    )


def _runtime_injection_from_origins(
    origin_list: list[Origin] | None,
) -> "Literal['static', 'mode', 'hook', 'skills', 'mcp', 'task'] | None":
    """Determine runtime_injection label from a list of Origins.

    Returns:
        ``"mode"`` if any origin bundle starts with ``"mode:"``.
        ``"static"`` if origin_list is non-empty and has no mode prefix.
        ``None`` if origin_list is empty or None.
    """
    if not origin_list:
        return None
    for o in origin_list:
        if o.bundle.startswith("mode:"):
            return cast("Literal['mode']", "mode")
    return cast("Literal['static']", "static")


class BundleInspector:
    """Read-only views over bundle state for the /config dashboard.

    BundleInspector holds no mutable state of its own (except
    ``_original_snapshot`` for diff tracking).  All data is read from the
    :class:`BundleStateManager` passed at construction time.
    """

    def __init__(self, state: "BundleStateManager") -> None:
        self._state = state
        # Initialised to None; set by take_snapshot() once the session is ready.
        self._original_snapshot: dict[str, Any] | None = None
        # BundleRegistry _registry dict for include-chain resolution (Bug B fix).
        # Loaded lazily on first access; None if unavailable (tests, edge cases).
        self._registry_dict: dict[str, Any] | None = self._load_registry_dict()

    def _load_registry_dict(self) -> dict[str, Any] | None:
        """Load the BundleRegistry _registry dict from persisted state.

        Returns the registry dict on success, or None if the registry cannot
        be loaded (e.g. in tests or environments without a home directory).
        """
        try:
            from amplifier_foundation.registry import BundleRegistry

            registry = BundleRegistry()
            return dict(registry._registry)
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # Snapshot helpers
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return a snapshot of the current session state."""
        stash = self._state.stash
        bundle = self._state.bundle
        coordinator = self._state.coordinator
        hook_snapshot = self._state.hook_snapshot

        context_enabled = list(bundle.context.keys())
        context_disabled = list(stash["context"].keys())

        agents_enabled = list(coordinator.config.get("agents", {}).keys())
        agents_disabled = list(stash["agents"].keys())

        tools_enabled = [
            mid
            for mod in bundle.tools
            if (mid := mod.get("id") or mod.get("module")) and mid not in stash["tools"]
        ]
        tools_disabled = list(stash["tools"].keys())

        providers_enabled = [
            mid
            for mod in bundle.providers
            if (mid := mod.get("id") or mod.get("module"))
            and mid not in stash["providers"]
        ]
        providers_disabled = list(stash["providers"].keys())

        hooks_enabled = [name for name in hook_snapshot if name not in stash["hooks"]]
        hooks_disabled = list(stash["hooks"].keys())

        return {
            "context": {"enabled": context_enabled, "disabled": context_disabled},
            "tools": {"enabled": tools_enabled, "disabled": tools_disabled},
            "hooks": {"enabled": hooks_enabled, "disabled": hooks_disabled},
            "providers": {"enabled": providers_enabled, "disabled": providers_disabled},
            "agents": {"enabled": agents_enabled, "disabled": agents_disabled},
        }

    def diff_from_original(self) -> list[dict[str, Any]]:
        """Return a list of changes compared to the original snapshot."""
        if self._original_snapshot is None:
            return []

        current = self.snapshot()
        changes: list[dict[str, Any]] = []

        for category in ("context", "tools", "hooks", "providers", "agents"):
            orig_enabled = set(
                self._original_snapshot.get(category, {}).get("enabled", [])
            )
            curr_enabled = set(current.get(category, {}).get("enabled", []))

            for name in orig_enabled - curr_enabled:
                changes.append(
                    {"category": category, "name": name, "action": "disabled"}
                )

            for name in curr_enabled - orig_enabled:
                changes.append(
                    {"category": category, "name": name, "action": "enabled"}
                )

        return changes

    def take_snapshot(self) -> None:
        """Capture a snapshot and store it as the original reference."""
        self._original_snapshot = self.snapshot()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_origins_for(self, prov_key: str) -> list[Origin]:
        """Return origins list for a raw prov_key, or empty list."""
        bundle = self._state.bundle
        return getattr(bundle, "origins", {}).get(prov_key) or []

    def _get_module_exports(self) -> dict[str, list[str]]:
        """Return module_exports from the prepared bundle, or empty dict."""
        pb = getattr(self._state, "_prepared_bundle", None)
        if pb is None:
            return {}
        return getattr(pb, "module_exports", {}) or {}

    def _get_source_by_module_id(self, coordinator: Any) -> dict[str, str | None]:
        """Build module_id -> source_uri map from coordinator tool/hook/provider specs."""
        source_by_id: dict[str, str | None] = {}
        for section in ("tools", "hooks", "providers"):
            for spec in coordinator.config.get(section, []):
                if isinstance(spec, dict):
                    mid = spec.get("id") or spec.get("module", "")
                    src = spec.get("source")
                    if mid:
                        source_by_id[mid] = src
        return source_by_id

    # ------------------------------------------------------------------
    # List methods — dashboard views for each category
    # ------------------------------------------------------------------

    def context_list(self) -> list[ItemRecord]:
        """Return a list of all context entries with their enabled/disabled status."""
        bundle = self._state.bundle
        stash = self._state.stash
        origins: dict[str, list[Origin]] = getattr(bundle, "origins", {})

        result: list[ItemRecord] = []

        for name, path in bundle.context.items():
            origin_list = _as_origin_list(origins.get(f"context:{name}"))
            include_paths = _build_include_paths(origin_list, self._registry_dict)
            result.append(
                ItemRecord(
                    category="context",
                    name=name,
                    enabled=True,
                    module_id=None,
                    source_uri=str(path),
                    config_summary={},
                    origins=origin_list,
                    include_paths=include_paths,
                    runtime_injection=_runtime_injection_from_origins(origin_list),
                )
            )

        for name, path in stash["context"].items():
            origin_list = _as_origin_list(origins.get(f"context:{name}"))
            include_paths = _build_include_paths(origin_list, self._registry_dict)
            result.append(
                ItemRecord(
                    category="context",
                    name=name,
                    enabled=False,
                    module_id=None,
                    source_uri=str(path),
                    config_summary={},
                    origins=origin_list,
                    include_paths=include_paths,
                    runtime_injection=_runtime_injection_from_origins(origin_list),
                )
            )

        return result

    def tools_list(self) -> list[ItemRecord]:
        """Return a list of all tools with their enabled/disabled status."""
        bundle = self._state.bundle
        stash = self._state.stash
        coordinator = self._state.coordinator
        tool_to_module = self._state.tool_to_module
        origins: dict[str, list[Origin]] = getattr(bundle, "origins", {})

        module_exports = self._get_module_exports()

        norm_prov_map = _build_normalized_prov_lookup("tool", origins)

        config_by_id: dict[str, dict] = {}
        source_by_id: dict[str, str | None] = {}
        for spec in coordinator.config.get("tools", []):
            if isinstance(spec, dict):
                mid = spec.get("id") or spec.get("module", "")
                cfg = spec.get("config") or {}
                src = spec.get("source")
                if mid:
                    from amplifier_foundation.configurator._provenance_utils import (
                        _normalize_module_name,
                    )

                    config_by_id[mid] = cfg
                    source_by_id[mid] = src
                    short = mid[5:] if mid.startswith("tool-") else mid
                    config_by_id[short] = cfg
                    source_by_id[short] = src
                    config_by_id[_normalize_module_name(short)] = cfg
                    source_by_id[_normalize_module_name(short)] = src

        result: list[ItemRecord] = []

        try:
            mounted: dict = coordinator.get("tools") or {}
        except Exception:  # noqa: BLE001
            mounted = {}

        for name in mounted:
            origin_list = (
                _lookup_prov_origins(
                    name, "tool", origins, norm_prov_map, module_exports
                )
                or []
            )
            from amplifier_foundation.configurator._provenance_utils import (
                _normalize_module_name,
            )

            norm_name = _normalize_module_name(name)
            module_id = tool_to_module.get(name, "unknown")
            include_paths = _build_include_paths(origin_list, self._registry_dict)
            result.append(
                ItemRecord(
                    category="tool",
                    name=name,
                    enabled=True,
                    module_id=module_id,
                    source_uri=source_by_id.get(name, source_by_id.get(norm_name)),
                    config_summary=_redact_config(
                        config_by_id.get(name, config_by_id.get(norm_name, {}))
                    ),
                    origins=origin_list,
                    include_paths=include_paths,
                    runtime_injection=_runtime_injection_from_origins(origin_list),
                )
            )

        for name in stash["tools"]:
            origin_list = (
                _lookup_prov_origins(
                    name, "tool", origins, norm_prov_map, module_exports
                )
                or []
            )
            from amplifier_foundation.configurator._provenance_utils import (
                _normalize_module_name,
            )

            norm_name = _normalize_module_name(name)
            module_id = tool_to_module.get(name, "unknown")
            include_paths = _build_include_paths(origin_list, self._registry_dict)
            result.append(
                ItemRecord(
                    category="tool",
                    name=name,
                    enabled=False,
                    module_id=module_id,
                    source_uri=source_by_id.get(name, source_by_id.get(norm_name)),
                    config_summary=_redact_config(
                        config_by_id.get(name, config_by_id.get(norm_name, {}))
                    ),
                    origins=origin_list,
                    include_paths=include_paths,
                    runtime_injection=_runtime_injection_from_origins(origin_list),
                )
            )

        return result

    def hooks_list(self) -> list[ItemRecord]:
        """Return a list of all hooks (always enabled — hooks are read-only)."""
        bundle = self._state.bundle
        hook_snapshot = self._state.hook_snapshot
        hook_handler_to_module = self._state.hook_handler_to_module
        origins: dict[str, list[Origin]] = getattr(bundle, "origins", {})

        module_exports = self._get_module_exports()

        norm_prov_map = _build_normalized_prov_lookup("hook", origins)
        norm_tool_prov_map = _build_normalized_prov_lookup("tool", origins)
        result: list[ItemRecord] = []

        for name, meta in hook_snapshot.items():
            origin_list = _lookup_prov_origins(
                name, "hook", origins, norm_prov_map, module_exports
            )

            if origin_list is None:
                module_id = hook_handler_to_module.get(name)
                if module_id:
                    origin_list = _as_origin_list(origins.get(f"hook:{module_id}"))
                    if origin_list is None:
                        origin_list = _as_origin_list(origins.get(f"tool:{module_id}"))
                    if origin_list is None:
                        origin_list = _lookup_prov_origins(
                            module_id, "hook", origins, norm_prov_map, module_exports
                        ) or _lookup_prov_origins(
                            module_id,
                            "tool",
                            origins,
                            norm_tool_prov_map,
                            module_exports,
                        )

            origin_list = origin_list or []
            include_paths = _build_include_paths(origin_list, self._registry_dict)
            result.append(
                ItemRecord(
                    category="hook",
                    name=name,
                    enabled=True,
                    module_id=hook_handler_to_module.get(name),
                    source_uri=None,
                    config_summary={
                        "event": meta.get("event", ""),
                        "priority": meta.get("priority", 0),
                    },
                    origins=origin_list,
                    include_paths=include_paths,
                    runtime_injection=_runtime_injection_from_origins(origin_list),
                )
            )

        return result

    def providers_list(self) -> list[ItemRecord]:
        """Return a list of all providers with their enabled/disabled status."""
        bundle = self._state.bundle
        stash = self._state.stash
        coordinator = self._state.coordinator
        origins: dict[str, list[Origin]] = getattr(bundle, "origins", {})

        module_exports = self._get_module_exports()

        from amplifier_foundation.configurator._provenance_utils import (
            _normalize_module_name,
        )

        norm_prov_map = _build_normalized_prov_lookup("provider", origins)

        config_by_id: dict[str, dict] = {}
        source_by_id: dict[str, str | None] = {}
        for spec in coordinator.config.get("providers", []):
            if isinstance(spec, dict):
                mid = spec.get("id") or spec.get("module", "")
                cfg = spec.get("config") or {}
                src = spec.get("source")
                if mid:
                    config_by_id[mid] = cfg
                    source_by_id[mid] = src
                    short = mid[9:] if mid.startswith("provider-") else mid
                    config_by_id[short] = cfg
                    source_by_id[short] = src
                    config_by_id[_normalize_module_name(short)] = cfg
                    source_by_id[_normalize_module_name(short)] = src

        result: list[ItemRecord] = []

        try:
            mounted: dict = coordinator.get("providers") or {}
        except Exception:  # noqa: BLE001
            mounted = {}

        if mounted:
            for name in mounted:
                origin_list = (
                    _lookup_prov_origins(
                        name, "provider", origins, norm_prov_map, module_exports
                    )
                    or []
                )
                norm_name = _normalize_module_name(name)
                include_paths = _build_include_paths(origin_list, self._registry_dict)
                result.append(
                    ItemRecord(
                        category="provider",
                        name=name,
                        enabled=True,
                        module_id=None,
                        source_uri=source_by_id.get(name, source_by_id.get(norm_name)),
                        config_summary=_redact_config(
                            config_by_id.get(name, config_by_id.get(norm_name, {}))
                        ),
                        origins=origin_list,
                        include_paths=include_paths,
                        runtime_injection=_runtime_injection_from_origins(origin_list),
                    )
                )

            for name in stash["providers"]:
                origin_list = (
                    _lookup_prov_origins(
                        name, "provider", origins, norm_prov_map, module_exports
                    )
                    or []
                )
                norm_name = _normalize_module_name(name)
                include_paths = _build_include_paths(origin_list, self._registry_dict)
                result.append(
                    ItemRecord(
                        category="provider",
                        name=name,
                        enabled=False,
                        module_id=None,
                        source_uri=source_by_id.get(name, source_by_id.get(norm_name)),
                        config_summary=_redact_config(
                            config_by_id.get(name, config_by_id.get(norm_name, {}))
                        ),
                        origins=origin_list,
                        include_paths=include_paths,
                        runtime_injection=_runtime_injection_from_origins(origin_list),
                    )
                )
        else:
            added: set[str] = set()
            for spec in coordinator.config.get("providers", []):
                if not isinstance(spec, dict):
                    continue
                mid = spec.get("id") or spec.get("module", "")
                if not mid:
                    continue
                short_name = mid[9:] if mid.startswith("provider-") else mid
                if short_name in added:
                    continue
                added.add(short_name)
                enabled = (
                    short_name not in stash["providers"]
                    and mid not in stash["providers"]
                )
                origin_list = (
                    _lookup_prov_origins(
                        short_name, "provider", origins, norm_prov_map, module_exports
                    )
                    or _lookup_prov_origins(
                        mid, "provider", origins, norm_prov_map, module_exports
                    )
                    or []
                )
                include_paths = _build_include_paths(origin_list, self._registry_dict)
                result.append(
                    ItemRecord(
                        category="provider",
                        name=short_name,
                        enabled=enabled,
                        module_id=None,
                        source_uri=source_by_id.get(short_name, source_by_id.get(mid)),
                        config_summary=_redact_config(
                            config_by_id.get(short_name, config_by_id.get(mid, {}))
                        ),
                        origins=origin_list,
                        include_paths=include_paths,
                        runtime_injection=_runtime_injection_from_origins(origin_list),
                    )
                )

            for name in stash["providers"]:
                if name not in added:
                    origin_list = (
                        _lookup_prov_origins(
                            name, "provider", origins, norm_prov_map, module_exports
                        )
                        or []
                    )
                    norm_name = _normalize_module_name(name)
                    include_paths = _build_include_paths(
                        origin_list, self._registry_dict
                    )
                    result.append(
                        ItemRecord(
                            category="provider",
                            name=name,
                            enabled=False,
                            module_id=None,
                            source_uri=source_by_id.get(
                                name, source_by_id.get(norm_name)
                            ),
                            config_summary=_redact_config(
                                config_by_id.get(name, config_by_id.get(norm_name, {}))
                            ),
                            origins=origin_list,
                            include_paths=include_paths,
                            runtime_injection=_runtime_injection_from_origins(
                                origin_list
                            ),
                        )
                    )

        return result

    def agents_list(self) -> list[ItemRecord]:
        """Return a list of all agents with their enabled/disabled status."""
        bundle = self._state.bundle
        stash = self._state.stash
        coordinator = self._state.coordinator
        origins: dict[str, list[Origin]] = getattr(bundle, "origins", {})

        result: list[ItemRecord] = []

        for name, cfg in coordinator.config.get("agents", {}).items():
            origin_list = _as_origin_list(origins.get(f"agent:{name}"))
            include_paths = _build_include_paths(origin_list, self._registry_dict)
            result.append(
                ItemRecord(
                    category="agent",
                    name=name,
                    enabled=True,
                    module_id=None,
                    source_uri=None,
                    config_summary=_redact_config(cfg if isinstance(cfg, dict) else {}),
                    origins=origin_list,
                    include_paths=include_paths,
                    runtime_injection=_runtime_injection_from_origins(origin_list),
                )
            )

        for name, cfg in stash["agents"].items():
            origin_list = _as_origin_list(origins.get(f"agent:{name}"))
            include_paths = _build_include_paths(origin_list, self._registry_dict)
            result.append(
                ItemRecord(
                    category="agent",
                    name=name,
                    enabled=False,
                    module_id=None,
                    source_uri=None,
                    config_summary=_redact_config(cfg if isinstance(cfg, dict) else {}),
                    origins=origin_list,
                    include_paths=include_paths,
                    runtime_injection=_runtime_injection_from_origins(origin_list),
                )
            )

        return result

    def behaviors_list(self) -> list[ItemRecord]:
        """Return a list of all behaviors derived from bundle origins."""
        bundle = self._state.bundle
        disabled_behaviors = self._state.disabled_behaviors
        origins: dict[str, list[Origin]] = getattr(bundle, "origins", {})

        # Collect the set of unique bundle names that appear in origins
        # as direct claimants (via_behavior=None means self-introduced).
        behaviors: dict[str, dict[str, list[str]]] = {}
        for prov_key, raw_list in origins.items():
            if ":" not in prov_key:
                continue
            if not raw_list:
                continue
            origin_list = _as_origin_list(raw_list)
            category, _ = prov_key.split(":", 1)
            plural_cat = _PROV_CATEGORY_MAP.get(category, category)
            for origin in origin_list:
                behavior_name = origin.bundle
                if not behavior_name:
                    continue
                if behavior_name not in behaviors:
                    behaviors[behavior_name] = {
                        "context": [],
                        "tools": [],
                        "hooks": [],
                        "providers": [],
                        "agents": [],
                    }
                if plural_cat in behaviors[behavior_name]:
                    behaviors[behavior_name][plural_cat].append(prov_key)

        result: list[ItemRecord] = []
        for name, contrib_lists in behaviors.items():
            contributions: dict[str, Any] = {
                cat: list(items) for cat, items in contrib_lists.items()
            }
            # Include root_namespace for behaviors that have a discoverable namespace root.
            root_ns = self._state._get_behavior_root_namespace(name)
            if root_ns is not None:
                contributions["root_namespace"] = root_ns

            # Build a minimal ItemRecord for each behavior.
            # The origins here reflect what claimants THAT behavior itself
            # introduced — for behaviors, there's no separate origins chain,
            # so we leave origins empty.
            result.append(
                ItemRecord(
                    category="behavior",
                    name=name,
                    enabled=name not in disabled_behaviors,
                    module_id=None,
                    source_uri=None,
                    config_summary=contributions,
                    origins=[],
                    include_paths=[],
                    runtime_injection=None,
                )
            )

        return sorted(result, key=lambda x: x.name)

    # ------------------------------------------------------------------
    # Config get
    # ------------------------------------------------------------------

    def config_get(self, path: str) -> Any:
        """Return the value at a dot-separated path in coordinator.config."""
        keys = path.split(".")
        return get_nested(self._state.coordinator.config, keys)
