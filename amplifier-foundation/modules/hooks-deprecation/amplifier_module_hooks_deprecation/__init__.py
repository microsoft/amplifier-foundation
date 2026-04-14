"""Deprecation hook module for Amplifier bundles.

A reusable hook that any bundle can include to signal it's deprecated.
Fires once per session on session:start, warns both AI and user,
provides migration guidance, and emits a deprecation:warning event.
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
        )


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
    ):
        self.config = config
        self.hooks = hooks
        self.search_dirs = search_dirs or []
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


async def mount(
    coordinator: Any, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Mount the deprecation hook into the coordinator.

    Args:
        coordinator: The Amplifier coordinator instance.
        config: Module configuration dict with deprecation settings.

    Returns:
        Module metadata dict.

    Raises:
        ValueError: If required config keys are missing or invalid.
    """
    parsed = DeprecationConfig.from_dict(config or {})

    # Build search directories for source file scanning
    working_dir_str = coordinator.get_capability("session.working_dir")
    search_dirs: list[Path] = []
    if working_dir_str:
        search_dirs.append(Path(working_dir_str))
    search_dirs.append(Path.cwd())
    search_dirs.append(Path.home())

    hook = DeprecationHook(parsed, coordinator.hooks, search_dirs=search_dirs)

    coordinator.hooks.register(
        "session:start",
        hook.on_session_start,
        priority=10,  # Run early so AI sees the warning from the start
        name="deprecation",
    )

    return {
        "name": "hooks-deprecation",
        "version": "0.1.0",
        "description": f"Deprecation warning for {parsed.bundle_name}",
        "config": {
            "bundle_name": parsed.bundle_name,
            "replacement": parsed.replacement,
            "severity": parsed.severity,
            "sunset_date": parsed.sunset_date.isoformat()
            if parsed.sunset_date
            else None,
        },
    }
