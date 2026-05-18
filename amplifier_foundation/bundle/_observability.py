"""Observability event injection for prepared bundles.

The Rust kernel owns ``amplifier_core.events.ALL_EVENTS``.  New event names
introduced in the Python layer cannot be added to that list without a
compiled-binary release.  The ``additional_events`` config key on the
``hooks-logging`` and ``hook-context-intelligence`` modules is the
documented escape hatch — they register handlers for every name in that list.

This module provides:
- ``FOUNDATION_OBSERVABILITY_EVENTS``: events emitted by amplifier-core
  that need default subscription on every session.
- ``inject_additional_events()``: idempotent helper used internally by
  ``PreparedBundle.create_session`` and re-exported for app layers that
  want to add their own events (e.g., app-cli's cleanup window events).
"""

from typing import Any, Iterable

# Modules that honor the additional_events config key.
# Soft-coupled by name — these modules implement the convention.
_DEFAULT_SUBSCRIBER_MODULES: frozenset[str] = frozenset(
    {"hooks-logging", "hook-context-intelligence"}
)

# Events emitted by amplifier-core (Python layer) that are not in the
# Rust kernel's ALL_EVENTS list.
FOUNDATION_OBSERVABILITY_EVENTS: tuple[str, ...] = (
    "session:config",  # emitted by _session_exec.emit_raw_field_if_configured (PR #79)
)


def inject_additional_events(
    mount_plan: dict[str, Any],
    events: Iterable[str],
    target_modules: frozenset[str] | None = None,
) -> None:
    """Add event names to ``additional_events`` config of subscriber hooks.

    Idempotent: events already present are not duplicated; user-configured
    entries keep their original position.  Mutates ``mount_plan`` in place.

    Args:
        mount_plan: The session mount plan dict whose hooks section will be
            updated in-place.  A missing or empty ``hooks`` key is a no-op.
        events: Iterable of event name strings to register.  Names already
            present in ``additional_events`` are not duplicated.
        target_modules: Override the default set of subscriber module names.
            Defaults to ``{"hooks-logging", "hook-context-intelligence"}``.

    Example:
        >>> plan = {"hooks": [{"module": "hooks-logging", "config": {}}]}
        >>> inject_additional_events(plan, ["session:config"])
        >>> plan["hooks"][0]["config"]["additional_events"]
        ['session:config']
    """
    targets = target_modules or _DEFAULT_SUBSCRIBER_MODULES
    new_events = list(events)
    for hook in mount_plan.get("hooks") or []:
        if not isinstance(hook, dict) or hook.get("module") not in targets:
            continue
        if hook.get("config") is None:
            hook["config"] = {}
        cfg: dict[str, Any] = hook["config"]
        existing = list(cfg.get("additional_events") or [])
        cfg["additional_events"] = list(dict.fromkeys(existing + new_events))
