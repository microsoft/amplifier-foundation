"""RuntimeOverlay — additive overlay of agents, context, and skills under named scopes.

RuntimeOverlay sits as a peer to SessionConfigurator and drives the same live coordinator
surfaces. Where SessionConfigurator toggles session-level items via stash/unstash,
RuntimeOverlay adds *additive* contributions (agents, context, skills) under named scopes
(typically 'mode:<name>') with refcount semantics:

  - session baseline contributes +1 per existing item at construction
  - each scope's apply() contributes +1 per declared item
  - mount on 0 → 1; unmount on 1 → 0

Phase 1 covers agents, context, skills. tools/config overrides reserved for v1.1.
Event names are caller-injected (the modes bundle owns canonical names).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CAP_OVERLAY_CONTEXT = "mode_overlay_context"
_CAP_OVERLAY_SKILLS = "mode_overlay_skills"

# Categories handled in Phase 1
_SUPPORTED_CATEGORIES = frozenset({"agents", "context", "skills"})
# Categories reserved for future (silently skipped with debug log)
_RESERVED_CATEGORIES = frozenset({"tools", "config"})


# ---------------------------------------------------------------------------
# TransitionResult dataclass
# ---------------------------------------------------------------------------


@dataclass
class TransitionResult:
    """Result of a RuntimeOverlay apply() or revoke() call.

    Attributes:
        success:    True when the transition completed without error.
        scope:      The scope name the transition targeted.
        mounted:    (category, key) pairs mounted during this transition.
        unmounted:  (category, key) pairs unmounted during this transition.
        rolled_back: (category, key) pairs rolled back due to a failed apply.
        error:      Human-readable error string when success is False.
    """

    success: bool
    scope: str
    mounted: list[tuple[str, str]] = field(default_factory=list)
    unmounted: list[tuple[str, str]] = field(default_factory=list)
    rolled_back: list[tuple[str, str]] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# RuntimeOverlay
# ---------------------------------------------------------------------------


class RuntimeOverlay:
    """Additive overlay of agents, context, and skills with refcount semantics.

    Args:
        coordinator:    Live session coordinator (same object SessionConfigurator holds).
        success_event:  Event name emitted when apply/revoke succeeds.
        failure_event:  Event name emitted when apply fails and rolls back.
    """

    def __init__(
        self,
        coordinator: Any,
        *,
        success_event: str,
        failure_event: str,
    ) -> None:
        self._coordinator = coordinator
        self._success_event = success_event
        self._failure_event = failure_event

        # refcounts: (category, key) → int
        self._refcounts: dict[tuple[str, str], int] = {}

        # scope_claims: scope_name → list of (category, key) in application order
        self._scope_claims: dict[str, list[tuple[str, str]]] = {}

        # _owned: per-category dict of items owned by this overlay
        self._owned: dict[str, dict[str, Any]] = {
            "agents": {},
            "context": {},
            "skills": {},
        }

        self._capture_baseline()

    # ------------------------------------------------------------------
    # Baseline capture
    # ------------------------------------------------------------------

    def _capture_baseline(self) -> None:
        """Record refcount=1 for session-baseline agents.

        S1 invariant: if a mode contributes the same agent name as the session
        baseline, the refcount reaches 2 and _mount() is never called (no-op).

        context/skills baseline is empty — session-level context flows through
        bundle.context / tool-skills config, not through overlay capability.
        """
        for agent_name in self._coordinator.config.get("agents") or {}:
            rc_key: tuple[str, str] = ("agents", agent_name)
            self._refcounts[rc_key] = 1

    # ------------------------------------------------------------------
    # Public API: apply / revoke
    # ------------------------------------------------------------------

    async def apply(
        self, scope: str, contributions: dict[str, Any]
    ) -> TransitionResult:
        """Mount all contributions under *scope* with refcount semantics.

        Args:
            scope:          Unique scope identifier, e.g. ``'mode:demo'``.
            contributions:  Dict mapping category → payload.

        Returns:
            A :class:`TransitionResult` indicating success/failure and what
            was mounted (or rolled back).
        """
        if scope in self._scope_claims:
            # Idempotent: already applied — return success no-op
            return TransitionResult(success=True, scope=scope)

        result = TransitionResult(success=True, scope=scope)
        applied_in_this_call: list[tuple[str, str]] = []

        try:
            for category, payload in contributions.items():
                if category in _RESERVED_CATEGORIES:
                    logger.debug(
                        "RuntimeOverlay.apply: skipping reserved category %r "
                        "(scope=%r) — reserved for v1.1",
                        category,
                        scope,
                    )
                    continue

                if category not in _SUPPORTED_CATEGORIES:
                    logger.warning(
                        "RuntimeOverlay.apply: unknown category %r in scope %r — skipping",
                        category,
                        scope,
                    )
                    continue

                # Normalise into (key, value) pairs
                if category == "agents":
                    items = self._normalise_agents(payload)
                else:
                    items = self._normalise_path_list(payload)

                for key, value in items:
                    self._increment(category, key, value)
                    rc_pair: tuple[str, str] = (category, key)
                    applied_in_this_call.append(rc_pair)
                    result.mounted.append(rc_pair)

        except Exception as exc:
            # Atomic rollback in reverse order
            for rc_pair in reversed(applied_in_this_call):
                try:
                    self._decrement(rc_pair[0], rc_pair[1])
                    result.rolled_back.append(rc_pair)
                except Exception as rollback_exc:
                    logger.debug(
                        "RuntimeOverlay.apply rollback failed for %r: %s",
                        rc_pair,
                        rollback_exc,
                    )
            result.mounted.clear()
            result.success = False
            result.error = str(exc)
            await self._emit(self._failure_event, scope, result)
            return result

        self._scope_claims[scope] = list(applied_in_this_call)
        await self._emit(self._success_event, scope, result)
        return result

    async def revoke(self, scope: str) -> TransitionResult:
        """Unmount all contributions for *scope* with refcount semantics.

        Args:
            scope:  The scope name previously passed to :meth:`apply`.

        Returns:
            A :class:`TransitionResult` indicating success/failure and what
            was unmounted.
        """
        if scope not in self._scope_claims:
            # Idempotent: scope was never applied — return success no-op
            result = TransitionResult(success=True, scope=scope)
            await self._emit(self._success_event, scope, result)
            return result

        claims = self._scope_claims.pop(scope)
        result = TransitionResult(success=True, scope=scope)

        for rc_pair in reversed(claims):
            try:
                self._decrement(rc_pair[0], rc_pair[1])
                result.unmounted.append(rc_pair)
            except Exception as exc:
                logger.warning(
                    "RuntimeOverlay.revoke: error unmounting %r in scope %r: %s",
                    rc_pair,
                    scope,
                    exc,
                )
                result.success = False
                result.error = str(exc)

        await self._emit(self._success_event, scope, result)
        return result

    # ------------------------------------------------------------------
    # Refcount helpers
    # ------------------------------------------------------------------

    def _increment(self, category: str, key: str, value: Any) -> None:
        """Increment refcount for (category, key); mount when crossing 0 → 1."""
        rc_key: tuple[str, str] = (category, key)
        before = self._refcounts.get(rc_key, 0)
        self._refcounts[rc_key] = before + 1
        if before == 0:
            self._mount(category, key, value)

    def _decrement(self, category: str, key: str) -> None:
        """Decrement refcount for (category, key); unmount when crossing 1 → 0."""
        rc_key: tuple[str, str] = (category, key)
        before = self._refcounts.get(rc_key, 0)
        if before <= 0:
            raise RuntimeError(
                f"RuntimeOverlay refcount underflow for {rc_key!r}: "
                f"refcount is already {before}"
            )
        new_rc = before - 1
        if new_rc == 0:
            self._unmount(category, key)
            del self._refcounts[rc_key]
        else:
            self._refcounts[rc_key] = new_rc

    # ------------------------------------------------------------------
    # Mount / unmount
    # ------------------------------------------------------------------

    def _mount(self, category: str, key: str, value: Any) -> None:
        """Actually mount one item into the live coordinator surface."""
        if category == "agents":
            agents: dict = self._coordinator.config.setdefault("agents", {})
            agents[key] = value
            self._owned["agents"][key] = value
        elif category in ("context", "skills"):
            self._owned[category][key] = value
            self._refresh_capability(category)
        else:
            raise ValueError(f"RuntimeOverlay._mount: unknown category {category!r}")

    def _unmount(self, category: str, key: str) -> None:
        """Actually unmount one item from the live coordinator surface."""
        if category == "agents":
            self._coordinator.config.get("agents", {}).pop(key, None)
            self._owned["agents"].pop(key, None)
        elif category in ("context", "skills"):
            self._owned[category].pop(key, None)
            self._refresh_capability(category)
        else:
            raise ValueError(f"RuntimeOverlay._unmount: unknown category {category!r}")

    # ------------------------------------------------------------------
    # Capability refresh
    # ------------------------------------------------------------------

    def _refresh_capability(self, category: str) -> None:
        """Register the current owned keys as a capability on the coordinator.

        Args:
            category:   ``'context'`` or ``'skills'``.
        """
        cap_name = (
            _CAP_OVERLAY_CONTEXT if category == "context" else _CAP_OVERLAY_SKILLS
        )
        self._coordinator.register_capability(
            cap_name, list(self._owned[category].keys())
        )

    # ------------------------------------------------------------------
    # Normalisation helpers (static)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_agents(payload: Any) -> list[tuple[str, Any]]:
        """Validate and flatten an agents contribution payload.

        Args:
            payload:    Must be a ``dict[str, dict]``.

        Returns:
            List of ``(name, cfg)`` tuples.

        Raises:
            ValueError: If payload is not a dict or contains non-dict entries.
        """
        if not isinstance(payload, dict):
            raise ValueError(
                f"RuntimeOverlay: 'agents' contribution must be a dict, got {type(payload)!r}"
            )
        result: list[tuple[str, Any]] = []
        for name, cfg in payload.items():
            if not isinstance(cfg, dict):
                raise ValueError(
                    f"RuntimeOverlay: agent config for {name!r} must be a dict, "
                    f"got {type(cfg)!r}"
                )
            result.append((name, cfg))
        return result

    @staticmethod
    def _normalise_path_list(payload: Any) -> list[tuple[str, str]]:
        """Validate and flatten a context/skills path-list contribution payload.

        Args:
            payload:    Must be a ``list[str]``.

        Returns:
            List of ``(entry, entry)`` tuples (key == value == path string).

        Raises:
            ValueError: If payload is not a list or contains non-string entries.
        """
        if not isinstance(payload, list):
            raise ValueError(
                f"RuntimeOverlay: path-list contribution must be a list, "
                f"got {type(payload)!r}"
            )
        result: list[tuple[str, str]] = []
        for entry in payload:
            if not isinstance(entry, str):
                raise ValueError(
                    f"RuntimeOverlay: path-list entry must be a string, got {type(entry)!r}"
                )
            result.append((entry, entry))
        return result

    # ------------------------------------------------------------------
    # Event emission (best-effort)
    # ------------------------------------------------------------------

    async def _emit(
        self, event_name: str, scope: str, result: TransitionResult
    ) -> None:
        """Emit a coordinator hook event; swallows exceptions (best-effort).

        Args:
            event_name: The event identifier to emit.
            scope:      The scope the transition targeted.
            result:     The :class:`TransitionResult` for this transition.
        """
        try:
            await self._coordinator.hooks.emit(
                event_name,
                {
                    "scope": scope,
                    "success": result.success,
                    "mounted": result.mounted,
                    "unmounted": result.unmounted,
                    "rolled_back": result.rolled_back,
                    "error": result.error,
                },
            )
        except Exception as exc:
            logger.debug(
                "RuntimeOverlay._emit: failed to emit event %r for scope %r: %s",
                event_name,
                scope,
                exc,
            )


__all__ = ["RuntimeOverlay", "TransitionResult"]
