"""Process guard hooks module for Amplifier.

Monitors and limits subprocess creation to prevent runaway processes
and resource exhaustion during tool execution.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from amplifier_core import HookResult


@dataclass
class ProcessGuardConfig:
    """Configuration for process guard behavior."""

    max_process_count: int = 128
    warning_threshold: int = 64
    kill_orphans_before_exec: bool = True
    kill_patterns: list[str] = field(default_factory=lambda: ["pytest", "node.*test"])
    repeat_detection_window_seconds: int = 60
    repeat_detection_max_count: int = 5
    enabled: bool = True


class ProcessGuardHooks:
    """Hook handler for process guard enforcement.

    Monitors process count before bash tool execution and enforces limits
    to prevent runaway processes and resource exhaustion.
    """

    def __init__(self, config: ProcessGuardConfig) -> None:
        self.config = config
        self._command_history: dict[str, Any] = {}

    def _get_process_count(self) -> int:
        """Run 'ps aux' and count lines minus the header line.

        Returns:
            Number of running processes, or 0 on failure.
        """
        try:
            result = subprocess.run(
                ["ps", "aux"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            lines = result.stdout.strip().split("\n")
            # Subtract 1 for the header line
            return max(0, len(lines) - 1)
        except Exception:
            return 0

    async def handle_tool_pre(self, event: str, data: dict[str, Any]) -> HookResult:
        """Handle tool:pre events to enforce process count limits.

        Args:
            event: The event name (e.g., "tool:pre").
            data: Event data dict including "tool_name" key.

        Returns:
            HookResult with appropriate action based on process count and config.
        """
        # Return continue if disabled
        if not self.config.enabled:
            return HookResult(action="continue")

        # Return continue for non-bash tools
        tool_name = data.get("tool_name", "")
        if tool_name != "bash":
            return HookResult(action="continue")

        # Get current process count
        count = self._get_process_count()

        # Deny if at or above max_process_count
        if count >= self.config.max_process_count:
            return HookResult(
                action="deny",
                reason=(
                    f"Process count ({count}) has reached or exceeded the maximum "
                    f"limit of {self.config.max_process_count}. "
                    f"New bash commands are blocked to prevent system overload. "
                    f"Please wait for existing processes to complete or terminate "
                    f"unnecessary processes before running new commands."
                ),
            )

        # Inject warning if at or above warning_threshold
        if count >= self.config.warning_threshold:
            warning = (
                f'<system-reminder source="hooks-process-guard">\n'
                f"⚠️ **High Process Count Warning**\n\n"
                f"Current process count: {count}\n"
                f"Warning threshold: {self.config.warning_threshold}\n"
                f"Critical limit: {self.config.max_process_count}\n\n"
                f"**Remediation steps:**\n"
                f"- Avoid spawning additional background processes\n"
                f"- Wait for existing processes to complete before running new commands\n"
                f"- Check for runaway processes with: ps aux | sort -rk 3,3 | head -10\n"
                f"- Terminate unnecessary processes with: kill <pid>\n"
                f"- If you see test processes accumulating, ensure tests are cleaning up properly\n"
                f"</system-reminder>"
            )
            return HookResult(
                action="inject_context",
                context_injection=warning,
                context_injection_role="user",
                ephemeral=True,
                append_to_last_tool_result=True,
            )

        # Repeated command detection
        session_id = data.get("session_id", "")
        command = data.get("tool_input", {}).get("command", "")
        now = time.time()
        window_start = now - self.config.repeat_detection_window_seconds

        # Initialize history for this session if not present
        if session_id not in self._command_history:
            self._command_history[session_id] = []

        # Prune entries outside the time window
        self._command_history[session_id] = [
            (ts, cmd)
            for ts, cmd in self._command_history[session_id]
            if ts >= window_start
        ]

        # Count occurrences of this exact command in the window (before recording)
        count = sum(1 for _, cmd in self._command_history[session_id] if cmd == command)

        # Record the new invocation
        self._command_history[session_id].append((now, command))

        # Warn if the command has been repeated too many times
        if count >= self.config.repeat_detection_max_count:
            truncated_cmd = command[:80]
            warning = (
                f'<system-reminder source="hooks-process-guard">\n'
                f"\u26a0\ufe0f **Repeated Command Detected**\n\n"
                f"Command: `{truncated_cmd}`\n"
                f"Repeated count: {count}\n\n"
                f"**Remediation steps:**\n"
                f"- Read the error output from previous executions\n"
                f"- Analyze the root cause of the failure\n"
                f"- Fix the root cause\n"
                f"- Fix the code before re-running\n"
                f"</system-reminder>"
            )
            return HookResult(
                action="inject_context",
                context_injection=warning,
                context_injection_role="user",
                ephemeral=True,
                append_to_last_tool_result=True,
            )

        # Process count is below warning threshold — continue normally
        return HookResult(action="continue")


async def mount(
    coordinator: Any, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Mount the process guard hooks into the coordinator.

    Args:
        coordinator: The Amplifier coordinator instance.
        config: Module configuration dict with process guard settings.

    Returns:
        Module metadata dict with name, version, description, and config.
    """
    guard_config = ProcessGuardConfig(**(config or {}))
    hooks = ProcessGuardHooks(guard_config)

    coordinator.hooks.register(
        "tool:pre",
        hooks.handle_tool_pre,
        priority=10,
    )

    return {
        "name": "hooks-process-guard",
        "version": "0.1.0",
        "description": "Process guard hooks to prevent runaway subprocesses and resource exhaustion",
        "config": {
            "max_process_count": guard_config.max_process_count,
            "warning_threshold": guard_config.warning_threshold,
            "kill_orphans_before_exec": guard_config.kill_orphans_before_exec,
            "kill_patterns": guard_config.kill_patterns,
            "repeat_detection_window_seconds": guard_config.repeat_detection_window_seconds,
            "repeat_detection_max_count": guard_config.repeat_detection_max_count,
            "enabled": guard_config.enabled,
        },
    }
