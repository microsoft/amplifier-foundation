"""BundleStateManager — all mutation logic for SessionConfigurator.

Extracted from SessionConfigurator so that query-only methods (BundleInspector)
are separated from state-mutation methods.  SessionConfigurator in __init__.py
is a thin facade that creates both objects and delegates every public method.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from amplifier_foundation.configurator._provenance_utils import _normalize_module_name
from amplifier_foundation.dicts.navigation import set_nested

_log = logging.getLogger(__name__)


class BundleStateManager:
    """Holds all mutable session state and exposes mutation methods.

    BundleStateManager owns the stash, hook snapshot, module-to-tool mappings,
    disabled-behavior tracking, and config overrides.  It also implements all
    methods that mutate these structures: enable/disable toggles, behavior
    group operations, config_set, save, and apply_saved_settings.
    """

    def __init__(self, session: Any, prepared_bundle: Any) -> None:
        self._session = session
        self._coordinator = session.coordinator
        self._bundle = prepared_bundle.bundle
        self._prepared_bundle = prepared_bundle

        # Stash for saved bundle resources — each category maps name → config.
        self._stash: dict[str, dict[str, Any]] = {
            "context": {},
            "tools": {},
            "hooks": {},
            "providers": {},
            "agents": {},
        }

        # Snapshot of hook handlers at construction time.
        self._hook_snapshot: dict[str, Any] = {}
        self._capture_hooks()

        # Module-to-tool and reverse mappings built from the mount-plan tool specs.
        self._module_to_tools: dict[str, list[str]] = {}
        self._tool_to_module: dict[str, str] = {}
        self._build_module_to_tools()

        # Hook-handler-to-module mapping.
        self._hook_handler_to_module: dict[str, str] = {}
        self._build_hook_handler_mapping()

        # Track disabled behaviors and per-session config overrides.
        self._disabled_behaviors: set[str] = set()
        self._config_overrides: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Read-only property accessors for BundleInspector
    # ------------------------------------------------------------------

    @property
    def bundle(self) -> Any:
        return self._bundle

    @property
    def coordinator(self) -> Any:
        return self._coordinator

    @property
    def stash(self) -> dict[str, dict[str, Any]]:
        return self._stash

    @property
    def disabled_behaviors(self) -> set[str]:
        return self._disabled_behaviors

    @property
    def module_to_tools(self) -> dict[str, list[str]]:
        return self._module_to_tools

    @property
    def tool_to_module(self) -> dict[str, str]:
        return self._tool_to_module

    @property
    def hook_snapshot(self) -> dict[str, Any]:
        return self._hook_snapshot

    @property
    def hook_handler_to_module(self) -> dict[str, str]:
        return self._hook_handler_to_module

    @property
    def config_overrides(self) -> dict[str, Any]:
        return self._config_overrides

    # ------------------------------------------------------------------
    # Hook management
    # ------------------------------------------------------------------

    def _capture_hooks(self) -> None:
        """Populate the hook snapshot from coordinator-provided metadata."""
        try:
            metadata = self._coordinator.get_capability("hook_metadata")
            if metadata and isinstance(metadata, dict):
                self._hook_snapshot.update(metadata)
                return
        except Exception as exc:  # noqa: BLE001
            _log.debug("hook_metadata capability not available: %s", exc)

        try:
            hook_registry = self._coordinator.hooks
            if hasattr(hook_registry, "list_handlers"):
                for event, names in hook_registry.list_handlers().items():
                    for name in names:
                        if name not in self._hook_snapshot:
                            self._hook_snapshot[name] = {"event": event}
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "Could not capture hook metadata from coordinator: %s. "
                "hook_disable() will work; hook_enable() will not be available.",
                exc,
            )

    def _build_module_to_tools(self) -> None:
        """Build _module_to_tools and _tool_to_module from the mount-plan tool specs."""
        try:
            mounted: dict = self._coordinator.get("tools") or {}
        except Exception:  # noqa: BLE001
            mounted = {}

        specs = self._coordinator.config.get("tools", [])
        claimed: set[str] = set()

        for spec in specs:
            if not isinstance(spec, dict):
                continue
            module_id = spec.get("id") or spec.get("module", "")
            if not module_id:
                continue

            matched_tools: list[str] = []

            prefix = "tool-"
            short = (
                module_id[len(prefix) :] if module_id.startswith(prefix) else module_id
            )
            norm_id = _normalize_module_name(module_id)
            norm_short = _normalize_module_name(short)

            for tool_name in mounted:
                if tool_name in claimed:
                    continue

                norm_tool = _normalize_module_name(tool_name)

                if tool_name == module_id:
                    matched_tools.append(tool_name)
                    claimed.add(tool_name)
                    continue

                if tool_name == short:
                    matched_tools.append(tool_name)
                    claimed.add(tool_name)
                    continue

                if norm_tool == norm_id or norm_tool == norm_short:
                    matched_tools.append(tool_name)
                    claimed.add(tool_name)
                    continue

                if norm_tool.startswith(norm_short + "_"):
                    matched_tools.append(tool_name)
                    claimed.add(tool_name)
                    continue

                tool_instance = mounted.get(tool_name)
                if tool_instance is not None:
                    try:
                        py_mod = type(tool_instance).__module__ or ""
                        norm_mod_id = _normalize_module_name(module_id)
                        if norm_mod_id and norm_mod_id in py_mod:
                            matched_tools.append(tool_name)
                            claimed.add(tool_name)
                            continue
                    except Exception:  # noqa: BLE001
                        pass

            if matched_tools:
                self._module_to_tools[module_id] = matched_tools
                for tool_name in matched_tools:
                    self._tool_to_module[tool_name] = module_id

    def _build_hook_handler_mapping(self) -> None:
        """Build _hook_handler_to_module: handler_name → best-matching module ID."""
        hook_specs = self._coordinator.config.get("hooks", [])
        tool_specs = self._coordinator.config.get("tools", [])

        for handler_name in self._hook_snapshot:
            norm_handler = _normalize_module_name(handler_name)

            best_module: str | None = None
            best_length = 0
            for spec in hook_specs:
                if not isinstance(spec, dict):
                    continue
                module_id = spec.get("id") or spec.get("module", "")
                if not module_id:
                    continue
                norm_module = _normalize_module_name(module_id)
                if norm_handler == norm_module or norm_handler.startswith(
                    norm_module + "_"
                ):
                    if len(norm_module) > best_length:
                        best_module = module_id
                        best_length = len(norm_module)

            if best_module:
                self._hook_handler_to_module[handler_name] = best_module
                continue

            best_tool_module: str | None = None
            best_length = 0
            for spec in tool_specs:
                if not isinstance(spec, dict):
                    continue
                module_id = spec.get("id") or spec.get("module", "")
                if not module_id:
                    continue
                tool_prefix = "tool-"
                short = (
                    module_id[len(tool_prefix) :]
                    if module_id.startswith(tool_prefix)
                    else module_id
                )
                norm_short = _normalize_module_name(short)
                if norm_short and (
                    norm_handler == norm_short
                    or norm_handler.startswith(norm_short + "_")
                ):
                    if len(norm_short) > best_length:
                        best_tool_module = module_id
                        best_length = len(norm_short)

            if best_tool_module:
                self._hook_handler_to_module[handler_name] = best_tool_module

    def hook_disable(self, name: str) -> None:
        """Hook toggle is not supported in this version — logs a warning."""
        _log.warning(
            "Hook toggle is not supported in this version. "
            "Hook '%s' remains active. A core suspend/resume API "
            "is needed for safe hook toggle.",
            name,
        )

    def hook_enable(self, name: str) -> None:
        """Hook toggle is not supported in this version — logs a warning."""
        _log.warning(
            "Hook toggle is not supported in this version. "
            "Hook '%s' state unchanged. A core suspend/resume API "
            "is needed for safe hook toggle.",
            name,
        )

    # ------------------------------------------------------------------
    # Context enable / disable
    # ------------------------------------------------------------------

    def context_disable(self, name: str) -> None:
        """Move a context entry from the bundle into the stash (disable it)."""
        if name in self._stash["context"]:
            return

        if name not in self._bundle.context:
            available = list(self._bundle.context.keys())
            raise ValueError(
                f"Context key {name!r} not found. Available keys: {available}"
            )

        self._stash["context"][name] = self._bundle.context.pop(name)

    def context_enable(self, name: str) -> None:
        """Move a context entry from the stash back into the bundle (enable it)."""
        if name in self._bundle.context:
            return

        if name not in self._stash["context"]:
            raise ValueError(f"Context key {name!r} not in stash. Cannot enable.")

        self._bundle.context[name] = self._stash["context"].pop(name)

    # ------------------------------------------------------------------
    # Agent enable / disable
    # ------------------------------------------------------------------

    def agent_disable(self, name: str) -> None:
        """Move an agent entry from coordinator.config['agents'] into the stash."""
        if name in self._stash["agents"]:
            return

        agents = self._coordinator.config.get("agents", {})
        if name not in agents:
            raise ValueError(f"Agent {name!r} not found.")

        self._stash["agents"][name] = agents.pop(name)

    def agent_enable(self, name: str) -> None:
        """Move an agent entry from the stash back into coordinator.config['agents']."""
        agents = self._coordinator.config.get("agents", {})

        if name in agents:
            return

        if name not in self._stash["agents"]:
            raise ValueError(f"Agent {name!r} not in stash. Cannot enable.")

        agents[name] = self._stash["agents"].pop(name)

    # ------------------------------------------------------------------
    # Tool enable / disable (async)
    # ------------------------------------------------------------------

    async def tool_disable(self, name: str) -> None:
        """Retrieve and stash a tool instance, then unmount it."""
        if name in self._stash["tools"]:
            return

        tools = self._coordinator.get("tools")
        if tools is not None and name in tools:
            instance = tools[name]
            try:
                await self._coordinator.unmount("tools", name=name)
            finally:
                self._stash["tools"][name] = instance
            return

        if name in self._module_to_tools:
            await self.tool_disable_module(name)
            return

        available_tools = list(tools.keys()) if tools else []
        available_modules = list(self._module_to_tools.keys())
        raise ValueError(
            f"Tool or module {name!r} not found. "
            f"Available tools: {available_tools}. "
            f"Available modules: {available_modules}"
        )

    async def tool_enable(self, name: str) -> None:
        """Remount a previously disabled tool from the stash."""
        if name in self._stash["tools"]:
            instance = self._stash["tools"].pop(name)
            try:
                await self._coordinator.mount("tools", instance, name=name)
            except Exception:
                self._stash["tools"][name] = instance  # re-stash on failure
                raise
            return

        if name in self._module_to_tools:
            await self.tool_enable_module(name)
            return

        raise ValueError(f"Tool or module {name!r} not in stash. Cannot enable.")

    async def tool_disable_module(self, module_id: str) -> list[str]:
        """Disable all tools belonging to a module by stashing each one."""
        if module_id not in self._module_to_tools:
            available = list(self._module_to_tools.keys())
            raise ValueError(
                f"Module {module_id!r} not found in module-to-tool mapping. "
                f"Available modules: {available}"
            )

        disabled: list[str] = []
        for tool_name in self._module_to_tools[module_id]:
            try:
                await self.tool_disable(tool_name)
                disabled.append(tool_name)
            except ValueError:
                pass

        return disabled

    async def tool_enable_module(self, module_id: str) -> list[str]:
        """Re-enable all stashed tools belonging to a module."""
        if module_id not in self._module_to_tools:
            available = list(self._module_to_tools.keys())
            raise ValueError(
                f"Module {module_id!r} not found in module-to-tool mapping. "
                f"Available modules: {available}"
            )

        enabled: list[str] = []
        for tool_name in self._module_to_tools[module_id]:
            if tool_name in self._stash["tools"]:
                try:
                    instance = self._stash["tools"].pop(tool_name)
                    await self._coordinator.mount("tools", instance, name=tool_name)
                    enabled.append(tool_name)
                except Exception:  # noqa: BLE001
                    self._stash["tools"].setdefault(tool_name, instance)

        return enabled

    # ------------------------------------------------------------------
    # Provider enable / disable (async)
    # ------------------------------------------------------------------

    async def provider_disable(self, name: str) -> None:
        """Retrieve and stash a provider instance, then unmount it."""
        if name in self._stash["providers"]:
            return

        providers = self._coordinator.get("providers")
        if providers is None or name not in providers:
            available = list(providers.keys()) if providers else []
            raise ValueError(f"Provider {name!r} not found. Available: {available}")
        instance = providers[name]
        try:
            await self._coordinator.unmount("providers", name=name)
        finally:
            self._stash["providers"][name] = instance

    async def provider_enable(self, name: str) -> None:
        """Remount a previously disabled provider from the stash."""
        if name not in self._stash["providers"]:
            raise ValueError(f"Provider {name!r} not in stash. Cannot enable.")

        instance = self._stash["providers"].pop(name)
        try:
            await self._coordinator.mount("providers", instance, name=name)
        except Exception:
            self._stash["providers"][name] = instance  # re-stash on failure
            raise

    # ------------------------------------------------------------------
    # Behavior group toggle (async)
    # ------------------------------------------------------------------

    async def _emit_change_event(
        self, action: str, target: str, changes: list[str] | None = None
    ) -> None:
        """Emit a configuration:changed event after a successful mutation."""
        try:
            await self._coordinator.hooks.emit(
                "configuration:changed",
                {
                    "action": action,
                    "target": target,
                    "changes": changes or [],
                },
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug("Failed to emit configuration:changed event: %s", exc)

    def _resolve_module_id_to_mounted_name(
        self, module_id: str, mount_point: str
    ) -> str | None:
        """Resolve a provenance module ID to its coordinator-mounted name."""
        try:
            mounted: dict = self._coordinator.get(mount_point) or {}
        except Exception:  # noqa: BLE001
            mounted = {}

        if not mounted:
            return None

        if module_id in mounted:
            return module_id

        prefix = mount_point.rstrip("s") + "-"
        short = module_id[len(prefix) :] if module_id.startswith(prefix) else module_id
        if short in mounted:
            return short

        norm_id = _normalize_module_name(module_id)
        short_norm = _normalize_module_name(short)
        for mounted_name in mounted:
            norm_mounted = _normalize_module_name(mounted_name)
            if norm_mounted == norm_id or norm_mounted == short_norm:
                return mounted_name

        return None

    def _get_behavior_root_namespace(self, behavior_name: str) -> str | None:
        """Find the root bundle namespace for a behavior using source_base_paths."""
        sbp = getattr(self._bundle, "source_base_paths", {})
        if not sbp:
            return None
        behavior_path = sbp.get(behavior_name)
        if behavior_path is None:
            return None
        behavior_path_str = str(behavior_path)
        siblings = [ns for ns, p in sbp.items() if str(p) == behavior_path_str]
        candidates = [
            ns
            for ns in siblings
            if ns != behavior_name
            and not ns.startswith("behavior-")
            and not ns.endswith("-behavior")
        ]
        if candidates:
            return min(candidates, key=len)

        if behavior_name in sbp:
            return behavior_name

        return None

    async def behavior_disable(self, name: str) -> dict[str, Any]:
        """Disable all contributions from a named behavior across all categories."""
        provenance: dict[str, list[str]] = getattr(self._bundle, "_provenance", {})
        matching_keys = [k for k, v in provenance.items() if name in v]

        if not matching_keys:
            raise ValueError(f"Behavior {name!r} not found in provenance")

        disabled: list[str] = []
        warnings: list[str] = []

        for prov_key in matching_keys:
            category, item_name = prov_key.split(":", 1)
            if category == "hook":
                _log.debug(
                    "Skipping hook %r in behavior toggle — hook toggle not supported in this version.",
                    item_name,
                )
                continue
            if category == "tool":
                claimants = provenance.get(prov_key, [])
                other_active = [
                    c
                    for c in claimants
                    if c != name and c not in self._disabled_behaviors
                ]
                if other_active:
                    _log.debug(
                        "Skipping tool %r — still claimed by active behavior(s): %s",
                        item_name,
                        other_active,
                    )
                    continue
                tool_names = list(self._module_to_tools.get(item_name, []))
                if not tool_names:
                    mn = self._resolve_module_id_to_mounted_name(item_name, "tools")
                    if mn:
                        tool_names = [mn]
                if tool_names:
                    any_disabled = False
                    for tool_name in tool_names:
                        try:
                            await self.tool_disable(tool_name)
                            any_disabled = True
                        except Exception as exc:  # noqa: BLE001
                            warnings.append(
                                f"Failed to disable tool '{tool_name}': {exc}"
                            )
                    if any_disabled:
                        disabled.append(prov_key)
                else:
                    warnings.append(
                        f"Tool module '{item_name}' not found in mounted tools. "
                        f"It may already be disabled or use a custom registered name."
                    )
                continue
            if category == "provider":
                claimants = provenance.get(prov_key, [])
                other_active = [
                    c
                    for c in claimants
                    if c != name and c not in self._disabled_behaviors
                ]
                if other_active:
                    _log.debug(
                        "Skipping provider %r — still claimed by active behavior(s): %s",
                        item_name,
                        other_active,
                    )
                    continue
                mounted_name = self._resolve_module_id_to_mounted_name(
                    item_name, "providers"
                )
                if mounted_name:
                    try:
                        await self.provider_disable(mounted_name)
                        disabled.append(prov_key)
                    except Exception as exc:  # noqa: BLE001
                        warnings.append(
                            f"Failed to disable provider '{mounted_name}': {exc}"
                        )
                else:
                    warnings.append(
                        f"Provider module '{item_name}' not found in mounted providers. "
                        f"It may already be disabled or use a custom registered name."
                    )
                continue
            claimants = provenance.get(prov_key, [])
            other_active = [
                c for c in claimants if c != name and c not in self._disabled_behaviors
            ]
            if other_active:
                _log.debug(
                    "Skipping %s %r — still claimed by active behavior(s): %s",
                    category,
                    item_name,
                    other_active,
                )
                continue
            try:
                if category == "context":
                    self.context_disable(item_name)
                elif category == "agent":
                    self.agent_disable(item_name)
                disabled.append(prov_key)
            except Exception as exc:  # noqa: BLE001
                warnings.append(str(exc))

        self._disabled_behaviors.add(name)
        await self._emit_change_event("behavior_disable", name, disabled)
        return {"disabled": disabled, "warnings": warnings}

    async def behavior_enable(self, name: str) -> dict[str, Any]:
        """Enable all contributions from a previously disabled named behavior."""
        from amplifier_foundation.configurator._provenance_utils import (
            _normalize_module_name,
        )

        provenance: dict[str, list[str]] = getattr(self._bundle, "_provenance", {})
        matching_keys = [k for k, v in provenance.items() if name in v]

        if not matching_keys:
            raise ValueError(f"Behavior {name!r} not found in provenance")

        enabled: list[str] = []
        warnings: list[str] = []

        for prov_key in matching_keys:
            category, item_name = prov_key.split(":", 1)
            if category == "hook":
                _log.debug(
                    "Skipping hook %r in behavior toggle — hook toggle not supported in this version.",
                    item_name,
                )
                continue
            if category == "tool":
                stash_candidates: list[str] = []
                for tname in self._module_to_tools.get(item_name, []):
                    if tname in self._stash["tools"] and tname not in stash_candidates:
                        stash_candidates.append(tname)
                if not stash_candidates:
                    if item_name in self._stash["tools"]:
                        stash_candidates = [item_name]
                if not stash_candidates:
                    prefix = "tool-"
                    short = (
                        item_name[len(prefix) :]
                        if item_name.startswith(prefix)
                        else item_name
                    )
                    if short in self._stash["tools"]:
                        stash_candidates = [short]
                if not stash_candidates:
                    for tname in list(self._stash["tools"]):
                        if self._tool_to_module.get(tname) == item_name:
                            stash_candidates.append(tname)
                any_enabled = False
                for tname in stash_candidates:
                    try:
                        await self.tool_enable(tname)
                        any_enabled = True
                    except Exception as exc:  # noqa: BLE001
                        warnings.append(f"Failed to re-enable tool '{tname}': {exc}")
                if any_enabled:
                    enabled.append(prov_key)
                continue
            if category == "provider":
                prefix = "provider-"
                short = (
                    item_name[len(prefix) :]
                    if item_name.startswith(prefix)
                    else item_name
                )
                prov_candidates: list[str] = []
                for candidate in [
                    item_name,
                    short,
                    _normalize_module_name(short),
                ]:
                    if (
                        candidate in self._stash["providers"]
                        and candidate not in prov_candidates
                    ):
                        prov_candidates.append(candidate)
                any_enabled = False
                for pname in prov_candidates:
                    try:
                        await self.provider_enable(pname)
                        any_enabled = True
                    except Exception as exc:  # noqa: BLE001
                        warnings.append(
                            f"Failed to re-enable provider '{pname}': {exc}"
                        )
                if any_enabled:
                    enabled.append(prov_key)
                continue
            try:
                if category == "context":
                    self.context_enable(item_name)
                elif category == "agent":
                    self.agent_enable(item_name)
                enabled.append(prov_key)
            except Exception as exc:  # noqa: BLE001
                warnings.append(str(exc))

        self._disabled_behaviors.discard(name)
        await self._emit_change_event("behavior_enable", name, enabled)
        return {"enabled": enabled, "warnings": warnings}

    # ------------------------------------------------------------------
    # Config set
    # ------------------------------------------------------------------

    def config_set(self, path: str, value: Any) -> None:
        """Set a value at a dot-separated path in coordinator.config."""
        keys = path.split(".")
        set_nested(self._coordinator.config, keys, value)
        self._config_overrides[path] = value

    # ------------------------------------------------------------------
    # Persistence: save and apply
    # ------------------------------------------------------------------

    def save(self, scope: str = "global") -> str:
        """Persist the current configurator state to a settings.yaml file."""
        if scope == "global":
            settings_path = Path.home() / ".amplifier" / "settings.yaml"
        elif scope == "project":
            settings_path = Path(".amplifier") / "settings.yaml"
        else:
            raise ValueError(f"Invalid scope: {scope!r}")

        configurator_data: dict[str, Any] = {
            "disabled": {
                "behaviors": sorted(self._disabled_behaviors),
                "context": list(self._stash["context"].keys()),
                "tools": list(self._stash["tools"].keys()),
                "hooks": list(self._stash["hooks"].keys()),
                "providers": list(self._stash["providers"].keys()),
                "agents": list(self._stash["agents"].keys()),
            },
            "config_overrides": dict(self._config_overrides),
        }

        existing: dict[str, Any] = {}
        if settings_path.exists():
            with settings_path.open() as f:
                existing = yaml.safe_load(f) or {}

        existing["configurator"] = configurator_data

        settings_path.parent.mkdir(parents=True, exist_ok=True)

        with settings_path.open("w") as f:
            yaml.dump(existing, f, default_flow_style=False)

        return str(settings_path)

    async def apply_saved_settings(self, settings: dict[str, Any]) -> list[str]:
        """Apply a saved configurator settings dict to the current session."""
        warnings: list[str] = []
        disabled = settings.get("disabled", {})

        for behavior_name in disabled.get("behaviors", []):
            try:
                result = await self.behavior_disable(behavior_name)
                warnings.extend(result.get("warnings", []))
            except Exception as exc:  # noqa: BLE001
                _log.debug("Skipping stale behavior %r: %s", behavior_name, exc)

        for name in disabled.get("context", []):
            try:
                self.context_disable(name)
            except Exception as exc:  # noqa: BLE001
                _log.debug("Skipping stale context item %r: %s", name, exc)

        for name in disabled.get("tools", []):
            try:
                await self.tool_disable(name)
            except Exception as exc:  # noqa: BLE001
                _log.debug("Skipping stale tool %r: %s", name, exc)

        for name in disabled.get("hooks", []):
            _log.debug(
                "Skipping hook %r in apply_saved_settings — hook toggle not supported in this version.",
                name,
            )

        for name in disabled.get("providers", []):
            try:
                await self.provider_disable(name)
            except Exception as exc:  # noqa: BLE001
                _log.debug("Skipping stale provider %r: %s", name, exc)

        for name in disabled.get("agents", []):
            try:
                self.agent_disable(name)
            except Exception as exc:  # noqa: BLE001
                _log.debug("Skipping stale agent %r: %s", name, exc)

        for path, value in settings.get("config_overrides", {}).items():
            self.config_set(path, value)

        await self._emit_change_event("settings_applied", "saved", [])
        return warnings
