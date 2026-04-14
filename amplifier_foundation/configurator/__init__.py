"""SessionConfigurator — per-session bundle configuration manager."""

from __future__ import annotations

from typing import Any

from amplifier_foundation.dicts.navigation import get_nested, set_nested


class SessionConfigurator:
    """Manages per-session bundle configuration: stashing, restoring, and overriding
    context, tools, hooks, providers, and agents from a prepared bundle."""

    def __init__(self, session: Any, prepared_bundle: Any) -> None:
        """Initialise the configurator for a session.

        Args:
            session: The active AmplifierSession (or compatible mock).
            prepared_bundle: The PreparedBundle associated with the session.
        """
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

        # Track disabled behaviors and per-session config overrides.
        self._disabled_behaviors: set[str] = set()
        self._config_overrides: dict[str, Any] = {}

        # Capture original session snapshot.
        self.take_snapshot()

    # ------------------------------------------------------------------
    # Hook management
    # ------------------------------------------------------------------

    def _capture_hooks(self) -> None:
        """Copy current hook handler entries from coordinator.hooks._handlers."""
        handlers: dict[str, Any] = self._coordinator.hooks._handlers
        for name, entry in handlers.items():
            self._hook_snapshot[name] = dict(entry)

    def hook_disable(self, name: str) -> None:
        """Unregister a hook from the coordinator and stash a marker (disable it).

        Args:
            name: The hook name to disable.

        Raises:
            ValueError: If the name is not found in the hook snapshot.
        """
        # Idempotent: already disabled (in stash) — nothing to do.
        if name in self._stash["hooks"]:
            return

        if name not in self._hook_snapshot:
            raise ValueError(f"Hook {name!r} not found in snapshot.")

        self._coordinator.hooks.unregister(name)
        self._stash["hooks"][name] = True

    def hook_enable(self, name: str) -> None:
        """Re-register a previously disabled hook from the snapshot (enable it).

        Args:
            name: The hook name to enable.

        Raises:
            ValueError: If the name is not in the stash (with a 'not in stash' message).
        """
        if name not in self._stash["hooks"]:
            raise ValueError(f"Hook {name!r} not in stash. Cannot enable.")

        info = self._hook_snapshot[name]
        self._coordinator.hooks.register(
            info["event"],
            info["handler"],
            priority=info.get("priority", 50),
            name=name,
        )
        del self._stash["hooks"][name]

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return a snapshot of the current session state.

        Returns a dict with keys: 'context', 'tools', 'hooks', 'providers', 'agents'.
        Each value is {'enabled': [...], 'disabled': [...]}.
        """
        stash = self._stash

        # Context: enabled = live keys in bundle.context, disabled = keys in stash
        context_enabled = list(self._bundle.context.keys())
        context_disabled = list(stash["context"].keys())

        # Agents: enabled = live keys in coordinator.config['agents'], disabled = stash keys
        agents_enabled = list(self._coordinator.config.get("agents", {}).keys())
        agents_disabled = list(stash["agents"].keys())

        # Tools: enabled = module IDs from bundle.tools not in stash, disabled = stash keys
        tools_enabled = [
            mid
            for mod in self._bundle.tools
            if (mid := mod.get("id") or mod.get("module")) and mid not in stash["tools"]
        ]
        tools_disabled = list(stash["tools"].keys())

        # Providers: enabled = module IDs from bundle.providers not in stash, disabled = stash keys
        providers_enabled = [
            mid
            for mod in self._bundle.providers
            if (mid := mod.get("id") or mod.get("module"))
            and mid not in stash["providers"]
        ]
        providers_disabled = list(stash["providers"].keys())

        # Hooks: enabled = names from hook_snapshot not in stash, disabled = stash keys
        hooks_enabled = [
            name for name in self._hook_snapshot if name not in stash["hooks"]
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
        """Return a list of changes compared to the original snapshot.

        Returns list of {'category': str, 'name': str, 'action': str} dicts.
        action is 'disabled' (was enabled, now disabled) or 'enabled' (newly enabled).
        Returns empty list if no original snapshot has been taken.
        """
        if self._original_snapshot is None:
            return []

        current = self.snapshot()
        changes: list[dict[str, Any]] = []

        for category in ("context", "tools", "hooks", "providers", "agents"):
            orig_enabled = set(
                self._original_snapshot.get(category, {}).get("enabled", [])
            )
            curr_enabled = set(current.get(category, {}).get("enabled", []))

            # Items present in original but gone now → disabled
            for name in orig_enabled - curr_enabled:
                changes.append(
                    {"category": category, "name": name, "action": "disabled"}
                )

            # Items present now but not in original → enabled
            for name in curr_enabled - orig_enabled:
                changes.append(
                    {"category": category, "name": name, "action": "enabled"}
                )

        return changes

    def take_snapshot(self) -> None:
        """Capture a snapshot and store it as the original reference."""
        self._original_snapshot = self.snapshot()

    # ------------------------------------------------------------------
    # Context enable / disable
    # ------------------------------------------------------------------

    def context_disable(self, name: str) -> None:
        """Move a context entry from the bundle into the stash (disable it).

        Args:
            name: The context key to disable.

        Raises:
            ValueError: If the name is not found in bundle.context and is not
                already stashed (with a message listing available keys).
        """
        # Idempotent: already disabled (in stash) — nothing to do.
        if name in self._stash["context"]:
            return

        if name not in self._bundle.context:
            available = list(self._bundle.context.keys())
            raise ValueError(
                f"Context key {name!r} not found. Available keys: {available}"
            )

        self._stash["context"][name] = self._bundle.context.pop(name)

    def context_enable(self, name: str) -> None:
        """Move a context entry from the stash back into the bundle (enable it).

        Args:
            name: The context key to enable.

        Raises:
            ValueError: If the name is not in the stash (with a 'not in stash'
                message).
        """
        # Idempotent: already enabled (in bundle.context) — nothing to do.
        if name in self._bundle.context:
            return

        if name not in self._stash["context"]:
            raise ValueError(f"Context key {name!r} not in stash. Cannot enable.")

        self._bundle.context[name] = self._stash["context"].pop(name)

    # ------------------------------------------------------------------
    # Agent enable / disable
    # ------------------------------------------------------------------

    def agent_disable(self, name: str) -> None:
        """Move an agent entry from coordinator.config['agents'] into the stash (disable it).

        Args:
            name: The agent name to disable.

        Raises:
            ValueError: If the name is not found in coordinator.config['agents'] (with a
                message containing 'not found').
        """
        # Idempotent: already disabled (in stash) — nothing to do.
        if name in self._stash["agents"]:
            return

        agents = self._coordinator.config.get("agents", {})
        if name not in agents:
            raise ValueError(f"Agent {name!r} not found.")

        self._stash["agents"][name] = agents.pop(name)

    def agent_enable(self, name: str) -> None:
        """Move an agent entry from the stash back into coordinator.config['agents'] (enable it).

        Args:
            name: The agent name to enable.

        Raises:
            ValueError: If the name is not in the stash (with a 'not in stash' message).
        """
        agents = self._coordinator.config.get("agents", {})

        # Idempotent: already enabled (in agents dict) — nothing to do.
        if name in agents:
            return

        if name not in self._stash["agents"]:
            raise ValueError(f"Agent {name!r} not in stash. Cannot enable.")

        agents[name] = self._stash["agents"].pop(name)

    # ------------------------------------------------------------------
    # Tool enable / disable (async)
    # ------------------------------------------------------------------

    async def tool_disable(self, name: str) -> None:
        """Unmount a tool from the coordinator and stash the instance (disable it).

        Args:
            name: The tool name to disable.
        """
        # Idempotent: already disabled (in stash) — nothing to do.
        if name in self._stash["tools"]:
            return

        instance = await self._coordinator.unmount("tools", name)
        self._stash["tools"][name] = instance

    async def tool_enable(self, name: str) -> None:
        """Remount a previously disabled tool from the stash (enable it).

        Args:
            name: The tool name to enable.

        Raises:
            ValueError: If the name is not in the stash (with a 'not in stash' message).
        """
        if name not in self._stash["tools"]:
            raise ValueError(f"Tool {name!r} not in stash. Cannot enable.")

        instance = self._stash["tools"].pop(name)
        await self._coordinator.mount("tools", name, instance)

    # ------------------------------------------------------------------
    # Provider enable / disable (async)
    # ------------------------------------------------------------------

    async def provider_disable(self, name: str) -> None:
        """Unmount a provider from the coordinator and stash the instance (disable it).

        Args:
            name: The provider name to disable.
        """
        # Idempotent: already disabled (in stash) — nothing to do.
        if name in self._stash["providers"]:
            return

        instance = await self._coordinator.unmount("providers", name)
        self._stash["providers"][name] = instance

    async def provider_enable(self, name: str) -> None:
        """Remount a previously disabled provider from the stash (enable it).

        Args:
            name: The provider name to enable.

        Raises:
            ValueError: If the name is not in the stash (with a 'not in stash' message).
        """
        if name not in self._stash["providers"]:
            raise ValueError(f"Provider {name!r} not in stash. Cannot enable.")

        instance = self._stash["providers"].pop(name)
        await self._coordinator.mount("providers", name, instance)

    # ------------------------------------------------------------------
    # Behavior group toggle (async)
    # ------------------------------------------------------------------

    async def behavior_disable(self, name: str) -> dict[str, Any]:
        """Disable all contributions from a named behavior across all categories.

        Reads bundle._provenance to find all items contributed by the behavior,
        then disables each one by calling the appropriate category disable method.
        Partial failures are collected as warnings and do not stop processing.

        Args:
            name: The behavior name to disable.

        Returns:
            dict with keys:
                'disabled': list of provenance keys that were successfully disabled.
                'warnings': list of error message strings for items that failed.

        Raises:
            ValueError: If the behavior name is not found in provenance.
        """
        provenance: dict[str, str] = getattr(self._bundle, "_provenance", {})
        matching_keys = [k for k, v in provenance.items() if v == name]

        if not matching_keys:
            raise ValueError(f"Behavior {name!r} not found in provenance")

        disabled: list[str] = []
        warnings: list[str] = []

        for prov_key in matching_keys:
            category, item_name = prov_key.split(":", 1)
            try:
                if category == "context":
                    self.context_disable(item_name)
                elif category == "tools":
                    await self.tool_disable(item_name)
                elif category == "hooks":
                    self.hook_disable(item_name)
                elif category == "providers":
                    await self.provider_disable(item_name)
                elif category == "agents":
                    self.agent_disable(item_name)
                disabled.append(prov_key)
            except Exception as exc:  # noqa: BLE001
                warnings.append(str(exc))

        self._disabled_behaviors.add(name)
        return {"disabled": disabled, "warnings": warnings}

    async def behavior_enable(self, name: str) -> dict[str, Any]:
        """Enable all contributions from a previously disabled named behavior.

        Reads bundle._provenance to find all items contributed by the behavior,
        then enables each one by calling the appropriate category enable method.
        Partial failures are collected as warnings and do not stop processing.

        Args:
            name: The behavior name to enable.

        Returns:
            dict with keys:
                'enabled': list of provenance keys that were successfully enabled.
                'warnings': list of error message strings for items that failed.

        Raises:
            ValueError: If the behavior name is not found in provenance.
        """
        provenance: dict[str, str] = getattr(self._bundle, "_provenance", {})
        matching_keys = [k for k, v in provenance.items() if v == name]

        if not matching_keys:
            raise ValueError(f"Behavior {name!r} not found in provenance")

        enabled: list[str] = []
        warnings: list[str] = []

        for prov_key in matching_keys:
            category, item_name = prov_key.split(":", 1)
            try:
                if category == "context":
                    self.context_enable(item_name)
                elif category == "tools":
                    await self.tool_enable(item_name)
                elif category == "hooks":
                    self.hook_enable(item_name)
                elif category == "providers":
                    await self.provider_enable(item_name)
                elif category == "agents":
                    self.agent_enable(item_name)
                enabled.append(prov_key)
            except Exception as exc:  # noqa: BLE001
                warnings.append(str(exc))

        self._disabled_behaviors.discard(name)
        return {"enabled": enabled, "warnings": warnings}

    # ------------------------------------------------------------------
    # Config get / set
    # ------------------------------------------------------------------

    def config_get(self, path: str) -> Any:
        """Return the value at a dot-separated path in coordinator.config.

        Args:
            path: Dot-separated key path (e.g. 'model.name').

        Returns:
            The value at the path, or None if not found.
        """
        keys = path.split(".")
        return get_nested(self._coordinator.config, keys)

    def config_set(self, path: str, value: Any) -> None:
        """Set a value at a dot-separated path in coordinator.config.

        Mutates coordinator.config in-place and records the override for
        later persistence via save().

        Args:
            path: Dot-separated key path (e.g. 'model.name').
            value: Value to set at the path.
        """
        keys = path.split(".")
        set_nested(self._coordinator.config, keys, value)
        self._config_overrides[path] = value
