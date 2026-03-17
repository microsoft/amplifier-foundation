"""Layer 2 integration tests for amplifier_foundation.grpc_adapter.

Uses subprocess-based integration tests with a real gRPC server.
The adapter is spawned as a child process; tests communicate via gRPC.
"""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Mock tool module source (inline)
# Written verbatim to tmpdir/test_tool/__init__.py
# ---------------------------------------------------------------------------

MOCK_TOOL_MODULE = '''\
"""Minimal mock tool module for integration testing."""
from __future__ import annotations

from typing import Any


class _ToolResult:
    """Minimal tool result compatible with ToolServiceAdapter expectations."""

    def __init__(self, success: bool, output: Any, error: Any = None) -> None:
        self.success = success
        self.output = output
        self.error = error


class TestTool:
    """Test tool that echoes its string input back with an 'echo: ' prefix."""

    name = "test-tool"
    description = "A test tool that echoes input"

    async def execute(self, input: Any) -> _ToolResult:  # noqa: A002
        text = input if isinstance(input, str) else str(input)
        return _ToolResult(success=True, output=f"echo: {text}")


_instance = TestTool()

# Module-level attributes so the module itself satisfies the Tool protocol
# (runtime_checkable Protocol checks attribute presence, not property descriptors)
name = _instance.name
description = _instance.description
execute = _instance.execute


def mount() -> None:
    """No-op mount; called by _load_module_object during startup."""
    pass
'''


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _write_test_module(tmpdir: str) -> Path:
    """Create the mock tool module directory structure under *tmpdir*.

    Creates::

        {tmpdir}/test-tool/
            test_tool/
                __init__.py   ← MOCK_TOOL_MODULE

    The ``test-tool`` directory is the **module_path** consumed by
    ``_load_module_object``:  it is added to ``sys.path`` and the package
    ``test_tool`` (hyphen → underscore) is imported from inside it.

    Returns:
        ``Path(tmpdir) / "test-tool"`` — the value to pass to ``_make_manifest``.
    """
    module_dir = Path(tmpdir) / "test-tool"
    module_dir.mkdir(exist_ok=True)
    pkg_dir = module_dir / "test_tool"
    pkg_dir.mkdir(exist_ok=True)
    (pkg_dir / "__init__.py").write_text(MOCK_TOOL_MODULE)
    return module_dir


def _make_manifest(module_path: Path) -> str:
    """Return a JSON manifest string for the test-tool at *module_path*.

    Args:
        module_path: Path to the module directory (e.g. ``tmpdir/test-tool``).

    Returns:
        JSON string with ``module``, ``type``, and ``path`` fields.
    """
    return json.dumps(
        {
            "module": "test-tool",
            "type": "tool",
            "path": str(module_path),
        }
    )


def _spawn_adapter(
    manifest: str,
    port: int = 0,
    env_extra: dict[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    """Spawn the grpc_adapter subprocess with *manifest* on stdin.

    Writes the manifest to stdin then closes the pipe so that
    ``_read_manifest()`` (which does ``sys.stdin.read()``) can reach EOF.

    Args:
        manifest: JSON manifest string to send on stdin.
        port: ``--port`` argument (0 = OS-assigned).
        env_extra: Additional environment variables to merge in.

    Returns:
        Running ``Popen`` object; caller is responsible for cleanup.
    """
    env = os.environ.copy()
    env["AMPLIFIER_AUTH_TOKEN"] = "test-token"
    env["AMPLIFIER_KERNEL_ENDPOINT"] = "localhost:0"
    if env_extra:
        env.update(env_extra)

    cmd = [
        sys.executable,
        "-m",
        "amplifier_foundation.grpc_adapter",
        "--port",
        str(port),
    ]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    # Write manifest and close stdin → subprocess's sys.stdin.read() returns
    assert proc.stdin is not None
    proc.stdin.write(manifest.encode("utf-8"))
    proc.stdin.close()
    return proc


def _read_first_line(proc: subprocess.Popen[bytes], timeout: float = 15.0) -> str:
    """Read the first newline-terminated line from *proc* stdout.

    Uses ``selectors`` to avoid blocking indefinitely when the adapter
    fails to start (guards against test hangs).

    Args:
        proc: Running subprocess with ``stdout=PIPE``.
        timeout: Maximum seconds to wait.

    Returns:
        Decoded, stripped first line (e.g. ``"READY:50123"``).

    Raises:
        TimeoutError: If no newline arrives within *timeout* seconds.
    """
    assert proc.stdout is not None
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)

    line = b""
    deadline = time.monotonic() + timeout

    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"No line received within {timeout}s (buffer: {line!r})"
                )

            ready = sel.select(timeout=remaining)
            if not ready:
                raise TimeoutError(
                    f"No line received within {timeout}s (buffer: {line!r})"
                )

            # read1() reads whatever is immediately available in the BufferedReader's
            # internal buffer (or the OS buffer) without blocking for more data.
            # Using read(1) instead would drain one byte then stall the next
            # sel.select() because the BufferedReader already consumed the OS data.
            chunk = proc.stdout.read1(4096)  # type: ignore[attr-defined]
            if not chunk:
                # EOF — return whatever we have
                break
            line += chunk
            if b"\n" in line:
                break
    finally:
        sel.close()

    return line.split(b"\n")[0].decode("utf-8").strip()


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestAdapterHappyPath:
    """Five integration tests covering successful adapter startup and RPC calls."""

    def test_adapter_prints_ready_with_port(self) -> None:
        """Adapter prints ``READY:<port>`` with port > 0 on successful startup."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = _write_test_module(tmpdir)
            manifest = _make_manifest(module_path)
            proc = _spawn_adapter(manifest)
            try:
                line = _read_first_line(proc)
                assert line.startswith("READY:"), (
                    f"Expected 'READY:<port>', got: {line!r}\n"
                    f"stderr: {proc.stderr.read().decode() if proc.stderr else ''}"
                )
                port = int(line.split(":", 1)[1])
                assert port > 0, f"Expected port > 0, got {port}"
            finally:
                proc.terminate()
                proc.wait(timeout=5)

    def test_grpc_client_get_spec(self) -> None:
        """gRPC ``GetSpec`` returns ``name='test-tool'``."""
        import grpc
        from amplifier_core._grpc_gen import amplifier_module_pb2 as pb2
        from amplifier_core._grpc_gen import amplifier_module_pb2_grpc as pb2_grpc

        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = _write_test_module(tmpdir)
            manifest = _make_manifest(module_path)
            proc = _spawn_adapter(manifest)
            try:
                line = _read_first_line(proc)
                port = int(line.split(":", 1)[1])

                channel = grpc.insecure_channel(f"127.0.0.1:{port}")
                try:
                    stub = pb2_grpc.ToolServiceStub(channel)
                    spec = stub.GetSpec(pb2.Empty())  # type: ignore[attr-defined]
                    assert spec.name == "test-tool", (
                        f"Expected name='test-tool', got {spec.name!r}"
                    )
                finally:
                    channel.close()
            finally:
                proc.terminate()
                proc.wait(timeout=5)

    def test_grpc_client_execute(self) -> None:
        """gRPC ``Execute`` returns ``success=True`` and ``b'echo: hello'`` in output."""
        import grpc
        from amplifier_core._grpc_gen import amplifier_module_pb2 as pb2
        from amplifier_core._grpc_gen import amplifier_module_pb2_grpc as pb2_grpc

        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = _write_test_module(tmpdir)
            manifest = _make_manifest(module_path)
            proc = _spawn_adapter(manifest)
            try:
                line = _read_first_line(proc)
                port = int(line.split(":", 1)[1])

                channel = grpc.insecure_channel(f"127.0.0.1:{port}")
                try:
                    stub = pb2_grpc.ToolServiceStub(channel)
                    response = stub.Execute(
                        pb2.ToolExecuteRequest(  # type: ignore[attr-defined]
                            input=json.dumps("hello").encode("utf-8"),
                            content_type="text/plain",
                        )
                    )
                    assert response.success is True, (
                        f"Expected success=True, got {response.success}"
                    )
                    assert b"echo: hello" in response.output, (
                        f"Expected b'echo: hello' in output, got {response.output!r}"
                    )
                finally:
                    channel.close()
            finally:
                proc.terminate()
                proc.wait(timeout=5)

    def test_grpc_client_health_check(self) -> None:
        """gRPC ``HealthCheck`` returns ``HEALTH_STATUS_SERVING``."""
        import grpc
        from amplifier_core._grpc_gen import amplifier_module_pb2 as pb2
        from amplifier_core._grpc_gen import amplifier_module_pb2_grpc as pb2_grpc

        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = _write_test_module(tmpdir)
            manifest = _make_manifest(module_path)
            proc = _spawn_adapter(manifest)
            try:
                line = _read_first_line(proc)
                port = int(line.split(":", 1)[1])

                channel = grpc.insecure_channel(f"127.0.0.1:{port}")
                try:
                    lifecycle_stub = pb2_grpc.ModuleLifecycleStub(channel)
                    response = lifecycle_stub.HealthCheck(pb2.Empty())  # type: ignore[attr-defined]
                    assert response.status == pb2.HEALTH_STATUS_SERVING, (  # type: ignore[attr-defined]
                        f"Expected HEALTH_STATUS_SERVING ({pb2.HEALTH_STATUS_SERVING}), "  # type: ignore[attr-defined]
                        f"got {response.status}"
                    )
                finally:
                    channel.close()
            finally:
                proc.terminate()
                proc.wait(timeout=5)

    def test_sigterm_shutdown_within_5_seconds(self) -> None:
        """SIGTERM causes the adapter to exit within 5 seconds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = _write_test_module(tmpdir)
            manifest = _make_manifest(module_path)
            proc = _spawn_adapter(manifest)
            try:
                # Wait for the server to be ready before sending SIGTERM
                _read_first_line(proc)

                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    pytest.fail(
                        "Adapter did not shut down within 5 seconds after SIGTERM"
                    )
            except Exception:
                proc.terminate()
                proc.wait(timeout=5)
                raise


# ---------------------------------------------------------------------------
# Failure-path tests
# ---------------------------------------------------------------------------


class TestAdapterFailurePaths:
    """Four integration tests covering startup error paths."""

    def _run_cli(
        self,
        stdin_data: bytes,
        extra_args: list[str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run the adapter CLI to completion and return the result."""
        env = os.environ.copy()
        env["AMPLIFIER_AUTH_TOKEN"] = "test-token"
        env["AMPLIFIER_KERNEL_ENDPOINT"] = "localhost:0"
        cmd = [sys.executable, "-m", "amplifier_foundation.grpc_adapter"] + (
            extra_args or []
        )
        return subprocess.run(
            cmd,
            input=stdin_data,
            capture_output=True,
            timeout=10,
            env=env,
        )

    def test_malformed_json_prints_error(self) -> None:
        """Invalid JSON manifest produces ``ERROR:...`` on stdout and non-zero exit."""
        result = self._run_cli(b"not valid json at all {{{{")
        stdout = result.stdout.decode()
        assert "ERROR:" in stdout, (
            f"Expected 'ERROR:' in stdout, got: {stdout!r}\n"
            f"stderr: {result.stderr.decode()!r}"
        )
        assert result.returncode != 0, (
            f"Expected non-zero exit code, got {result.returncode}"
        )

    def test_missing_module_field_prints_error(self) -> None:
        """Manifest missing ``'module'`` produces ``ERROR`` with ``'module'`` in message."""
        manifest = json.dumps({"type": "tool", "path": "/tmp"}).encode()
        result = self._run_cli(manifest)
        stdout = result.stdout.decode()
        assert "ERROR:" in stdout, f"Expected 'ERROR:' in stdout, got: {stdout!r}"
        assert "module" in stdout, (
            f"Expected 'module' in error message, got: {stdout!r}"
        )
        assert result.returncode != 0, (
            f"Expected non-zero exit code, got {result.returncode}"
        )

    def test_missing_type_field_prints_error(self) -> None:
        """Manifest missing ``'type'`` produces ``ERROR`` with ``'type'`` in message."""
        manifest = json.dumps({"module": "test-tool", "path": "/tmp"}).encode()
        result = self._run_cli(manifest)
        stdout = result.stdout.decode()
        assert "ERROR:" in stdout, f"Expected 'ERROR:' in stdout, got: {stdout!r}"
        assert "type" in stdout, f"Expected 'type' in error message, got: {stdout!r}"
        assert result.returncode != 0, (
            f"Expected non-zero exit code, got {result.returncode}"
        )

    def test_nonexistent_module_path_prints_error(self) -> None:
        """Nonexistent ``path`` produces ``ERROR`` on stdout and non-zero exit."""
        manifest = json.dumps(
            {
                "module": "test-tool",
                "type": "tool",
                "path": "/nonexistent/path/that/definitely/does/not/exist",
            }
        ).encode()
        result = self._run_cli(manifest)
        stdout = result.stdout.decode()
        assert "ERROR:" in stdout, (
            f"Expected 'ERROR:' in stdout, got: {stdout!r}\n"
            f"stderr: {result.stderr.decode()!r}"
        )
        assert result.returncode != 0, (
            f"Expected non-zero exit code, got {result.returncode}"
        )
