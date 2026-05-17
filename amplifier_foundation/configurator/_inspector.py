"""BundleInspector — read-only queries for SessionConfigurator.

Extracted from SessionConfigurator so that all query/view logic is in one
place, separate from state-mutation logic in BundleStateManager.
"""

from __future__ import annotations

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


def _build_include_path(
    bundle_name: str,
    source_base_paths: dict,
) -> list[IncludeStep]:
    """Build a best-effort include path from source_base_paths.

    Full registry-based chain traversal is not available without BundleRegistry
    access; this returns a flat list of all namespaces reachable through
    source_base_paths, ordered with the given bundle_name last (leaf).

    Args:
        bundle_name:       The bundle name for the item's origin.
        source_base_paths: Bundle.source_base_paths mapping namespace → path.

    Returns:
        list[IncludeStep] ordered root→leaf, version/uri left as None.
    """
    if not bundle_name or not source_base_paths:
        return (
            [IncludeStep(bundle=bundle_name, version=None, uri=None)]
            if bundle_name
            else []
        )

    steps: list[IncludeStep] = []
    # Add all other namespaces as earlier steps (root → leaf order heuristic:
    # alphabetically shorter names tend to be higher in the include tree).
    other_ns = sorted(
        (ns for ns in source_base_paths if ns != bundle_name),
        key=len,
    )
    for ns in other_ns:
        steps.append(IncludeStep(bundle=ns, version=None, uri=None))

    # Always append the specific bundle as the leaf
    if bundle_name not in other_ns:
        steps.append(IncludeStep(bundle=bundle_name, version=None, uri=None))

    return steps


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
        source_base_paths: dict = getattr(bundle, "source_base_paths", {}) or {}
        result: list[ItemRecord] = []

        for name, path in bundle.context.items():
            origin_list = _as_origin_list(origins.get(f"context:{name}"))
            include_path = []
            if origin_list:
                include_path = _build_include_path(
                    origin_list[0].bundle, source_base_paths
                )
            result.append(
                ItemRecord(
                    category="context",
                    name=name,
                    enabled=True,
                    module_id=None,
                    source_uri=str(path),
                    config_summary={},
                    origins=origin_list,
                    include_path=include_path,
                    runtime_injection=_runtime_injection_from_origins(origin_list),
                )
            )

        for name, path in stash["context"].items():
            origin_list = _as_origin_list(origins.get(f"context:{name}"))
            include_path = []
            if origin_list:
                include_path = _build_include_path(
                    origin_list[0].bundle, source_base_paths
                )
            result.append(
                ItemRecord(
                    category="context",
                    name=name,
                    enabled=False,
                    module_id=None,
                    source_uri=str(path),
                    config_summary={},
                    origins=origin_list,
                    include_path=include_path,
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
        source_base_paths: dict = getattr(bundle, "source_base_paths", {}) or {}
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
            include_path = []
            if origin_list:
                include_path = _build_include_path(
                    origin_list[0].bundle, source_base_paths
                )
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
                    include_path=include_path,
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
            include_path = []
            if origin_list:
                include_path = _build_include_path(
                    origin_list[0].bundle, source_base_paths
                )
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
                    include_path=include_path,
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
        source_base_paths: dict = getattr(bundle, "source_base_paths", {}) or {}
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
            include_path = []
            if origin_list:
                include_path = _build_include_path(
                    origin_list[0].bundle, source_base_paths
                )
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
                    include_path=include_path,
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
        source_base_paths: dict = getattr(bundle, "source_base_paths", {}) or {}
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
                include_path = []
                if origin_list:
                    include_path = _build_include_path(
                        origin_list[0].bundle, source_base_paths
                    )
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
                        include_path=include_path,
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
                include_path = []
                if origin_list:
                    include_path = _build_include_path(
                        origin_list[0].bundle, source_base_paths
                    )
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
                        include_path=include_path,
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
                include_path = []
                if origin_list:
                    include_path = _build_include_path(
                        origin_list[0].bundle, source_base_paths
                    )
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
                        include_path=include_path,
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
                    include_path = []
                    if origin_list:
                        include_path = _build_include_path(
                            origin_list[0].bundle, source_base_paths
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
                            include_path=include_path,
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
        source_base_paths: dict = getattr(bundle, "source_base_paths", {}) or {}
        result: list[ItemRecord] = []

        for name, cfg in coordinator.config.get("agents", {}).items():
            origin_list = _as_origin_list(origins.get(f"agent:{name}"))
            include_path = []
            if origin_list:
                include_path = _build_include_path(
                    origin_list[0].bundle, source_base_paths
                )
            result.append(
                ItemRecord(
                    category="agent",
                    name=name,
                    enabled=True,
                    module_id=None,
                    source_uri=None,
                    config_summary=_redact_config(cfg if isinstance(cfg, dict) else {}),
                    origins=origin_list,
                    include_path=include_path,
                    runtime_injection=_runtime_injection_from_origins(origin_list),
                )
            )

        for name, cfg in stash["agents"].items():
            origin_list = _as_origin_list(origins.get(f"agent:{name}"))
            include_path = []
            if origin_list:
                include_path = _build_include_path(
                    origin_list[0].bundle, source_base_paths
                )
            result.append(
                ItemRecord(
                    category="agent",
                    name=name,
                    enabled=False,
                    module_id=None,
                    source_uri=None,
                    config_summary=_redact_config(cfg if isinstance(cfg, dict) else {}),
                    origins=origin_list,
                    include_path=include_path,
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
                    include_path=[],
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
