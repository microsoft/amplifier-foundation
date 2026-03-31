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
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

from amplifier_core import AmplifierSession
from amplifier_foundation.bundle import BundleModuleResolver

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ("config", "prompt", "parent_id", "project_path")

# Environment variable allowlist — controls what the parent process passes to
# child subprocesses.  Only variables matching an allowed prefix or exact name
# are forwarded; everything else (database passwords, internal tokens, etc.)
# is silently dropped.
_ENV_ALLOWED_PREFIXES: tuple[str, ...] = (
    "AMPLIFIER_",
    "ANTHROPIC_",
    "OPENAI_",
    "AZURE_OPENAI_",
    "AZURE_",
    "GOOGLE_",
    "AWS_",
    "GITHUB_",
    "GH_",
)

_ENV_ALLOWED_EXACT: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "LC_ALL",
        "TERM",
        "SHELL",
        "TMPDIR",
        "TMP",
        "TEMP",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "CONDA_DEFAULT_ENV",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)


def _build_child_env() -> dict[str, str]:
    """Build a filtered environment dict for child subprocesses.

    Iterates ``os.environ`` and includes only variables that are in the
    ``_ENV_ALLOWED_EXACT`` set or whose name starts with one of the
    ``_ENV_ALLOWED_PREFIXES``.  All other variables are excluded to prevent
    unrelated secrets (database passwords, internal tokens, etc.) from leaking
    into child processes.

    Returns:
        A new dict containing only the allowed environment variables.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if key in _ENV_ALLOWED_EXACT
        or any(key.startswith(prefix) for prefix in _ENV_ALLOWED_PREFIXES)
    }


# Credential patterns — used by _sanitize_error() to redact sensitive values
# from exception messages before they propagate to callers.
_CREDENTIAL_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9\-_]{10,}"),
    re.compile(r"key=\s*\S+", re.IGNORECASE),
    re.compile(r"token=\s*\S+", re.IGNORECASE),
    re.compile(r"secret=\s*\S+", re.IGNORECASE),
    re.compile(r"password=\s*\S+", re.IGNORECASE),
    re.compile(r"Bearer\s+\S+"),
]


def _sanitize_error(msg: str) -> str:
    """Replace credential patterns in an error message with '[REDACTED]'.

    Protects against leaking API keys, tokens, passwords, and other sensitive
    values in exception messages that may appear in logs or be shown to users.

    Args:
        msg: The error message string to sanitize.

    Returns:
        The message with all recognized credential patterns replaced by
        ``'[REDACTED]'``.
    """
    for pattern in _CREDENTIAL_PATTERNS:
        msg = pattern.sub("[REDACTED]", msg)
    return msg


def _validate_project_path(path: str) -> None:
    """Validate that the given path is an existing directory.

    Resolves the path and checks that it exists and is a directory.

    Args:
        path: The path to validate.

    Raises:
        ValueError: If the path does not exist or is not a directory.
    """
    resolved = Path(path).resolve()
    if not resolved.is_dir():
        raise ValueError(f"{path!r} does not exist or is not a directory")


DEFAULT_MAX_SUBPROCESS: int = 4

# Framing markers for subprocess stdout protocol — prevents stray print() calls
# from corrupting the result payload.
RESULT_START_MARKER = "<<<AMPLIFIER_RESULT_START>>>"
RESULT_END_MARKER = "<<<AMPLIFIER_RESULT_END>>>"
_subprocess_semaphore: asyncio.Semaphore | None = None
_semaphore_limit: int = DEFAULT_MAX_SUBPROCESS
_semaphore_configured: bool = False


def configure_subprocess_limit(max_concurrent: int) -> None:
    """Configure the maximum number of concurrent subprocess sessions (set-once).

    Must be called before the first subprocess is launched.  Subsequent calls
    with the *same* value are a no-op; calls with a *different* value raise
    ``RuntimeError``.

    Args:
        max_concurrent: Maximum number of concurrent subprocess sessions.

    Raises:
        RuntimeError: If called again after already configured with a different value.
    """
    global _semaphore_limit, _semaphore_configured

    if _semaphore_configured:
        if max_concurrent != _semaphore_limit:
            raise RuntimeError("already configured")
        # Same value — no-op
        return

    _semaphore_limit = max_concurrent
    _semaphore_configured = True


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


def _get_semaphore() -> asyncio.Semaphore:
    """Return the module-level semaphore, creating it lazily on first call.

    Uses ``_semaphore_limit`` (set via ``configure_subprocess_limit()`` or
    defaulting to ``DEFAULT_MAX_SUBPROCESS``) to size the semaphore.

    Returns:
        The asyncio.Semaphore for gating concurrent subprocesses.
    """
    global _subprocess_semaphore

    if _subprocess_semaphore is None:
        _subprocess_semaphore = asyncio.Semaphore(_semaphore_limit)

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
    mention_mappings: dict[str, str] | None = None,
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
        mention_mappings: Optional mapping of bundle namespace to base path
            for @mention resolution in the child session. Defaults to empty
            dict when None.

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
        "mention_mappings": mention_mappings if mention_mappings is not None else {},
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
    payload.setdefault("mention_mappings", {})

    return payload


async def run_session_in_subprocess(
    config: dict[str, Any],
    prompt: str,
    parent_id: str,
    project_path: str,
    session_id: str | None = None,
    timeout: int = 1800,
    module_paths: dict[str, str] | None = None,
    bundle_package_paths: list[str] | None = None,
    sys_paths: list[str] | None = None,
    mention_mappings: dict[str, str] | None = None,
) -> str:
    """Run an Amplifier session in an isolated subprocess.

    Serializes the session config to a temp file, spawns a child process
    running the subprocess_runner module, waits for it to complete, and
    returns the result from stdout.

    The concurrency limit is controlled globally via ``configure_subprocess_limit()``
    (default: ``DEFAULT_MAX_SUBPROCESS``).

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
        module_paths: Optional mapping of module names to their source paths
            for bundle context propagation.
        bundle_package_paths: Optional list of bundle package root paths.
        sys_paths: Optional list of additional sys.path entries to inject in
            the child process.
        mention_mappings: Optional mapping of bundle namespace to base path
            for @mention resolution in the child session.

    Returns:
        The output string from the child session (stdout, stripped).

    Raises:
        TimeoutError: If the subprocess does not complete within ``timeout`` seconds.
        RuntimeError: If the subprocess exits with a non-zero return code.
    """
    _validate_project_path(project_path)

    serialized = serialize_subprocess_config(
        config=config,
        prompt=prompt,
        parent_id=parent_id,
        project_path=project_path,
        session_id=session_id,
        module_paths=module_paths,
        bundle_package_paths=bundle_package_paths,
        sys_paths=sys_paths,
        mention_mappings=mention_mappings,
    )

    semaphore = _get_semaphore()

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", prefix="amp_subprocess_", delete=False
        ) as f:
            tmp_path = f.name
            f.write(serialized)

        # Assert permissions: ensure group/other bits are not set
        current_mode = stat.S_IMODE(os.stat(tmp_path).st_mode)
        if current_mode & (stat.S_IRWXG | stat.S_IRWXO):
            os.chmod(tmp_path, 0o600)

        async with semaphore:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "amplifier_foundation.subprocess_runner",
                tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=project_path,
                env=_build_child_env(),
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
                logger.debug("Subprocess stderr: %s", stderr_text)
                sanitized = _sanitize_error(stderr_text)
                raise RuntimeError(
                    f"Subprocess session failed (exit code {process.returncode}): {sanitized}"
                )

            raw_stdout = stdout.decode("utf-8")
            return _extract_framed_result(raw_stdout)
    finally:
        if tmp_path is not None:
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

    NOTE: Subprocess children do not have access to the parent's approval_system
    or display_system. These are live runtime objects that cannot cross process
    boundaries. For recipe-dispatched agents this is acceptable — approval gates
    are at the recipe level, not inside agent sessions. For sessions requiring
    interactive approval, use in-process mode (use_subprocess=False).

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
    _validate_project_path(project_path)
    os.chdir(project_path)

    # (4) Create AmplifierSession
    session = AmplifierSession(
        config=config, parent_id=parent_id, session_id=session_id
    )

    # (5) If module_paths non-empty, construct BundleModuleResolver and mount
    #     on coordinator as 'module-source-resolver' BEFORE initialize().
    #     Wrap with AppModuleResolver + FoundationSettingsResolver fallback so
    #     provider modules (provider-anthropic, etc.) configured via user
    #     settings (~/.amplifier/settings.yaml) can also be resolved.  This
    #     mirrors what resume_sub_session() does in session_spawner.py.
    #     Graceful degradation: fall back to bare BundleModuleResolver if
    #     amplifier_app_cli is not installed (non-CLI environments).
    if module_paths:
        bundle_resolver = BundleModuleResolver(
            {name: Path(path) for name, path in module_paths.items()}
        )
        try:
            from amplifier_app_cli.lib.bundle_loader import AppModuleResolver  # type: ignore[import-untyped,import-not-found]
            from amplifier_app_cli.paths import create_foundation_resolver  # type: ignore[import-untyped,import-not-found]

            resolver = AppModuleResolver(
                bundle_resolver=bundle_resolver,
                settings_resolver=create_foundation_resolver(),
            )
            logger.debug(
                "Subprocess child: wrapped BundleModuleResolver with AppModuleResolver "
                "(settings fallback enabled for provider modules)"
            )
        except ImportError:
            # amplifier_app_cli not available — non-CLI environment, use bare resolver
            resolver = bundle_resolver
            logger.debug(
                "Subprocess child: amplifier_app_cli not available, "
                "using bare BundleModuleResolver (provider modules from settings unavailable)"
            )
        await session.coordinator.mount("module-source-resolver", resolver)

    # (6) Call session.initialize() BEFORE session.execute()
    logger.debug(
        "Initializing subprocess child session with %d tools in config",
        len(config.get("tools", [])),
    )
    await session.initialize()

    # (6a) Register capabilities that the in-process spawn path provides but
    # subprocess children lack.  Ordered from most critical to least.
    #
    # session.working_dir: tool-filesystem uses this for path validation.
    # Without it, writes to absolute paths outside CWD may be blocked.
    session.coordinator.register_capability("session.working_dir", project_path)

    # self_delegation_depth: tracks recursion depth for delegation loops.
    # Matches what spawn_sub_session() and resume_sub_session() register.
    session.coordinator.register_capability("self_delegation_depth", 0)

    # mention_resolver: enables @namespace:path resolution in subprocess agents.
    # Requires mention_mappings in the payload and amplifier_app_cli installed.
    mention_mappings: dict[str, str] = payload.get("mention_mappings", {})
    if mention_mappings:
        try:
            from pathlib import Path as _Path

            from amplifier_app_cli.lib.mention_loading.app_resolver import (  # type: ignore[import-untyped,import-not-found]
                AppMentionResolver,
            )

            session.coordinator.register_capability(
                "mention_resolver",
                AppMentionResolver(
                    bundle_mappings={k: _Path(v) for k, v in mention_mappings.items()}
                ),
            )
            logger.debug(
                "Subprocess child: registered AppMentionResolver with %d bundle mappings",
                len(mention_mappings),
            )
        except ImportError:
            logger.debug(
                "Subprocess child: amplifier_app_cli not available, "
                "mention_resolver not registered (@mention resolution unavailable)"
            )

    # mention_deduplicator: prevents redundant context inclusion within a session.
    # ContentDeduplicator is available in amplifier_foundation.mentions.
    from amplifier_foundation.mentions import ContentDeduplicator

    session.coordinator.register_capability(
        "mention_deduplicator", ContentDeduplicator()
    )

    logger.debug("Subprocess child session initialized, capabilities registered")

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
    import json as _json

    try:
        output = asyncio.run(_run_child_session(config_path))
        result_envelope = _json.dumps(
            {
                "output": output,
                "status": "success",
                "turn_count": 1,  # TODO: capture from orchestrator:complete event in future
                "metadata": {},
            }
        )
        print(RESULT_START_MARKER)
        print(result_envelope, end="")
        print()
        print(RESULT_END_MARKER)
    except Exception as e:
        error_envelope = _json.dumps(
            {
                "output": "",
                "status": "error",
                "error": str(e),
                "turn_count": 0,
                "metadata": {},
            }
        )
        print(RESULT_START_MARKER)
        print(error_envelope, end="")
        print()
        print(RESULT_END_MARKER)
        print(f"Subprocess session error: {e}", file=sys.stderr)
        sys.exit(1)
