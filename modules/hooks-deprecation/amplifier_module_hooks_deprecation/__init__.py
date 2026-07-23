"""Deprecation hook module for Amplifier bundles.

A reusable hook that any bundle can include to signal it's deprecated.
Fires once per session on session:start, warns both AI and user,
provides migration guidance, and emits a deprecation:warning event.

Supports N tombstones per mount (see ``parse_deprecation_configs``): a
legacy flat config (single tombstone, unchanged), a ``deprecations:`` list
(N tombstones), or both at once. Both-at-once is what a composed bundle
sees after foundation's deep_merge unions two behaviors that each mount
this module with a different tombstone -- deep_merge replaces the scalar
``bundle_name`` (child-wins, collapsing to one) but concatenates the
``deprecations`` list, so moving per-tombstone data under a list key is
what lets multiple tombstones survive the merge.

Firing decisions are made in two phases:

- ``mount()`` only validates config (fails fast on a malformed tombstone).
- ``on_session_ready()`` -- the kernel's post-composition lifecycle
  callback, run once every module has finished ``mount()`` -- is where the
  session:start handler actually gets registered. At that point
  ``coordinator.config`` reflects the ACTUAL composed module list, so a
  tombstone whose deprecated bundle is genuinely present in this session
  fires reliably, even with ``require_evidence=True`` and no filesystem
  evidence (best-effort ``.amplifier/`` scanning remains the fallback
  signal for anything not directly observable in the composed plan).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from amplifier_core import HookResult

logger = logging.getLogger(__name__)


@dataclass
class DeprecationConfig:
    """Parsed and validated deprecation configuration."""

    bundle_name: str
    message: str
    replacement: str | None = None
    migration: str | None = None
    severity: str = "warning"  # "warning" or "info"
    sunset_date: date | None = None
    require_evidence: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DeprecationConfig:
        """Parse and validate config from a raw dict.

        Required keys: bundle_name, message.
        Optional keys: replacement, migration, severity, sunset_date.

        Raises:
            ValueError: If required keys are missing or values are invalid.
        """
        bundle_name = raw.get("bundle_name")
        if not bundle_name:
            raise ValueError("bundle_name is required in deprecation hook config")

        message = raw.get("message")
        if not message:
            raise ValueError("message is required in deprecation hook config")

        severity = raw.get("severity", "warning")
        if severity not in ("warning", "info"):
            raise ValueError(f"severity must be 'warning' or 'info', got '{severity}'")

        sunset_date = None
        raw_date = raw.get("sunset_date")
        if raw_date:
            try:
                sunset_date = date.fromisoformat(str(raw_date))
            except ValueError:
                raise ValueError(
                    f"sunset_date must be YYYY-MM-DD format, got '{raw_date}'"
                )

        return cls(
            bundle_name=bundle_name,
            message=message,
            replacement=raw.get("replacement"),
            migration=raw.get("migration"),
            severity=severity,
            sunset_date=sunset_date,
            require_evidence=bool(raw.get("require_evidence", False)),
        )


def parse_deprecation_configs(raw: dict[str, Any]) -> list[DeprecationConfig]:
    """Parse one or more deprecation tombstones from a raw module config dict.

    Supports three shapes:
      - Legacy flat (unchanged): {bundle_name, message, ...} -> one tombstone.
      - List form: {deprecations: [{bundle_name, message, ...}, ...]} -> N tombstones.
      - Both at once: the flat fields form one tombstone AND each entry in
        `deprecations` forms another. This is the shape a composed bundle sees
        after foundation's deep_merge unions two behaviors that each mount this
        module with a different tombstone (deep_merge replaces the scalar
        `bundle_name` -- collapsing to one -- but concatenates the `deprecations`
        list, so this is what lets both tombstones survive the merge).

    `DeprecationConfig.from_dict` already ignores unknown keys (like
    `deprecations`), so passing `raw` straight through for the flat branch is safe.

    Raises:
        ValueError: If neither a top-level `bundle_name` nor a non-empty
            `deprecations` list is present.
    """
    tombstones: list[DeprecationConfig] = []

    if raw.get("bundle_name"):
        tombstones.append(DeprecationConfig.from_dict(raw))

    for entry in raw.get("deprecations") or []:
        tombstones.append(DeprecationConfig.from_dict(entry))

    if not tombstones:
        raise ValueError(
            "hooks-deprecation requires a top-level 'bundle_name' or a non-empty 'deprecations' list"
        )

    return tombstones


def find_source_files(bundle_name: str, search_dirs: list[Path]) -> list[str]:
    """Scan .amplifier/ directories for files referencing the deprecated bundle.

    Best-effort: silently skips unreadable files and missing directories.
    Searches for any YAML file under .amplifier/ that contains the bundle_name string.

    Args:
        bundle_name: Name of the deprecated bundle to search for.
        search_dirs: Base directories to search (e.g., [cwd, home]).

    Returns:
        List of absolute file paths containing references to the bundle.
    """
    found: list[str] = []

    for base_dir in search_dirs:
        amp_dir = base_dir / ".amplifier"
        if not amp_dir.is_dir():
            continue

        for yaml_file in amp_dir.rglob("*.yaml"):
            # Skip resolved/cached artifacts (e.g. .amplifier/cache/...) — the
            # tombstone's own carrier config is cached on every install and would
            # otherwise always self-match, defeating require_evidence gating. (#344)
            # Scope the check to the path *below* .amplifier so a "cache" segment
            # in an ancestor of base_dir (e.g. a user project under some cache/
            # dir) doesn't suppress genuine authored config.
            if "cache" in yaml_file.relative_to(amp_dir).parts:
                continue
            try:
                content = yaml_file.read_text(encoding="utf-8")
                if bundle_name in content:
                    found.append(str(yaml_file))
            except (OSError, UnicodeDecodeError):
                continue

    return found


def effective_severity(config: DeprecationConfig) -> str:
    """Compute effective severity, escalating if sunset_date is past.

    Escalation rules:
      - info  + past sunset → warning
      - warning + past sunset → urgent (URGENT prefix in messages)
      - No sunset or future sunset → configured severity unchanged.
      - Sunset date equal to today is NOT considered past.
    """
    if config.sunset_date and config.sunset_date < date.today():
        if config.severity == "info":
            return "warning"
        if config.severity == "warning":
            return "urgent"
    return config.severity


def build_warning_text(
    config: DeprecationConfig,
    severity: str,
    source_files: list[str],
) -> str:
    """Build the AI context injection text block.

    This is the text the AI sees in its conversation context.
    It should be clear, actionable, and include all migration details.
    """
    if severity == "urgent":
        header = f"URGENT DEPRECATION WARNING: {config.bundle_name}"
    else:
        header = f"DEPRECATION WARNING: {config.bundle_name}"

    lines = [header, "", config.message]

    if config.replacement:
        lines.append(f"Replacement: {config.replacement}")

    if config.sunset_date:
        lines.append(f"Sunset date: {config.sunset_date.isoformat()}")

    if source_files:
        lines.append("")
        lines.append("Found in:")
        for path in source_files:
            lines.append(f"  - {path}")

    if config.migration:
        lines.append("")
        lines.append("Migration steps:")
        lines.append(config.migration)

    return "\n".join(lines)


def build_user_message(config: DeprecationConfig, severity: str) -> str:
    """Build the user-visible warning message.

    Shorter than the AI context — just enough to alert the user.
    """
    prefix = "URGENT: " if severity == "urgent" else ""
    msg = f"{prefix}Deprecated bundle '{config.bundle_name}': {config.message}"
    if config.replacement:
        msg += f" → Use '{config.replacement}' instead."
    return msg


class DeprecationHook:
    """Hook handler that fires a deprecation warning once per session."""

    def __init__(
        self,
        config: DeprecationConfig,
        hooks: Any,
        search_dirs: list[Path] | None = None,
        composed: bool = False,
    ):
        self.config = config
        self.hooks = hooks
        self.search_dirs = search_dirs or []
        # True when the kernel's fully-composed mount plan (read at
        # on_session_ready time) confirms this tombstone's bundle_name is
        # actually part of this session. Defaults to False so direct
        # construction (all pre-existing tests) is byte-identical to
        # today's behavior.
        self.composed = composed
        self._fired = False

    async def on_session_start(self, event: str, data: dict[str, Any]) -> HookResult:
        """Handle session:start event. Fires once per session.

        Returns:
            HookResult with action="inject_context" on first call,
            action="continue" on subsequent calls.
        """
        if self._fired:
            return HookResult(action="continue")
        self._fired = True

        # Compute severity (may escalate if sunset is past)
        severity = effective_severity(self.config)

        # Scan for source files referencing this deprecated bundle
        source_files = find_source_files(self.config.bundle_name, self.search_dirs)

        # Opt-in: when evidence is required and none was found, stay silent --
        # UNLESS this bundle is confirmed present in the ACTUAL composed
        # module list (self.composed), which is a stronger, reliable signal
        # that should never be overridden by the absence of best-effort
        # filesystem evidence.
        if self.config.require_evidence and not source_files and not self.composed:
            return HookResult(action="continue")

        # Build the AI context block and user message
        context_text = build_warning_text(self.config, severity, source_files)
        user_msg = build_user_message(self.config, severity)

        # Map severity to HookResult user_message_level
        # HookResult only supports "info", "warning", "error"
        # "urgent" is our internal concept — map to "warning"
        if severity in ("warning", "urgent"):
            msg_level = "warning"
        else:
            msg_level = "info"

        # Emit deprecation event for other hooks to observe
        await self.hooks.emit(
            "deprecation:warning",
            {
                "bundle_name": self.config.bundle_name,
                "replacement": self.config.replacement,
                "severity": severity,
                "source_files": source_files,
            },
        )

        return HookResult(
            action="inject_context",
            context_injection=context_text,
            context_injection_role="system",
            user_message=user_msg,
            user_message_level=msg_level,
            user_message_source="deprecation",
        )


_SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2}


def combine_hook_results(results: list[HookResult]) -> HookResult:
    """Combine the per-tombstone HookResults from one session:start dispatch.

    Pass-through for the single-tombstone case (len(results) == 1): returns
    that result unchanged, byte-for-byte, so the legacy single-config path is
    observably identical to today's behavior -- including when the one result
    is action="continue" (already fired, or require_evidence gated it silent).

    For multiple tombstones, results with action="continue" (already fired or
    evidence-gated silent) are dropped; the rest are concatenated into a
    single inject_context result. If none remain active, returns "continue".
    """
    if len(results) == 1:
        return results[0]

    active = [r for r in results if r.action != "continue"]
    if not active:
        return HookResult(action="continue")

    context_blocks = [r.context_injection for r in active if r.context_injection]
    user_messages = [r.user_message for r in active if r.user_message]
    levels = [r.user_message_level for r in active if r.user_message_level]
    level = max(levels, key=lambda lvl: _SEVERITY_RANK.get(lvl, 0)) if levels else None

    return HookResult(
        action="inject_context",
        context_injection="\n\n".join(context_blocks) if context_blocks else None,
        context_injection_role="system",
        user_message="\n".join(user_messages) if user_messages else None,
        user_message_level=level,
        user_message_source="deprecation",
    )


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Validate deprecation hook config at mount time.

    `mount()` runs mid-composition (kernel Phase 2-5): earlier-phase modules
    are wired, but the full module list for this session isn't settled yet,
    so "is bundle X actually part of this session?" can't be answered
    reliably here. All this does now is validate `config` up front -- so a
    misconfigured tombstone still fails fast with the same ValueError as
    before -- and return. No hook is registered here.

    Registering the actual `session:start` handler is deferred to
    `on_session_ready()`, which the kernel calls once every module across
    every phase has finished `mount()`. At that point `coordinator.config`
    reflects the ACTUAL composed module list, which is what lets the
    firing decision be based on ground truth instead of best-effort
    filesystem scanning. See `on_session_ready` for the full mechanism.

    Args:
        coordinator: The Amplifier coordinator instance.
        config: Module configuration dict with deprecation settings. Either
            a legacy flat tombstone, a `deprecations:` list of tombstones,
            or both (see `parse_deprecation_configs`).

    Returns:
        None. No cleanup is needed for this module.

    Raises:
        ValueError: If required config keys are missing or invalid.
    """
    parse_deprecation_configs(config or {})

    # Declare the event we emit so the session capture hooks
    # (hooks-logging + hook-context-intelligence) auto-discover and record it via
    # the observability.events contribution channel. Module-owned declaration --
    # see core:docs/specs/CONTRIBUTION_CHANNELS.md; template: tool-delegate.
    coordinator.register_contributor(
        "observability.events",
        "hooks-deprecation",
        lambda: ["deprecation:warning"],
    )

    return None


async def on_session_ready(coordinator: Any) -> None:
    """Register the deprecation session:start handler from the composed plan.

    This is where firing is actually decided and wired up -- `mount()` only
    validates config. `on_session_ready()` runs once every
    orchestrator/context/provider/tool/hook module has finished `mount()`
    (kernel Phase 6), strictly before `session:start` can fire, so
    `coordinator.config` here reflects the ACTUAL composed module list for
    this session rather than a best-effort filesystem guess made
    mid-composition.

    Steps:
      1. Recover this module's own merged tombstone config: scan
         `coordinator.config["hooks"]` for the entry whose
         `module == "hooks-deprecation"` (after foundation's compose-merge
         there is exactly one, with the unioned config) and take its
         `config`. If no such entry exists, this module wasn't actually
         mounted for this session -- return without registering anything.
      2. Parse that config into N tombstones via `parse_deprecation_configs`
         (unchanged helper -- handles flat / list / both shapes).
      3. Compute the declared composed module-id set: the union of
         `module` across `coordinator.config["hooks"]`, `["tools"]`, and
         `["providers"]`. This is the reliable "is bundle X actually part
         of this session?" signal `mount()` couldn't produce mid-composition.
      4. Build one `DeprecationHook` per tombstone, passing
         `composed=(tombstone.bundle_name in declared_ids)`.
      5. Register ONE combined `session:start` handler, named
         "deprecation", using the same `combine_hook_results` merge the
         old `mount()`-time registration used.

    Net effect (additive-only -- see `DeprecationHook.on_session_start`): a
    tombstone fires if `require_evidence=False` (legacy, unchanged) OR its
    bundle is composed (new, reliable signal) OR (`require_evidence=True`
    and filesystem evidence was found, unchanged). Nothing that fired under
    the old mount()-time-only gate stops firing.

    Exceptions are caught and logged rather than propagated. The kernel
    already isolates `on_session_ready` failures per-module (emitting
    `module:on_session_ready_failed`), but a deprecation notice is
    inherently best-effort and must never be the reason session
    initialization fails.
    """
    try:
        own_config: dict[str, Any] | None = None
        for entry in coordinator.config.get("hooks", []) or []:
            if entry.get("module") == "hooks-deprecation":
                own_config = entry.get("config") or {}
                break

        if own_config is None:
            return

        configs = parse_deprecation_configs(own_config)

        # Self-deprecate the legacy flat form under composition. When the merged
        # config carries BOTH a scalar `bundle_name` AND a `deprecations:` list,
        # we are provably in a multi-tombstone composition where at least one
        # author is still on the flat form. That scalar is at risk: foundation's
        # deep-merge keeps only ONE `bundle_name` (child-wins), so a second flat
        # mount would silently clobber it. The hook can't see the tombstone that
        # was already dropped (the merge discards it before we run) -- so we warn
        # on the CAUSE (flat form present alongside a list), not the symptom. A
        # lone flat consumer (no list) is safe and stays silent -- no nagging.
        if own_config.get("bundle_name") and (own_config.get("deprecations") or []):
            logger.warning(
                "hooks-deprecation: a flat scalar 'bundle_name' tombstone (%r) is "
                "composed alongside a 'deprecations:' list. Foundation's deep-merge "
                "keeps only one scalar 'bundle_name' (child-wins), so a flat "
                "tombstone can be silently clobbered when another behavior also "
                "mounts this hook flat. Move it under the 'deprecations:' list so "
                "every tombstone unions.",
                own_config.get("bundle_name"),
            )

        declared_ids: set[str] = set()
        for section in ("hooks", "tools", "providers"):
            for entry in coordinator.config.get(section, []) or []:
                module_id = entry.get("module")
                if module_id:
                    declared_ids.add(module_id)

        working_dir_str = coordinator.get_capability("session.working_dir")
        search_dirs: list[Path] = []
        if working_dir_str:
            search_dirs.append(Path(working_dir_str))
        search_dirs.append(Path.cwd())
        search_dirs.append(Path.home())

        tombstone_hooks = [
            DeprecationHook(
                cfg,
                coordinator.hooks,
                search_dirs=search_dirs,
                composed=cfg.bundle_name in declared_ids,
            )
            for cfg in configs
        ]

        hooks_registry = coordinator.hooks

        # Defensive guard: the kernel invokes on_session_ready() exactly
        # once per session, but avoid double-registering the handler if
        # this is ever invoked twice for the same coordinator (e.g. direct
        # re-invocation in a test or a future kernel change).
        if hasattr(hooks_registry, "list_handlers"):
            try:
                existing = hooks_registry.list_handlers("session:start") or {}
                if "deprecation" in existing.get("session:start", []):
                    return
            except Exception:
                logger.debug(
                    "hooks-deprecation: list_handlers guard check failed; "
                    "proceeding with registration",
                    exc_info=True,
                )

        async def _on_session_start(event: str, data: dict[str, Any]) -> HookResult:
            results = [await h.on_session_start(event, data) for h in tombstone_hooks]
            return combine_hook_results(results)

        hooks_registry.register(
            "session:start",
            _on_session_start,
            priority=10,  # Run early so AI sees the warning from the start
            name="deprecation",
        )
    except Exception:
        logger.exception(
            "hooks-deprecation: on_session_ready failed to register the "
            "session:start handler"
        )
