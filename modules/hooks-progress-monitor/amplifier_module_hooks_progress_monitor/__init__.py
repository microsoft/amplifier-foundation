"""Progress Monitor Hooks Module

Detects analysis paralysis patterns and injects corrective prompts.

Monitors the ratio of read operations (read_file, grep, glob, web_fetch, web_search)
to write operations (write_file, edit_file) and injects warnings when agents appear
stuck in endless research loops.

Detection Patterns:
- High read/write ratio: Many reads with zero writes suggests analysis paralysis
- Repeated file reads: Reading the same file multiple times indicates uncertainty
- Endless web research: Excessive web searches without implementation

Thresholds (configurable):
- read_threshold: Trigger warning after N reads with 0 writes (default: 30)
- same_file_threshold: Warn after reading same file N times (default: 3)
- warning_interval: Re-warn every N additional reads (default: 15)
"""

from dataclasses import dataclass
from dataclasses import field
from typing import Any

from amplifier_core import HookResult


# Read operation tools
READ_TOOLS = frozenset(
    {
        "read_file",
        "grep",
        "glob",
        "web_fetch",
        "web_search",
    }
)

# Write operation tools
WRITE_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
    }
)


@dataclass
class ProgressMonitorConfig:
    """Configuration for progress monitoring."""

    read_threshold: int = 30  # Warn after this many reads with 0 writes
    same_file_threshold: int = 3  # Warn after reading same file this many times
    warning_interval: int = 15  # Re-warn every N additional reads
    enabled: bool = True


@dataclass
class ProgressState:
    """Tracks read/write progress within a session."""

    read_count: int = 0
    write_count: int = 0
    file_read_counts: dict[str, int] = field(default_factory=dict)
    last_warning_at: int = 0  # Read count when last warning was issued
    warnings_issued: int = 0


class ProgressMonitorHooks:
    """Hook handlers for detecting analysis paralysis."""

    def __init__(self, config: ProgressMonitorConfig):
        self.config = config
        # Track state per session (keyed by session_id)
        self._states: dict[str, ProgressState] = {}

    def _get_state(self, session_id: str) -> ProgressState:
        """Get or create state for a session."""
        if session_id not in self._states:
            self._states[session_id] = ProgressState()
        return self._states[session_id]

    async def handle_tool_post(self, _event: str, data: dict[str, Any]) -> HookResult:
        """Track tool usage and detect paralysis patterns."""
        if not self.config.enabled:
            return HookResult(action="continue")

        tool_name = data.get("tool_name", "")
        session_id = data.get("session_id", "default")
        state = self._get_state(session_id)

        # Track write operations
        if tool_name in WRITE_TOOLS:
            state.write_count += 1
            # Reset warning state on writes - agent is making progress
            state.last_warning_at = 0
            state.warnings_issued = 0
            return HookResult(action="continue")

        # Track read operations
        if tool_name in READ_TOOLS:
            state.read_count += 1

            # Track specific file reads
            if tool_name == "read_file":
                tool_input = data.get("tool_input", {})
                if isinstance(tool_input, dict):
                    file_path = tool_input.get("file_path", "")
                    if file_path:
                        state.file_read_counts[file_path] = (
                            state.file_read_counts.get(file_path, 0) + 1
                        )

                        # Check for repeated file reads
                        if (
                            state.file_read_counts[file_path]
                            == self.config.same_file_threshold
                        ):
                            return self._inject_same_file_warning(file_path, state)

            # Check for high read count with no writes
            if (
                state.write_count == 0
                and state.read_count >= self.config.read_threshold
            ):
                reads_since_warning = state.read_count - state.last_warning_at

                # Issue warning at threshold and then at intervals
                if (
                    state.last_warning_at == 0
                    or reads_since_warning >= self.config.warning_interval
                ):
                    return self._inject_paralysis_warning(state)

        return HookResult(action="continue")

    def _inject_paralysis_warning(self, state: ProgressState) -> HookResult:
        """Inject a warning about potential analysis paralysis."""
        state.last_warning_at = state.read_count
        state.warnings_issued += 1

        # Escalate urgency with repeated warnings
        if state.warnings_issued == 1:
            urgency = "Note"
            instruction = "Consider starting implementation with what you know."
        elif state.warnings_issued == 2:
            urgency = "Warning"
            instruction = "You should start implementing NOW. Write a file, even if it's just a skeleton."
        else:
            urgency = "CRITICAL"
            instruction = (
                "STOP READING. Write code IMMEDIATELY. You have enough information."
            )

        warning = f"""<system-reminder source="hooks-progress-monitor">
**{urgency}: Potential Analysis Paralysis Detected**

You have performed {state.read_count} read operations with 0 write operations.

{instruction}

Remember:
- Working code that needs iteration > perfect understanding with zero output
- If you've read a file 3+ times, you have enough information
- Implementation reveals gaps faster than research

This is warning #{state.warnings_issued}. Please make progress.
</system-reminder>"""

        return HookResult(
            action="inject_context",
            context_injection=warning,
            context_injection_role="user",
            ephemeral=True,
            append_to_last_tool_result=True,
        )

    def _inject_same_file_warning(
        self, file_path: str, state: ProgressState
    ) -> HookResult:
        """Inject a warning about reading the same file repeatedly."""
        # Extract just the filename for readability
        filename = file_path.split("/")[-1] if "/" in file_path else file_path

        warning = f"""<system-reminder source="hooks-progress-monitor">
**Note: Repeated File Read Detected**

You have read `{filename}` {self.config.same_file_threshold} times.

If you're re-reading to "make sure" or "understand better", STOP.
You have the information you need. Start implementing.

The 3-Read Rule: After reading a file 3 times, you must implement, not read again.
</system-reminder>"""

        return HookResult(
            action="inject_context",
            context_injection=warning,
            context_injection_role="user",
            ephemeral=True,
            append_to_last_tool_result=True,
        )


async def mount(
    coordinator: Any, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Mount the progress monitor hooks module.

    Config options:
        enabled: bool (default: True) - Enable/disable monitoring
        read_threshold: int (default: 30) - Reads before first warning
        same_file_threshold: int (default: 3) - Same-file reads before warning
        warning_interval: int (default: 15) - Reads between repeated warnings
    """
    config = config or {}

    monitor_config = ProgressMonitorConfig(
        enabled=config.get("enabled", True),
        read_threshold=config.get("read_threshold", 30),
        same_file_threshold=config.get("same_file_threshold", 3),
        warning_interval=config.get("warning_interval", 15),
    )

    hooks = ProgressMonitorHooks(monitor_config)

    # Register hook - runs after tool execution to track operations
    coordinator.hooks.register("tool:post", hooks.handle_tool_post, priority=50)

    return {
        "name": "hooks-progress-monitor",
        "version": "0.1.0",
        "description": "Detects analysis paralysis and injects corrective prompts",
        "config": {
            "enabled": monitor_config.enabled,
            "read_threshold": monitor_config.read_threshold,
            "same_file_threshold": monitor_config.same_file_threshold,
            "warning_interval": monitor_config.warning_interval,
        },
    }
