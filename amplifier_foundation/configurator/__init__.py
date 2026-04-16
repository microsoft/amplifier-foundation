"""SessionConfigurator — per-session bundle configuration manager.

This module is a thin facade that creates a :class:`BundleStateManager` (all
mutation logic) and a :class:`BundleInspector` (all query logic), then
delegates every public method to the appropriate sub-object.

All existing imports continue to work unchanged:
    from amplifier_foundation.configurator import SessionConfigurator
    from amplifier_foundation.configurator import _normalize_module_name, ...
"""
from __future__ import annotations

from typing import Any

from amplifier_foundation.configurator._inspector import BundleInspector
from amplifier_foundation.configurator._provenance_utils import (
    _PROV_CATEGORY_MAP as _PROV_CATEGORY_MAP,  # noqa: F401 — re-export
    _build_normalized_prov_lookup as _build_normalized_prov_lookup,  # noqa: F401
    _lookup_prov_behavior as _lookup_prov_behavior,  # noqa: F401
    _normalize_module_name as _normalize_module_name,  # noqa: F401
)
from amplifier_foundation.configurator._state_manager import BundleStateManager


class SessionConfigurator:
    """Facade over BundleStateManager (mutations) and BundleInspector (queries).

    All existing call sites continue to work — the public API is identical to
    the original monolithic class.  Tests that directly access private attributes
    (``_stash``, ``_bundle``, ``_coordinator``, etc.) continue to work via
    read-only properties that proxy to the internal manager/inspector.
    """

    def __init__(self, session: Any, prepared_bundle: Any) -> None:
        self._state = BundleStateManager(session, prepared_bundle)
        self._inspector = BundleInspector(self._state)
        # BundleInspector.__init__ sets _original_snapshot = None.
        # Capture initial state snapshot; take_snapshot() may be patched in tests.
        self.take_snapshot()

    # ------------------------------------------------------------------
    # Backward-compat property accessors
    # (tests and CLI code access these attributes directly)
    # ------------------------------------------------------------------

    @property
    def _session(self) -> Any:
        return self._state._session

    @property
    def _coordinator(self) -> Any:
        return self._state._coordinator

    @property
    def _bundle(self) -> Any:
        return self._state._bundle

    @property
    def _prepared_bundle(self) -> Any:
        return self._state._prepared_bundle

    @property
    def _stash(self) -> dict[str, dict[str, Any]]:
        return self._state._stash

    @property
    def _hook_snapshot(self) -> dict[str, Any]:
        return self._state._hook_snapshot

    @property
    def _disabled_behaviors(self) -> set[str]:
        return self._state._disabled_behaviors

    @property
    def _config_overrides(self) -> dict[str, Any]:
        return self._state._config_overrides

    @property
    def _original_snapshot(self) -> dict[str, Any] | None:
        return self._inspector._original_snapshot

    @_original_snapshot.setter
    def _original_snapshot(self, value: dict[str, Any] | None) -> None:
        self._inspector._original_snapshot = value

    @property
    def _module_to_tools(self) -> dict[str, list[str]]:
        return self._state._module_to_tools

    @property
    def _tool_to_module(self) -> dict[str, str]:
        return self._state._tool_to_module

    # ------------------------------------------------------------------
    # Internal helper exposed for tests
    # ------------------------------------------------------------------

    def _get_behavior_root_namespace(self, behavior_name: str) -> str | None:
        return self._state._get_behavior_root_namespace(behavior_name)

    # ------------------------------------------------------------------
    # Snapshot / diff
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        return self._inspector.snapshot()

    def diff_from_original(self) -> list[dict[str, Any]]:
        return self._inspector.diff_from_original()

    def take_snapshot(self) -> None:
        self._inspector.take_snapshot()

    # ------------------------------------------------------------------
    # Hook toggle
    # ------------------------------------------------------------------

    def hook_disable(self, name: str) -> None:
        return self._state.hook_disable(name)

    def hook_enable(self, name: str) -> None:
        return self._state.hook_enable(name)

    # ------------------------------------------------------------------
    # Context toggle
    # ------------------------------------------------------------------

    def context_disable(self, name: str) -> None:
        return self._state.context_disable(name)

    def context_enable(self, name: str) -> None:
        return self._state.context_enable(name)

    # ------------------------------------------------------------------
    # Agent toggle
    # ------------------------------------------------------------------

    def agent_disable(self, name: str) -> None:
        return self._state.agent_disable(name)

    def agent_enable(self, name: str) -> None:
        return self._state.agent_enable(name)

    # ------------------------------------------------------------------
    # Tool toggle (async)
    # ------------------------------------------------------------------

    async def tool_disable(self, name: str) -> None:
        return await self._state.tool_disable(name)

    async def tool_enable(self, name: str) -> None:
        return await self._state.tool_enable(name)

    async def tool_disable_module(self, module_id: str) -> list[str]:
        return await self._state.tool_disable_module(module_id)

    async def tool_enable_module(self, module_id: str) -> list[str]:
        return await self._state.tool_enable_module(module_id)

    # ------------------------------------------------------------------
    # Provider toggle (async)
    # ------------------------------------------------------------------

    async def provider_disable(self, name: str) -> None:
        return await self._state.provider_disable(name)

    async def provider_enable(self, name: str) -> None:
        return await self._state.provider_enable(name)

    # ------------------------------------------------------------------
    # Behavior group toggle (async)
    # ------------------------------------------------------------------

    async def behavior_disable(self, name: str) -> dict[str, Any]:
        return await self._state.behavior_disable(name)

    async def behavior_enable(self, name: str) -> dict[str, Any]:
        return await self._state.behavior_enable(name)

    # ------------------------------------------------------------------
    # Config get / set
    # ------------------------------------------------------------------

    def config_get(self, path: str) -> Any:
        return self._inspector.config_get(path)

    def config_set(self, path: str, value: Any) -> None:
        return self._state.config_set(path, value)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, scope: str = "global") -> str:
        return self._state.save(scope)

    async def apply_saved_settings(self, settings: dict[str, Any]) -> list[str]:
        return await self._state.apply_saved_settings(settings)

    # ------------------------------------------------------------------
    # List methods — dashboard views
    # ------------------------------------------------------------------

    def context_list(self) -> list[dict]:
        return self._inspector.context_list()

    def tools_list(self) -> list[dict]:
        return self._inspector.tools_list()

    def hooks_list(self) -> list[dict]:
        return self._inspector.hooks_list()

    def providers_list(self) -> list[dict]:
        return self._inspector.providers_list()

    def agents_list(self) -> list[dict]:
        return self._inspector.agents_list()

    def behaviors_list(self) -> list[dict]:
        return self._inspector.behaviors_list()


__all__ = [
    "SessionConfigurator",
    "BundleStateManager",
    "BundleInspector",
    "_PROV_CATEGORY_MAP",
    "_normalize_module_name",
    "_build_normalized_prov_lookup",
    "_lookup_prov_behavior",
]
