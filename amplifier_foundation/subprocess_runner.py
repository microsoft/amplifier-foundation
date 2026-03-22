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

import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from amplifier_core import AmplifierSession
from amplifier_foundation.bundle import BundleModuleResolver

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ("config", "prompt", "parent_id", "project_path")

DEFAULT_MAX_SUBPROCESS: int = 4

# Framing markers for subprocess stdout protocol — prevents stray print() calls
# from corrupting the result payload.
RESULT_START_MARKER = "<<<AMPLIFIER_RESULT_START>>>"
RESULT_END_MARKER = "<<<AMPLIFIER_RESULT_END>>>"
_subprocess_semaphore: asyncio.Semaphore | None = None
_semaphore_limit: int = DEFAULT_MAX_SUBPROCESS


def _extract_framed_result(stdout: str) -> str:
    """Extract the result payload from framed subprocess stdout.

    Locates the content between ``RESULT_START_MARKER`` and ``RESULT_END_MARKER``
    in the subprocess stdout string.  Any output printed outside the envelope
    (e.g. by third-party code or debug ``print()`` calls) is ignored.

    Args:
        stdout: The full stdout string from the child process.

    Returns:
        The stripped content between the start and end markers.

    Raises:
        RuntimeError: If either marker is absent from ``stdout``.
    """
    start_idx = stdout.find(RESULT_START_MARKER)
    end_idx = stdout.find(RESULT_END_MARKER)
    if start_idx == -1 or end_idx == -1:
        logger.debug("Unframed subprocess output (no result envelope): %r", stdout)
        raise RuntimeError("missing result envelope")
    content_start = start_idx + len(RESULT_START_MARKER)
    return stdout[content_start:end_idx].strip()


def _get_semaphore(max_concurrent: int | None = None) -> asyncio.Semaphore:
    """Return the module-level semaphore, creating or recreating it as needed.

    Lazily creates the semaphore on first call. If ``max_concurrent`` differs
    from the current limit, the semaphore is recreated with the new limit.

    Args:
        max_concurrent: Maximum number of concurrent subprocess sessions.
            If ``None``, uses the current ``_semaphore_limit``.

    Returns:
        The asyncio.Semaphore for gating concurrent subprocesses.
    """
    global _subprocess_semaphore, _semaphore_limit

    limit = max_concurrent if max_concurrent is not None else _semaphore_limit

    if _subprocess_semaphore is None or limit != _semaphore_limit:
        _semaphore_limit = limit
        _subprocess_semaphore = asyncio.Semaphore(limit)

    return _subprocess_semaphore


def serialize_subprocess_config(
    config: dict[str, Any],
    prompt: str,
    parent_id: str,
    project_path: str,
    session_id: str | None = None,
    module_paths: dict[str, str] | None = None,
    bundle_package_paths: list[str] | None = None,
    sys_paths: list[str] | None = None,
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
        module_paths: Optional mapping of module names to their source paths
            for bundle context propagation. Defaults to empty dict when None.
        bundle_package_paths: Optional list of bundle package root paths.
            Defaults to empty list when None.
        sys_paths: Optional list of additional sys.path entries to inject in
            the child process. Defaults to empty list when None.

    Returns:
        JSON string containing all fields.
    """
    payload: dict[str, Any] = {
        "config": config,
        "prompt": prompt,
        "parent_id": parent_id,
        "project_path": project_path,
        "session_id": session_id,
        "module_paths": module_paths if module_paths is not None else {},
        "bundle_package_paths": bundle_package_paths
        if bundle_package_paths is not None
        else [],
        "sys_paths": sys_paths if sys_paths is not None else [],
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

    # Set defaults for bundle context fields — backward compatible with old payloads
    payload.setdefault("module_paths", {})
    payload.setdefault("bundle_package_paths", [])
    payload.setdefault("sys_paths", [])

    return payload


async def run_session_in_subprocess(
    config: dict[str, Any],
    prompt: str,
    parent_id: str,
    project_path: str,
    session_id: str | None = None,
    timeout: int = 1800,
    max_concurrent: int | None = None,
) -> str:
    """Run an Amplifier session in an isolated subprocess.

    Serializes the session config to a temp file, spawns a child process
    running the subprocess_runner module, waits for it to complete, and
    returns the result from stdout.

    Args:
        config: Session configuration dict (providers, tools, hooks, etc.).
            Must be JSON-serializable.
        prompt: The prompt the child session will run.
        parent_id: Session ID of the parent process (for delegation tracing).
        project_path: Absolute path to the project directory the child should
            operate in.
        session_id: Optional pre-assigned session ID for the child session.
            If ``None``, the child will generate its own ID.
        timeout: Seconds to wait for the subprocess to complete (default: 1800).
        max_concurrent: Maximum number of concurrent subprocess sessions allowed.
            If ``None``, uses the current module-level semaphore limit
            (default: ``DEFAULT_MAX_SUBPROCESS``).

    Returns:
        The output string from the child session (stdout, stripped).

    Raises:
        TimeoutError: If the subprocess does not complete within ``timeout`` seconds.
        RuntimeError: If the subprocess exits with a non-zero return code.
    """
    serialized = serialize_subprocess_config(
        config=config,
        prompt=prompt,
        parent_id=parent_id,
        project_path=project_path,
        session_id=session_id,
    )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="amp_subprocess_", delete=False
    ) as f:
        tmp_path = f.name
        f.write(serialized)

    semaphore = _get_semaphore(max_concurrent)

    try:
        async with semaphore:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "amplifier_foundation.subprocess_runner",
                tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=project_path,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise TimeoutError(f"Subprocess session timed out after {timeout}s")

            if process.returncode != 0:
                stderr_text = stderr.decode("utf-8")
                raise RuntimeError(
                    f"Subprocess session failed (exit code {process.returncode}): {stderr_text}"
                )

            raw_stdout = stdout.decode("utf-8")
            return _extract_framed_result(raw_stdout)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            logger.warning("Failed to clean up temp file: %s", tmp_path)


async def _run_child_session(config_path: str) -> str:
    """Run a child Amplifier session from a serialized config file.

    Reads the config file, injects sys.path entries, changes the working
    directory to the project path, creates an ``AmplifierSession``, mounts
    the module resolver if module paths are provided, calls ``initialize()``,
    executes the prompt, and returns the result.  Cleanup is guaranteed to run
    via ``try/finally`` even when ``execute()`` raises.

    Args:
        config_path: Path to the JSON config file produced by
            ``serialize_subprocess_config()``.

    Returns:
        The result string returned by ``session.execute()``.

    Raises:
        Any exception raised by ``session.execute()`` after cleanup completes.
    """
    with open(config_path) as f:
        data = f.read()

    payload = deserialize_subprocess_config(data)
    config: dict[str, Any] = payload["config"]
    prompt: str = payload["prompt"]
    parent_id: str = payload["parent_id"]
    project_path: str = payload["project_path"]
    session_id: str | None = payload.get("session_id")
    # (1) Extract module_paths, bundle_package_paths, sys_paths from payload
    module_paths: dict[str, str] = payload.get("module_paths", {})
    bundle_package_paths: list[str] = payload.get("bundle_package_paths", [])
    sys_paths: list[str] = payload.get("sys_paths", [])

    # (2) Add all sys.path and bundle_package_paths entries BEFORE session creation
    for path_entry in (*sys_paths, *bundle_package_paths):
        if path_entry not in sys.path:
            logger.debug("Adding sys.path entry: %s", path_entry)
            sys.path.insert(0, path_entry)

    # (3) Validate and chdir to project_path
    os.chdir(project_path)

    # (4) Create AmplifierSession
    session = AmplifierSession(
        config=config, parent_id=parent_id, session_id=session_id
    )

    # (5) If module_paths non-empty, construct BundleModuleResolver and mount
    #     on coordinator as 'module-source-resolver' BEFORE initialize()
    if module_paths:
        resolver = BundleModuleResolver(
            {name: Path(path) for name, path in module_paths.items()}
        )
        await session.coordinator.mount("module-source-resolver", resolver)

    # (6) Call session.initialize() BEFORE session.execute()
    await session.initialize()

    # (7) Wrap execute/cleanup in try/finally
    try:
        result: str = await session.execute(prompt)
        return result
    finally:
        await session.cleanup()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(
            "Usage: python -m amplifier_foundation.subprocess_runner <config_path>",
            file=sys.stderr,
        )
        sys.exit(1)
    config_path = sys.argv[1]
    try:
        output = asyncio.run(_run_child_session(config_path))
        print(RESULT_START_MARKER)
        print(output, end="")
        print()
        print(RESULT_END_MARKER)
    except Exception as e:
        print(f"Subprocess session error: {e}", file=sys.stderr)
        sys.exit(1)
