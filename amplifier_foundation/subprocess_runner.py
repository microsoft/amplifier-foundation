"""Subprocess IPC config serialization helpers for subprocess session isolation.

Subprocess Isolation Philosophy
--------------------------------
This module provides the *mechanism*, not the *policy*, for running Amplifier
sessions in isolated subprocesses.

The pattern:
  1. The **parent** process assembles the session configuration dict (providers,
     tools, hooks, etc.) and serializes it — along with the prompt, its own
     session ID, and the project path — to a temp file using
     ``serialize_subprocess_config()``.
  2. The **child** process reads the temp file, calls
     ``deserialize_subprocess_config()``, then creates a fresh ``AmplifierSession``
     from the validated dict.

This keeps credentials and runtime objects in the parent, while the child
starts from a clean slate with only the JSON-serializable portions of the
config.  The parent is responsible for deciding *what* to serialize; this
module is responsible for *how* to serialize and validate it.
"""

from __future__ import annotations

import json
from typing import Any

REQUIRED_KEYS = ("config", "prompt", "parent_id", "project_path")


def serialize_subprocess_config(
    config: dict[str, Any],
    prompt: str,
    parent_id: str,
    project_path: str,
    session_id: str | None = None,
) -> str:
    """Serialize subprocess configuration to a JSON string.

    Packages all information a child process needs to start an isolated
    ``AmplifierSession``: the session config dict, the prompt to run, the
    parent session ID for traceability, the project path, and an optional
    pre-assigned session ID for the child.

    Args:
        config: Session configuration dict (providers, tools, hooks, etc.).
            Must be JSON-serializable.
        prompt: The prompt the child session will run.
        parent_id: Session ID of the parent process (for delegation tracing).
        project_path: Absolute path to the project directory the child should
            operate in.
        session_id: Optional pre-assigned session ID for the child session.
            If ``None``, the child will generate its own ID.

    Returns:
        JSON string containing all fields.
    """
    payload: dict[str, Any] = {
        "config": config,
        "prompt": prompt,
        "parent_id": parent_id,
        "project_path": project_path,
        "session_id": session_id,
    }
    return json.dumps(payload)


def deserialize_subprocess_config(data: str) -> dict[str, Any]:
    """Deserialize and validate subprocess configuration from a JSON string.

    Parses the JSON string produced by ``serialize_subprocess_config()`` and
    validates that all required keys are present.

    Args:
        data: JSON string containing the subprocess configuration.

    Returns:
        Dict with keys: ``config``, ``prompt``, ``parent_id``,
        ``project_path``, and ``session_id`` (may be ``None``).

    Raises:
        json.JSONDecodeError: If ``data`` is not valid JSON.
        ValueError: If any required key is missing from the parsed payload.
    """
    # Let JSONDecodeError propagate naturally on malformed input
    payload: dict[str, Any] = json.loads(data)

    missing = [key for key in REQUIRED_KEYS if key not in payload]
    if missing:
        raise ValueError(f"Subprocess config is missing required keys: {missing}")

    return payload
