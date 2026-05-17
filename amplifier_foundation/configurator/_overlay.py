"""RuntimeOverlay — additive overlay of agents, context, and skills under named scopes.

RuntimeOverlay sits as a peer to SessionConfigurator and drives the same live coordinator
surfaces. Where SessionConfigurator toggles session-level items via stash/unstash,
RuntimeOverlay adds *additive* contributions (agents, context, skills) under named scopes
(typically 'mode:<name>') with refcount semantics.

When a ``bundle`` argument is supplied to the constructor, RuntimeOverlay also writes
``Origin(bundle=scope_name, via_behavior=None)`` entries directly into
``bundle.origins`` when items are mounted, and removes them on unmount.  This gives the
inspector the information it needs to set ``runtime_injection="mode"`` on the
corresponding ``ItemRecord``.

  - session baseline contributes +1 per existing item at construction
  - each scope's apply() contributes +1 per declared item
  - mount on 0 → 1; unmount on 1 → 0

Phase 1 covers agents, context, skills. tools/config overrides reserved for v1.1.
Event names are caller-injected (the modes bundle owns canonical names).

Design choice — caller-injected event names:
The constructor takes `success_event` and `failure_event` as required keyword-only
strings rather than importing fixed event constants from a hosting bundle. This
keeps `RuntimeOverlay` standalone — the foundation library has no dependency on
any specific bundle's event names. The modes bundle (and any future consumer)
owns its canonical event vocabulary and passes the names in. Consumers that
prefer not to emit events at all can pass any string they like; the event
emission is fail-safe (exceptions in `_emit` are swallowed and logged at debug
level, not propagated).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CAP_OVERLAY_CONTEXT = "runtime_context_overlay"
_CAP_OVERLAY_SKILLS = "runtime_skill_overlay"

# Public exports: stable constants for downstream consumers (tool-skills, hooks-mode,
# session_spawner). Import from amplifier_foundation top level, not from this module.
RUNTIME_CONTEXT_OVERLAY_CAPABILITY: str = _CAP_OVERLAY_CONTEXT
RUNTIME_SKILL_OVERLAY_CAPABILITY: str = _CAP_OVERLAY_SKILLS

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
        bundle:         Optional Bundle whose ``origins`` dict is updated when items
                        are mounted/unmounted.  When provided, an
                        ``Origin(bundle="mode:<scope>", via_behavior=None)`` entry is
                        written to ``bundle.origins`` on mount and removed on unmount.
                        The inspector then detects these entries and reports
                        ``runtime_injection="mode"`` for the corresponding ItemRecord.
    """

    def __init__(
        self,
        coordinator: Any,
        *,
        success_event: str,
        failure_event: str,
        bundle: Any = None,
    ) -> None:
        self._coordinator = coordinator
        self._success_event = success_event
        self._failure_event = failure_event
        self._bundle = bundle  # Optional Bundle for origins tracking

        # refcounts: (category, key) → int
        self._refcounts: dict[tuple[str, str], int] = {}

        # scope_claims: scope_name → list of (category, key) in application order
        self._scope_claims: dict[str, list[tuple[str, str]]] = {}

        # _owned: per-category dict of items owned by this overlay
        # Kept for _refresh_capability and dump_state compatibility.
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

        ASYMMETRY (intentional, v1):
        - Agents: captured here. If a mode contributes an agent name that already
          exists in the session config, the refcount reaches 2 and _mount() is
          never called — the existing instance is preserved. This implements the
          S1 overlap scenario for agents.
        - Context: NOT captured here. Session-level context flows through the
          bundle's `context: include:` mechanism and the existing `provider:request`
          hook's @-mention resolver. Mode-contributed context is a separate,
          additive layer registered as the `runtime_context_overlay` capability.
          A mode contributing the same context file as the session bundle will
          not deduplicate — both sources will inject independently. Document this
          in the mode authoring guide if it becomes a real issue.
        - Skills: NOT captured here. Same rationale as context.
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

        # Write Origin entries to bundle.origins for all newly mounted items.
        # This allows BundleInspector to detect mode-contributed items.
        if self._bundle is not None and applied_in_this_call:
            self._write_origins_for_scope(scope, applied_in_this_call, add=True)

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

        # Remove Origin entries from bundle.origins for all unmounted items.
        if self._bundle is not None and result.unmounted:
            self._write_origins_for_scope(scope, result.unmounted, add=False)

        event_name = self._success_event if result.success else self._failure_event
        await self._emit(event_name, scope, result)
        return result

    # ------------------------------------------------------------------
    # Debug / introspection helpers
    # ------------------------------------------------------------------

    def get_refcount(self, category: str, key: str) -> int:
        """Return current refcount for (category, key). 0 if not tracked.

        Args:
            category:   Category name, e.g. ``'agents'``.
            key:        Item key within the category.

        Returns:
            Current refcount (>= 0).  Returns 0 for any key that has never
            been applied or has been fully revoked — never raises.
        """
        return self._refcounts.get((category, key), 0)

    def dump_state(self) -> dict[str, Any]:
        """Return a debug snapshot: scope_claims, refcounts (sorted), and owned items per category.

        Suitable for printing during incident debugging. Read-only — does not
        modify any overlay state.

        Returns:
            ``{
                "scope_claims": {scope: list_of_(category, key)_tuples},
                "refcounts":    {(category, key): count, …},  # sorted by (cat, key)
                "owned":        {category: [key, …], …},
            }``
        """
        return {
            "scope_claims": {
                scope: list(claims) for scope, claims in self._scope_claims.items()
            },
            "refcounts": dict(
                sorted(self._refcounts.items(), key=lambda item: item[0])
            ),
            "owned": {
                category: list(items.keys()) for category, items in self._owned.items()
            },
        }

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
    # Origins tracking (bundle.origins integration)
    # ------------------------------------------------------------------

    def _write_origins_for_scope(
        self,
        scope: str,
        claims: list[tuple[str, str]],
        *,
        add: bool,
    ) -> None:
        """Add or remove Origin entries in bundle.origins for a scope.

        Called after successful apply() (add=True) or revoke() (add=False).
        No-op when self._bundle is None or bundle.origins is not a dict.

        Args:
            scope:  The scope identifier (e.g. ``"mode:demo"``).
            claims: List of ``(category, key)`` pairs owned by the scope.
            add:    True to add Origins, False to remove them.
        """
        if self._bundle is None:
            return
        bundle_origins = getattr(self._bundle, "origins", None)
        if not isinstance(bundle_origins, dict):
            return

        from amplifier_foundation.bundle._provenance import _prov_add
        from amplifier_foundation.configurator._types import Origin

        for category, key in claims:
            if category == "agents":
                origins_key = f"agent:{key}"
            elif category == "context":
                origins_key = f"context:{key}"
            else:
                origins_key = f"skill:{key}"

            if add:
                _prov_add(bundle_origins, origins_key, scope, via_behavior=None)
            else:
                # Remove the Origin entry for this scope
                existing = bundle_origins.get(origins_key, [])
                target = Origin(bundle=scope, via_behavior=None)
                updated = [o for o in existing if o != target]
                if updated:
                    bundle_origins[origins_key] = updated
                elif origins_key in bundle_origins:
                    del bundle_origins[origins_key]

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
