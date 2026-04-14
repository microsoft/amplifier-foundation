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
