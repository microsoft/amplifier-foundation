"""Process guard hooks module for Amplifier.

Monitors and limits subprocess creation to prevent runaway processes
and resource exhaustion during tool execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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

    Stub — to be implemented in Task 6.
    """

    def __init__(self, config: ProcessGuardConfig) -> None:
        self.config = config


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
        hooks.handle_tool_pre,  # type: ignore[attr-defined]  # stub: Task 6 adds this
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
