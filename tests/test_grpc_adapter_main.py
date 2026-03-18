"""Tests for amplifier_foundation.grpc_adapter.__main__ CLI entry point.

Covers:
- _parse_args: --port default and explicit
- _read_manifest: valid JSON, empty stdin raises ValueError
- _verify_module_type: Tool/Provider pass, unknown type or wrong type raises TypeError
- CLI subprocess acceptance: empty manifest, missing path
"""

from __future__ import annotations

import json
import subprocess
import sys
from io import StringIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PYTHON = sys.executable
MODULE = "amplifier_foundation.grpc_adapter"


def _run_cli(
    stdin_data: str, extra_args: list[str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Invoke the grpc_adapter CLI with the given stdin and return the result."""
    cmd = [PYTHON, "-m", MODULE] + (extra_args or [])
    return subprocess.run(
        cmd,
        input=stdin_data,
        capture_output=True,
        text=True,
        timeout=10,
    )


# ---------------------------------------------------------------------------
# Tests: _parse_args
# ---------------------------------------------------------------------------


class TestParseArgs:
    """Tests for _parse_args() argument parsing."""

    def test_default_port_is_zero(self) -> None:
        """_parse_args() with no arguments returns port=0 (OS-assigned)."""
        from amplifier_foundation.grpc_adapter.__main__ import _parse_args  # type: ignore[import-not-found]

        args = _parse_args([])
        assert args.port == 0

    def test_explicit_port_parsed(self) -> None:
        """_parse_args() with --port 8080 returns port=8080."""
        from amplifier_foundation.grpc_adapter.__main__ import _parse_args  # type: ignore[import-not-found]

        args = _parse_args(["--port", "8080"])
        assert args.port == 8080

    def test_port_is_int(self) -> None:
        """_parse_args() port attribute is an int, not a string."""
        from amplifier_foundation.grpc_adapter.__main__ import _parse_args  # type: ignore[import-not-found]

        args = _parse_args(["--port", "50051"])
        assert isinstance(args.port, int)


# ---------------------------------------------------------------------------
# Tests: _read_manifest
# ---------------------------------------------------------------------------


class TestReadManifest:
    """Tests for _read_manifest() stdin parsing."""

    def test_valid_json_returns_dict(self) -> None:
        """_read_manifest() returns dict for valid JSON on stdin."""
        from amplifier_foundation.grpc_adapter.__main__ import _read_manifest  # type: ignore[import-not-found]

        manifest_str = json.dumps({"module": "test-tool", "type": "tool"})
        with patch("sys.stdin", StringIO(manifest_str)):
            result = _read_manifest()
        assert result == {"module": "test-tool", "type": "tool"}

    def test_empty_stdin_raises_value_error(self) -> None:
        """_read_manifest() raises ValueError when stdin is empty."""
        from amplifier_foundation.grpc_adapter.__main__ import _read_manifest  # type: ignore[import-not-found]

        with patch("sys.stdin", StringIO("")):
            with pytest.raises(ValueError):
                _read_manifest()

    def test_empty_json_object_returns_empty_dict(self) -> None:
        """_read_manifest() returns empty dict for '{}' on stdin (not empty)."""
        from amplifier_foundation.grpc_adapter.__main__ import _read_manifest  # type: ignore[import-not-found]

        with patch("sys.stdin", StringIO("{}")):
            result = _read_manifest()
        assert result == {}


# ---------------------------------------------------------------------------
# Tests: _verify_module_type
# ---------------------------------------------------------------------------


class TestVerifyModuleType:
    """Tests for _verify_module_type() protocol compliance checks."""

    def _make_tool(self) -> Any:
        """Create a minimal object satisfying the Tool protocol."""
        obj = MagicMock()
        obj.name = "test-tool"
        obj.description = "A test tool"
        obj.parameters_json = "{}"
        obj.execute = MagicMock()
        return obj

    def _make_provider(self) -> Any:
        """Create a minimal object satisfying the Provider protocol."""
        obj = MagicMock()
        obj.name = "test-provider"
        obj.get_info = MagicMock()
        obj.list_models = MagicMock()
        obj.complete = MagicMock()
        obj.parse_tool_calls = MagicMock()
        return obj

    def test_tool_type_passes(self) -> None:
        """_verify_module_type() does not raise for a valid Tool object."""
        from amplifier_foundation.grpc_adapter.__main__ import _verify_module_type  # type: ignore[import-not-found]

        tool = self._make_tool()
        # Should not raise
        _verify_module_type(tool, "tool")

    def test_provider_type_passes(self) -> None:
        """_verify_module_type() does not raise for a valid Provider object."""
        from amplifier_foundation.grpc_adapter.__main__ import _verify_module_type  # type: ignore[import-not-found]

        provider = self._make_provider()
        # Should not raise
        _verify_module_type(provider, "provider")

    def test_missing_mount_raises_type_error_regardless_of_declared_type(self) -> None:
        """_verify_module_type() raises TypeError when mount is missing, for any declared_type.

        The check is purely structural: does the module have a callable mount()?
        Type-specific validation is deferred to the Mount() RPC call.
        """
        import types

        from amplifier_foundation.grpc_adapter.__main__ import _verify_module_type  # type: ignore[import-not-found]

        module_obj = types.SimpleNamespace()  # no mount attribute
        with pytest.raises(TypeError, match="callable 'mount' entry point"):
            _verify_module_type(
                module_obj, "hook"
            )  # fails on missing mount, not on type

    def test_protocol_violation_raises_type_error(self) -> None:
        """_verify_module_type() raises TypeError when object doesn't match declared type."""
        from amplifier_foundation.grpc_adapter.__main__ import _verify_module_type  # type: ignore[import-not-found]

        # Object missing required Tool attributes
        bad_obj = object()
        with pytest.raises(TypeError):
            _verify_module_type(bad_obj, "tool")

    # ------------------------------------------------------------------
    # New structural-conformance tests (mount callable check, not isinstance)
    # ------------------------------------------------------------------

    def test_module_with_mount_passes_for_tool(self) -> None:
        """_verify_module_type() passes when module has callable mount for tool type.

        A Python Amplifier module is a module OBJECT, not a Tool instance.
        The correct check is structural: does the module expose a callable mount()?
        """
        import types

        from amplifier_foundation.grpc_adapter.__main__ import _verify_module_type  # type: ignore[import-not-found]

        module_obj = types.SimpleNamespace()
        module_obj.mount = lambda coordinator, config: None  # type: ignore[attr-defined]
        # Should not raise — has callable mount
        _verify_module_type(module_obj, "tool")

    def test_module_with_mount_passes_for_provider(self) -> None:
        """_verify_module_type() passes when module has callable mount for provider type."""
        import types

        from amplifier_foundation.grpc_adapter.__main__ import _verify_module_type  # type: ignore[import-not-found]

        module_obj = types.SimpleNamespace()
        module_obj.mount = lambda coordinator, config: None  # type: ignore[attr-defined]
        # Should not raise — has callable mount
        _verify_module_type(module_obj, "provider")

    def test_module_without_mount_raises_type_error_with_mount_message(self) -> None:
        """_verify_module_type() raises TypeError with 'mount entry point' message when no mount.

        The error message must guide the developer to the real problem:
        the module lacks the required mount(coordinator, config) entry point.
        """
        import types

        from amplifier_foundation.grpc_adapter.__main__ import _verify_module_type  # type: ignore[import-not-found]

        module_obj = types.SimpleNamespace()  # no mount attribute
        with pytest.raises(TypeError, match="callable 'mount' entry point"):
            _verify_module_type(module_obj, "tool")

    def test_module_with_noncallable_mount_raises_type_error(self) -> None:
        """_verify_module_type() raises TypeError when mount attribute is not callable."""
        import types

        from amplifier_foundation.grpc_adapter.__main__ import _verify_module_type  # type: ignore[import-not-found]

        module_obj = types.SimpleNamespace()
        module_obj.mount = "not_a_function"  # type: ignore[attr-defined]  # exists but not callable
        with pytest.raises(TypeError, match="callable 'mount' entry point"):
            _verify_module_type(module_obj, "provider")

    def test_non_isinstance_object_with_mount_passes(self) -> None:
        """Old isinstance check is NOT used — non-Provider/Tool object with mount still passes.

        The key bug: importlib.import_module() returns a MODULE object, never a
        Provider or Tool instance. isinstance(module_obj, Provider) always fails.
        The fix: check structural conformance (callable mount) instead.
        """
        import types

        from amplifier_core.interfaces import Provider, Tool
        from amplifier_foundation.grpc_adapter.__main__ import _verify_module_type  # type: ignore[import-not-found]

        module_obj = types.SimpleNamespace()
        module_obj.mount = lambda coordinator, config: None  # type: ignore[attr-defined]

        # Explicitly verify this object is NOT a Tool or Provider instance
        assert not isinstance(module_obj, Tool), (
            "Test setup: SimpleNamespace must not satisfy Tool protocol"
        )
        assert not isinstance(module_obj, Provider), (
            "Test setup: SimpleNamespace must not satisfy Provider protocol"
        )

        # Should NOT raise — structural conformance (callable mount) is the v1 check
        _verify_module_type(module_obj, "tool")
        _verify_module_type(module_obj, "provider")

    def test_declared_type_validation_deferred_to_mount_rpc(self) -> None:
        """_verify_module_type() defers type validation to Mount() RPC — only checks mount callable.

        v1 trust model: declared_type is trusted from the manifest.
        Instance type verification happens when Mount() RPC is called, not at adapter startup.
        A module with callable mount must NOT be rejected here regardless of declared_type.
        """
        import types

        from amplifier_foundation.grpc_adapter.__main__ import _verify_module_type  # type: ignore[import-not-found]

        module_obj = types.SimpleNamespace()
        module_obj.mount = lambda coordinator, config: None  # type: ignore[attr-defined]
        # Even with an unrecognized declared_type, should not raise if mount exists
        # (type validation is deferred to the Mount() RPC call)
        _verify_module_type(module_obj, "hook")  # no TypeError — deferred check


# ---------------------------------------------------------------------------
# Acceptance criteria: CLI subprocess tests
# ---------------------------------------------------------------------------


class TestCliAcceptanceCriteria:
    """Acceptance criteria: subprocess CLI invocation tests."""

    def test_empty_manifest_exits_nonzero(self) -> None:
        """With empty manifest '{}', exits non-zero."""
        result = _run_cli("{}")
        assert result.returncode != 0, (
            f"Expected non-zero exit code, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_empty_manifest_prints_missing_module_error(self) -> None:
        """With empty manifest '{}', prints ERROR:Missing required field 'module' in manifest."""
        result = _run_cli("{}")
        assert "ERROR:Missing required field 'module' in manifest" in result.stdout, (
            f"Expected error message in stdout, got:\n{result.stdout}\nstderr: {result.stderr}"
        )

    def test_missing_module_path_exits_nonzero(self) -> None:
        """With valid manifest but nonexistent path, exits non-zero."""
        manifest = json.dumps(
            {
                "module": "test-tool",
                "type": "tool",
                "path": "/nonexistent",
            }
        )
        result = _run_cli(manifest)
        assert result.returncode != 0, (
            f"Expected non-zero exit code, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_missing_module_path_prints_error(self) -> None:
        """With valid manifest but nonexistent path, prints ERROR:Module path does not exist."""
        manifest = json.dumps(
            {
                "module": "test-tool",
                "type": "tool",
                "path": "/nonexistent",
            }
        )
        result = _run_cli(manifest)
        assert "ERROR:Module path does not exist: /nonexistent" in result.stdout, (
            f"Expected path error in stdout, got:\n{result.stdout}\nstderr: {result.stderr}"
        )

    def test_missing_type_exits_nonzero(self) -> None:
        """With manifest missing 'type', exits non-zero."""
        manifest = json.dumps({"module": "test-tool"})
        result = _run_cli(manifest)
        assert result.returncode != 0

    def test_missing_type_prints_error(self) -> None:
        """With manifest missing 'type', prints ERROR about missing field."""
        manifest = json.dumps({"module": "test-tool"})
        result = _run_cli(manifest)
        assert "ERROR:" in result.stdout

    def test_empty_stdin_exits_nonzero(self) -> None:
        """With empty stdin (no JSON), exits non-zero."""
        result = _run_cli("")
        assert result.returncode != 0

    def test_empty_stdin_prints_error(self) -> None:
        """With empty stdin, prints ERROR to stdout."""
        result = _run_cli("")
        assert "ERROR:" in result.stdout

    def test_module_invocable(self) -> None:
        """Module is invocable as 'python -m amplifier_foundation.grpc_adapter'."""
        # Even with bad input, it should run (not crash with ImportError or similar)
        result = _run_cli("{}")
        # Should not fail with ModuleNotFoundError
        assert "No module named" not in result.stderr, (
            f"Module not importable: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Tests: main() entry point
# ---------------------------------------------------------------------------


class TestMainEntryPoint:
    """Tests for main() synchronous entry point."""

    def test_main_is_callable(self) -> None:
        """main() function exists and is callable."""
        from amplifier_foundation.grpc_adapter.__main__ import main  # type: ignore[import-not-found]

        assert callable(main)


# ---------------------------------------------------------------------------
# Tests: async mount() handling (regression — was silently dropped)
# ---------------------------------------------------------------------------


class TestLoadModuleObjectAsyncMount:
    """Tests for _load_module_object() mount() handling.

    mount() is NOT called during module load — real initialization happens
    via the Mount() RPC with coordinator=None and config dict.
    """

    def test_load_module_object_is_async(self) -> None:
        """_load_module_object() is an async function (required for async-safe import)."""
        import inspect

        from amplifier_foundation.grpc_adapter.__main__ import _load_module_object  # type: ignore[import-not-found]

        assert inspect.iscoroutinefunction(_load_module_object), (
            "_load_module_object must be async for safe use in the async _run() flow"
        )

    @pytest.mark.asyncio
    async def test_mount_not_called_during_load(self) -> None:
        """_load_module_object() does NOT call mount() — initialization deferred to Mount() RPC.

        This replaces the old early-mount pattern that tried mount() with zero args and
        swallowed TypeError. The real mount happens via the Mount() RPC with
        coordinator=None and config dict (v1 limitation).
        """
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        from amplifier_foundation.grpc_adapter.__main__ import _load_module_object  # type: ignore[import-not-found]

        mount_called = False

        def recording_mount(coordinator: object, config: dict) -> None:
            nonlocal mount_called
            mount_called = True

        mock_module = MagicMock()
        mock_module.mount = recording_mount

        with patch("importlib.import_module", return_value=mock_module):
            await _load_module_object(Path("/fake/my-module"), "tool")

        assert not mount_called, (
            "_load_module_object() must NOT call mount() during load — "
            "initialization is deferred to the Mount() RPC"
        )


# ---------------------------------------------------------------------------
# Tests: asyncio.get_running_loop() used in async context (not deprecated get_event_loop)
# ---------------------------------------------------------------------------


class TestGetRunningLoopUsage:
    """Tests that _run() uses asyncio.get_running_loop() (not deprecated get_event_loop)."""

    def test_run_does_not_call_get_event_loop(self) -> None:
        """_run() source must use get_running_loop(), not deprecated get_event_loop()."""
        import inspect

        from amplifier_foundation.grpc_adapter import __main__ as m  # type: ignore[import-not-found]

        source = inspect.getsource(m._run)  # type: ignore[attr-defined]
        assert "get_event_loop" not in source, (
            "_run() calls asyncio.get_event_loop() which is deprecated in Python 3.10+ "
            "when called from within a running event loop; use asyncio.get_running_loop() instead"
        )


# ---------------------------------------------------------------------------
# Tests: _load_module_object security (sys.path and package name validation)
# ---------------------------------------------------------------------------


class TestLoadModuleObjectSecurity:
    """Tests for security properties of _load_module_object().

    These tests expose two security bugs:
    1. sys.path.insert(0, ...) allows attacker-controlled dirs to shadow stdlib modules.
    2. No validation of package names allows shell-injection-style names.
    """

    @pytest.mark.asyncio
    async def test_sys_path_append_not_insert(self) -> None:
        """_load_module_object() must use sys.path.append() not sys.path.insert(0, ...).

        sys.path.insert(0, path) places the module directory at the front of
        sys.path, which can shadow stdlib modules (e.g., a module named 'os'
        would shadow the real os module). sys.path.append() is safer because
        it adds the directory at the end, after stdlib paths.
        """
        import inspect

        from amplifier_foundation.grpc_adapter.__main__ import _load_module_object  # type: ignore[import-not-found]

        source = inspect.getsource(_load_module_object)
        assert "sys.path.append(" in source, (
            "_load_module_object() should use sys.path.append() to avoid shadowing stdlib modules"
        )
        assert "sys.path.insert(0," not in source, (
            "_load_module_object() uses sys.path.insert(0, ...) which allows attacker-controlled "
            "directories to shadow stdlib modules — use sys.path.append() instead"
        )

    @pytest.mark.asyncio
    async def test_malicious_package_name_raises_value_error(self) -> None:
        """_load_module_object() must reject package names containing shell metacharacters."""
        from pathlib import Path

        from amplifier_foundation.grpc_adapter.__main__ import _load_module_object  # type: ignore[import-not-found]

        malicious_path = Path("/tmp/my-module; rm -rf /")
        with pytest.raises(ValueError, match="Invalid package name"):
            await _load_module_object(malicious_path, "tool")

    @pytest.mark.asyncio
    async def test_valid_package_name_accepted(self) -> None:
        """_load_module_object() accepts valid package names and returns the module."""
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        from amplifier_foundation.grpc_adapter.__main__ import _load_module_object  # type: ignore[import-not-found]

        mock_module = MagicMock()
        mock_module.mount = None

        with patch("importlib.import_module", return_value=mock_module):
            result = await _load_module_object(Path("/tmp/my-valid-module"), "tool")

        assert result is mock_module

    @pytest.mark.asyncio
    async def test_dotted_package_name_rejected(self) -> None:
        """_load_module_object() must reject package names containing dots.

        A path like '/tmp/os.system' would derive package name 'os.system' after
        hyphen-to-underscore conversion, which could be used to import submodules
        of stdlib packages. Dotted names must be rejected.
        """
        from pathlib import Path

        from amplifier_foundation.grpc_adapter.__main__ import _load_module_object  # type: ignore[import-not-found]

        dotted_path = Path("/tmp/os.system")
        with pytest.raises(ValueError, match="Invalid package name"):
            await _load_module_object(dotted_path, "tool")
