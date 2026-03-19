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

pytest.importorskip("grpc", reason="grpcio not installed")

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


def mount(coordinator: Any, config: dict) -> None:
    """No-op mount; real initialization via Mount() RPC (coordinator is None in v1)."""
    pass
'''

# ---------------------------------------------------------------------------
# Mock provider module source (inline)
# Written verbatim to tmpdir/test-provider/test_provider/__init__.py
# ---------------------------------------------------------------------------

MOCK_PROVIDER_MODULE = '''\
"""Minimal mock provider module for integration testing."""
from __future__ import annotations

from typing import Any


class _ProviderInfo:
    id = 'test-provider'
    display_name = 'Test Provider'
    credential_env_vars: list = []
    capabilities = ['chat']
    defaults: dict = {}


class _ModelInfo:
    id = 'test-model-1'
    display_name = 'Test Model 1'
    context_window = 4096
    max_output_tokens = 1024
    capabilities = ['chat']
    defaults: dict = {}


class _Usage:
    prompt_tokens = 10
    completion_tokens = 5
    total_tokens = 15
    reasoning_tokens = 0
    cache_read_tokens = 0
    cache_creation_tokens = 0


class _ChatResponse:
    content = 'Hello from test provider'
    tool_calls: list = []
    finish_reason = 'stop'
    metadata: dict = {}

    def __init__(self) -> None:
        self.usage = _Usage()


class _ToolCall:
    def __init__(self, id: str, name: str, arguments: Any) -> None:
        self.id = id
        self.name = name
        self.arguments = arguments


class TestProvider:
    """Test provider that returns canned responses."""

    name = 'test-provider'

    def get_info(self) -> _ProviderInfo:
        return _ProviderInfo()

    async def list_models(self) -> list:
        return [_ModelInfo()]

    async def complete(self, request: Any, **kwargs: Any) -> _ChatResponse:
        return _ChatResponse()

    def parse_tool_calls(self, response: Any) -> list:
        return []


_instance = TestProvider()

# Module-level attributes so the module itself satisfies the Provider protocol
name = _instance.name
get_info = _instance.get_info
list_models = _instance.list_models
complete = _instance.complete
parse_tool_calls = _instance.parse_tool_calls


def mount(coordinator: Any, config: dict) -> None:
    """No-op mount; real initialization via Mount() RPC (coordinator is None in v1)."""
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


def _write_provider_module(tmpdir: str) -> Path:
    """Create the mock provider module directory structure under *tmpdir*.

    Creates::

        {tmpdir}/test-provider/
            test_provider/
                __init__.py   ← MOCK_PROVIDER_MODULE

    The ``test-provider`` directory is the **module_path** consumed by
    ``_load_module_object``:  it is added to ``sys.path`` and the package
    ``test_provider`` (hyphen → underscore) is imported from inside it.

    Returns:
        ``Path(tmpdir) / "test-provider"`` — the value to pass to
        ``_make_provider_manifest``.
    """
    module_dir = Path(tmpdir) / "test-provider"
    module_dir.mkdir(exist_ok=True)
    pkg_dir = module_dir / "test_provider"
    pkg_dir.mkdir(exist_ok=True)
    (pkg_dir / "__init__.py").write_text(MOCK_PROVIDER_MODULE)
    return module_dir


def _make_provider_manifest(module_path: Path) -> str:
    """Return a JSON manifest string for the test-provider at *module_path*.

    Args:
        module_path: Path to the module directory (e.g. ``tmpdir/test-provider``).

    Returns:
        JSON string with ``module``, ``type``, and ``path`` fields.
    """
    return json.dumps(
        {
            "module": "test-provider",
            "type": "provider",
            "path": str(module_path),
        }
    )


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

    def test_provider_adapter_prints_ready(self) -> None:
        """Provider adapter prints ``READY:<port>`` with port > 0 on successful startup."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = _write_provider_module(tmpdir)
            manifest = _make_provider_manifest(module_path)
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

    def test_grpc_client_list_models(self) -> None:
        """gRPC ``ListModels`` returns one model with id='test-model-1'."""
        import grpc
        from amplifier_core._grpc_gen import amplifier_module_pb2 as pb2
        from amplifier_core._grpc_gen import amplifier_module_pb2_grpc as pb2_grpc

        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = _write_provider_module(tmpdir)
            manifest = _make_provider_manifest(module_path)
            proc = _spawn_adapter(manifest)
            try:
                line = _read_first_line(proc)
                port = int(line.split(":", 1)[1])

                channel = grpc.insecure_channel(f"127.0.0.1:{port}")
                try:
                    stub = pb2_grpc.ProviderServiceStub(channel)
                    response = stub.ListModels(pb2.Empty())  # type: ignore[attr-defined]
                    assert len(response.models) == 1, (
                        f"Expected 1 model, got {len(response.models)}"
                    )
                    assert response.models[0].id == "test-model-1", (
                        f"Expected id='test-model-1', got {response.models[0].id!r}"
                    )
                finally:
                    channel.close()
            finally:
                proc.terminate()
                proc.wait(timeout=5)

    def test_grpc_client_complete(self) -> None:
        """gRPC ``Complete`` returns content containing 'Hello from test provider' and finish_reason='stop'.

        In v2, the ``content`` field holds the legacy JSON representation of content blocks
        (e.g. ``[{"type":"text","text":"Hello from test provider"}]``).  We verify that the
        expected text is present in the content field and that finish_reason is 'stop'.
        """
        import grpc
        from amplifier_core._grpc_gen import amplifier_module_pb2 as pb2
        from amplifier_core._grpc_gen import amplifier_module_pb2_grpc as pb2_grpc

        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = _write_provider_module(tmpdir)
            manifest = _make_provider_manifest(module_path)
            proc = _spawn_adapter(manifest)
            try:
                line = _read_first_line(proc)
                port = int(line.split(":", 1)[1])

                channel = grpc.insecure_channel(f"127.0.0.1:{port}")
                try:
                    stub = pb2_grpc.ProviderServiceStub(channel)
                    response = stub.Complete(pb2.ChatRequest())  # type: ignore[attr-defined]
                    # v2: content field holds JSON-serialized content blocks; extract text for assertion
                    import json as _json

                    try:
                        blocks = _json.loads(response.content)
                        text_content = " ".join(
                            b.get("text", "")
                            for b in blocks
                            if isinstance(b, dict) and b.get("type") == "text"
                        )
                    except (_json.JSONDecodeError, TypeError):
                        text_content = response.content
                    assert text_content == "Hello from test provider", (
                        f"Expected text content 'Hello from test provider', "
                        f"got content={response.content!r} (extracted={text_content!r})"
                    )
                    assert response.finish_reason == "stop", (
                        f"Expected finish_reason='stop', got {response.finish_reason!r}"
                    )
                finally:
                    channel.close()
            finally:
                proc.terminate()
                proc.wait(timeout=5)

    def test_grpc_client_health_check_provider(self) -> None:
        """gRPC ``HealthCheck`` on provider adapter returns ``HEALTH_STATUS_SERVING``."""
        import grpc
        from amplifier_core._grpc_gen import amplifier_module_pb2 as pb2
        from amplifier_core._grpc_gen import amplifier_module_pb2_grpc as pb2_grpc

        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = _write_provider_module(tmpdir)
            manifest = _make_provider_manifest(module_path)
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


# ---------------------------------------------------------------------------
# Provider module scaffolding tests
# ---------------------------------------------------------------------------


class TestProviderModuleScaffolding:
    """Tests that validate the mock provider module infrastructure."""

    def test_mock_provider_module_constant_exists(self) -> None:
        """MOCK_PROVIDER_MODULE string constant is defined."""
        assert isinstance(MOCK_PROVIDER_MODULE, str)
        assert len(MOCK_PROVIDER_MODULE) > 0

    def test_mock_provider_module_contains_test_provider_class(self) -> None:
        """MOCK_PROVIDER_MODULE contains TestProvider class definition."""
        assert "TestProvider" in MOCK_PROVIDER_MODULE

    def test_mock_provider_module_contains_required_classes(self) -> None:
        """MOCK_PROVIDER_MODULE contains all required class definitions."""
        for class_name in [
            "_ProviderInfo",
            "_ModelInfo",
            "_Usage",
            "_ChatResponse",
            "_ToolCall",
            "TestProvider",
        ]:
            assert class_name in MOCK_PROVIDER_MODULE, (
                f"Expected {class_name!r} in MOCK_PROVIDER_MODULE"
            )

    def test_mock_provider_module_contains_mount_function(self) -> None:
        """MOCK_PROVIDER_MODULE contains a mount(coordinator, config) function."""
        assert "def mount(coordinator" in MOCK_PROVIDER_MODULE

    def test_write_provider_module_creates_directory_structure(
        self, tmp_path: Path
    ) -> None:
        """_write_provider_module creates {tmpdir}/test-provider/test_provider/__init__.py."""
        module_dir = _write_provider_module(str(tmp_path))
        assert module_dir.name == "test-provider"
        init_py = module_dir / "test_provider" / "__init__.py"
        assert init_py.exists(), f"Expected {init_py} to exist"

    def test_write_provider_module_writes_mock_content(self, tmp_path: Path) -> None:
        """_write_provider_module writes MOCK_PROVIDER_MODULE content to __init__.py."""
        module_dir = _write_provider_module(str(tmp_path))
        init_py = module_dir / "test_provider" / "__init__.py"
        content = init_py.read_text()
        assert "TestProvider" in content

    def test_write_provider_module_returns_test_provider_path(
        self, tmp_path: Path
    ) -> None:
        """_write_provider_module returns Path pointing to test-provider directory."""
        module_dir = _write_provider_module(str(tmp_path))
        assert isinstance(module_dir, Path)
        assert module_dir == tmp_path / "test-provider"

    def test_make_provider_manifest_returns_valid_json(self, tmp_path: Path) -> None:
        """_make_provider_manifest returns a valid JSON string."""
        manifest_str = _make_provider_manifest(tmp_path)
        parsed = json.loads(manifest_str)
        assert parsed["module"] == "test-provider"
        assert parsed["type"] == "provider"
        assert parsed["path"] == str(tmp_path)


# ---------------------------------------------------------------------------
# Streaming integration tests
# ---------------------------------------------------------------------------


class TestProviderStreamingIntegration:
    """Integration test for CompleteStreaming RPC on ProviderService."""

    def test_complete_streaming_returns_response(self) -> None:
        """gRPC ``CompleteStreaming`` returns at least one response with a non-empty finish_reason."""
        import grpc
        from amplifier_core._grpc_gen import amplifier_module_pb2 as pb2
        from amplifier_core._grpc_gen import amplifier_module_pb2_grpc as pb2_grpc

        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = _write_provider_module(tmpdir)
            manifest = _make_provider_manifest(module_path)
            proc = _spawn_adapter(manifest)
            try:
                line = _read_first_line(proc)
                port = int(line.split(":", 1)[1])

                channel = grpc.insecure_channel(f"127.0.0.1:{port}")
                try:
                    stub = pb2_grpc.ProviderServiceStub(channel)
                    responses = list(
                        stub.CompleteStreaming(
                            pb2.ChatRequest(  # type: ignore[attr-defined]
                                messages=[
                                    pb2.Message(  # type: ignore[attr-defined]
                                        role=pb2.ROLE_USER,  # type: ignore[attr-defined]
                                        text_content="Hello!",
                                    )
                                ]
                            )
                        )
                    )
                    assert len(responses) >= 1, (
                        f"Expected at least 1 response, got {len(responses)}"
                    )
                    assert responses[0].finish_reason != "", (
                        f"Expected non-empty finish_reason, "
                        f"got {responses[0].finish_reason!r}"
                    )
                finally:
                    channel.close()
            finally:
                proc.terminate()
                proc.wait(timeout=5)
