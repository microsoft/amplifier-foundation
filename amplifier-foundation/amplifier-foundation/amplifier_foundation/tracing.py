"""Tracing utilities for amplifier-foundation.

Provides W3C-compatible trace context ID generation for sub-agent
tracing. Apps decide WHEN to create sub-sessions - this module
provides the HOW for generating traceable IDs.

Based on app-cli's battle-tested `_generate_sub_session_id()` from
session_spawner.py, which follows W3C Trace Context principles.

Philosophy: Mechanism not policy. Apps decide when to spawn sub-sessions.
"""

from __future__ import annotations

import re
import uuid

# W3C Trace Context uses 16 hex chars (8 bytes) for span IDs
_SPAN_HEX_LEN = 16
_DEFAULT_PARENT_SPAN = "0" * _SPAN_HEX_LEN

# Pattern to extract parent/child spans from sub-session IDs
_SPAN_PATTERN = re.compile(r"^([0-9a-f]{16})-([0-9a-f]{16})_")
_TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def generate_sub_session_id(
    agent_name: str | None = None,
    parent_session_id: str | None = None,
    parent_trace_id: str | None = None,
) -> str:
    """Generate a sub-session ID with W3C Trace Context lineage.

    Creates hierarchical IDs that can be traced back to parent sessions
    following W3C Trace Context principles:
    - Parent span ID (16 hex chars) extracted from parent session or trace
    - New child span ID (16 hex chars) for this session
    - Agent name suffix for readability (sanitized for filesystem safety)

    Format: {parent-span}-{child-span}_{agent-name}
    Example: 1234567890abcdef-fedcba0987654321_zen-architect

    Based on app-cli's battle-tested implementation in session_spawner.py.

    Args:
        agent_name: Name of the sub-agent (for human readability)
        parent_session_id: Parent session's ID (for span extraction)
        parent_trace_id: Parent trace ID if using distributed tracing

    Returns:
        Sub-session ID with embedded trace context

    Example:
        # With parent context
        sub_id = generate_sub_session_id(
            agent_name="researcher",
            parent_session_id="abc123def456-7890abcdef123456_planner",
        )
        # "7890abcdef123456-fedcba0987654321_researcher"

        # First-level sub-session (no parent span)
        sub_id = generate_sub_session_id(agent_name="analyzer")
        # "0000000000000000-fedcba0987654321_analyzer"

        # Using trace ID for parent span
        sub_id = generate_sub_session_id(
            agent_name="worker",
            parent_trace_id="12345678901234567890123456789012",
        )
        # "3456789012345678-fedcba0987654321_worker"
    """
    # Sanitize agent name for filesystem safety
    raw_name = (agent_name or "").lower()

    # Replace any non-alphanumeric characters with hyphens
    sanitized = re.sub(r"[^a-z0-9]+", "-", raw_name)
    # Collapse multiple hyphens
    sanitized = re.sub(r"-{2,}", "-", sanitized)
    # Remove leading/trailing hyphens and dots
    sanitized = sanitized.strip("-").lstrip(".")

    # Default to "agent" if empty after sanitization
    if not sanitized:
        sanitized = "agent"

    # Extract parent span ID following W3C Trace Context principles
    parent_span = _DEFAULT_PARENT_SPAN

    if parent_session_id:
        # If parent has our format, extract its child span (becomes our parent span)
        match = _SPAN_PATTERN.match(parent_session_id)
        if match:
            # Extract the child span from parent (second group)
            parent_span = match.group(2)

    # If no parent span found and we have a trace ID, derive parent span from trace
    # Extract middle 16 chars (positions 8-24) from 32-char trace ID
    if parent_span == _DEFAULT_PARENT_SPAN and parent_trace_id and _TRACE_ID_PATTERN.fullmatch(parent_trace_id):
        # Take middle 16 characters (8-24) of the 32-char trace ID
        parent_span = parent_trace_id[8:24]

    # Generate new span ID for this child session
    child_span = uuid.uuid4().hex[:_SPAN_HEX_LEN]

    return f"{parent_span}-{child_span}_{sanitized}"
