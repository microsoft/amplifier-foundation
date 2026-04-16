"""BundleInspector — read-only queries for SessionConfigurator.

Extracted from SessionConfigurator so that all query/view logic is in one
place, separate from state-mutation logic in BundleStateManager.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from amplifier_foundation.configurator._provenance_utils import (
    _PROV_CATEGORY_MAP,
    _build_normalized_prov_lookup,
    _lookup_prov_behavior,
)
from amplifier_foundation.dicts.navigation import get_nested

if TYPE_CHECKING:
    from amplifier_foundation.configurator._state_manager import BundleStateManager


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

        hooks_enabled = [
            name for name in hook_snapshot if name not in stash["hooks"]
        ]
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
    # List methods — dashboard views for each category
    # ------------------------------------------------------------------

    def context_list(self) -> list[dict]:
        """Return a list of all context entries with their enabled/disabled status."""
        bundle = self._state.bundle
        stash = self._state.stash
        provenance: dict[str, list[str]] = getattr(bundle, "_provenance", {})
        result: list[dict] = []

        for name, path in bundle.context.items():
            behavior = provenance.get(f"context:{name}")
            result.append(
                {
                    "name": name,
                    "path": str(path),
                    "enabled": True,
                    "behaviors": behavior,
                    "source": behavior,
                }
            )

        for name, path in stash["context"].items():
            behavior = provenance.get(f"context:{name}")
            result.append(
                {
                    "name": name,
                    "path": str(path),
                    "enabled": False,
                    "behaviors": behavior,
                    "source": behavior,
                }
            )

        return result

    def tools_list(self) -> list[dict]:
        """Return a list of all tools with their enabled/disabled status."""
        bundle = self._state.bundle
        stash = self._state.stash
        coordinator = self._state.coordinator
        tool_to_module = self._state.tool_to_module
        provenance: dict[str, list[str]] = getattr(bundle, "_provenance", {})

        norm_prov_map = _build_normalized_prov_lookup("tool", provenance)

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

        result: list[dict] = []

        try:
            mounted: dict = coordinator.get("tools") or {}
        except Exception:  # noqa: BLE001
            mounted = {}

        for name in mounted:
            behavior = _lookup_prov_behavior(name, "tool", provenance, norm_prov_map)
            from amplifier_foundation.configurator._provenance_utils import (
                _normalize_module_name,
            )

            norm_name = _normalize_module_name(name)
            result.append(
                {
                    "name": name,
                    "enabled": True,
                    "config": config_by_id.get(name, config_by_id.get(norm_name, {})),
                    "behaviors": behavior,
                    "source": behavior,
                    "module_id": tool_to_module.get(name, "unknown"),
                    "source_uri": source_by_id.get(name, source_by_id.get(norm_name)),
                }
            )

        for name in stash["tools"]:
            behavior = _lookup_prov_behavior(name, "tool", provenance, norm_prov_map)
            from amplifier_foundation.configurator._provenance_utils import (
                _normalize_module_name,
            )

            norm_name = _normalize_module_name(name)
            result.append(
                {
                    "name": name,
                    "enabled": False,
                    "config": config_by_id.get(name, config_by_id.get(norm_name, {})),
                    "behaviors": behavior,
                    "source": behavior,
                    "module_id": tool_to_module.get(name, "unknown"),
                    "source_uri": source_by_id.get(name, source_by_id.get(norm_name)),
                }
            )

        return result

    def hooks_list(self) -> list[dict]:
        """Return a list of all hooks (always enabled — hooks are read-only)."""
        bundle = self._state.bundle
        hook_snapshot = self._state.hook_snapshot
        hook_handler_to_module = self._state.hook_handler_to_module
        provenance: dict[str, list[str]] = getattr(bundle, "_provenance", {})

        norm_prov_map = _build_normalized_prov_lookup("hook", provenance)
        norm_tool_prov_map = _build_normalized_prov_lookup("tool", provenance)
        result: list[dict] = []

        for name, meta in hook_snapshot.items():
            behavior = _lookup_prov_behavior(name, "hook", provenance, norm_prov_map)

            if behavior is None:
                module_id = hook_handler_to_module.get(name)
                if module_id:
                    behavior = provenance.get(f"hook:{module_id}")
                    if behavior is None:
                        behavior = provenance.get(f"tool:{module_id}")
                    if behavior is None:
                        behavior = _lookup_prov_behavior(
                            module_id, "hook", provenance, norm_prov_map
                        ) or _lookup_prov_behavior(
                            module_id, "tool", provenance, norm_tool_prov_map
                        )

            result.append(
                {
                    "name": name,
                    "event": meta.get("event", ""),
                    "priority": meta.get("priority", 0),
                    "enabled": True,
                    "behaviors": behavior,
                    "source": behavior,
                }
            )

        return result

    def providers_list(self) -> list[dict]:
        """Return a list of all providers with their enabled/disabled status."""
        bundle = self._state.bundle
        stash = self._state.stash
        coordinator = self._state.coordinator
        provenance: dict[str, list[str]] = getattr(bundle, "_provenance", {})

        from amplifier_foundation.configurator._provenance_utils import (
            _normalize_module_name,
        )

        norm_prov_map = _build_normalized_prov_lookup("provider", provenance)

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

        result: list[dict] = []

        try:
            mounted: dict = coordinator.get("providers") or {}
        except Exception:  # noqa: BLE001
            mounted = {}

        if mounted:
            for name in mounted:
                behavior = _lookup_prov_behavior(
                    name, "provider", provenance, norm_prov_map
                )
                norm_name = _normalize_module_name(name)
                result.append(
                    {
                        "name": name,
                        "enabled": True,
                        "config": config_by_id.get(
                            name, config_by_id.get(norm_name, {})
                        ),
                        "behaviors": behavior,
                        "source": behavior,
                        "source_uri": source_by_id.get(
                            name, source_by_id.get(norm_name)
                        ),
                    }
                )

            for name in stash["providers"]:
                behavior = _lookup_prov_behavior(
                    name, "provider", provenance, norm_prov_map
                )
                norm_name = _normalize_module_name(name)
                result.append(
                    {
                        "name": name,
                        "enabled": False,
                        "config": config_by_id.get(
                            name, config_by_id.get(norm_name, {})
                        ),
                        "behaviors": behavior,
                        "source": behavior,
                        "source_uri": source_by_id.get(
                            name, source_by_id.get(norm_name)
                        ),
                    }
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
                behavior = _lookup_prov_behavior(
                    short_name, "provider", provenance, norm_prov_map
                ) or _lookup_prov_behavior(mid, "provider", provenance, norm_prov_map)
                result.append(
                    {
                        "name": short_name,
                        "enabled": enabled,
                        "config": config_by_id.get(
                            short_name, config_by_id.get(mid, {})
                        ),
                        "behaviors": behavior,
                        "source": behavior,
                        "source_uri": source_by_id.get(
                            short_name, source_by_id.get(mid)
                        ),
                    }
                )

            for name in stash["providers"]:
                if name not in added:
                    behavior = _lookup_prov_behavior(
                        name, "provider", provenance, norm_prov_map
                    )
                    norm_name = _normalize_module_name(name)
                    result.append(
                        {
                            "name": name,
                            "enabled": False,
                            "config": config_by_id.get(
                                name, config_by_id.get(norm_name, {})
                            ),
                            "behaviors": behavior,
                            "source": behavior,
                            "source_uri": source_by_id.get(
                                name, source_by_id.get(norm_name)
                            ),
                        }
                    )

        return result

    def agents_list(self) -> list[dict]:
        """Return a list of all agents with their enabled/disabled status."""
        bundle = self._state.bundle
        stash = self._state.stash
        coordinator = self._state.coordinator
        provenance: dict[str, list[str]] = getattr(bundle, "_provenance", {})
        result: list[dict] = []

        for name, cfg in coordinator.config.get("agents", {}).items():
            behavior = provenance.get(f"agent:{name}")
            result.append(
                {
                    "name": name,
                    "enabled": True,
                    "config": cfg if isinstance(cfg, dict) else {},
                    "behaviors": behavior,
                    "source": behavior,
                }
            )

        for name, cfg in stash["agents"].items():
            behavior = provenance.get(f"agent:{name}")
            result.append(
                {
                    "name": name,
                    "enabled": False,
                    "config": cfg if isinstance(cfg, dict) else {},
                    "behaviors": behavior,
                    "source": behavior,
                }
            )

        return result

    def behaviors_list(self) -> list[dict]:
        """Return a list of all behaviors derived from bundle provenance."""
        bundle = self._state.bundle
        disabled_behaviors = self._state.disabled_behaviors
        provenance: dict[str, list[str]] = getattr(bundle, "_provenance", {})

        behaviors: dict[str, dict[str, list[str]]] = {}
        for prov_key, behavior_names in provenance.items():
            if ":" not in prov_key:
                continue
            if not behavior_names:
                continue
            category, _ = prov_key.split(":", 1)
            plural_cat = _PROV_CATEGORY_MAP.get(category, category)
            for behavior_name in behavior_names:
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

        result: list[dict] = []
        for name, contrib_lists in behaviors.items():
            contributions = {cat: list(items) for cat, items in contrib_lists.items()}
            result.append(
                {
                    "name": name,
                    "enabled": name not in disabled_behaviors,
                    "contributions": contributions,
                    "root_namespace": self._state._get_behavior_root_namespace(name),
                }
            )

        return sorted(result, key=lambda x: x["name"])

    # ------------------------------------------------------------------
    # Config get
    # ------------------------------------------------------------------

    def config_get(self, path: str) -> Any:
        """Return the value at a dot-separated path in coordinator.config."""
        keys = path.split(".")
        return get_nested(self._state.coordinator.config, keys)
