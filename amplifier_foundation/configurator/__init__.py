"""SessionConfigurator — per-session bundle configuration manager."""

from __future__ import annotations

from typing import Any


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

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return a snapshot of the current session state.

        Placeholder implementation — returns an empty dict.
        """
        return {}

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
