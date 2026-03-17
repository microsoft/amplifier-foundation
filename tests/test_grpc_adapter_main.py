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

    def test_unsupported_type_raises_type_error(self) -> None:
        """_verify_module_type() raises TypeError for unsupported module types."""
        from amplifier_foundation.grpc_adapter.__main__ import _verify_module_type  # type: ignore[import-not-found]

        obj = MagicMock()
        with pytest.raises(TypeError):
            _verify_module_type(obj, "hook")  # unsupported in v1

    def test_protocol_violation_raises_type_error(self) -> None:
        """_verify_module_type() raises TypeError when object doesn't match declared type."""
        from amplifier_foundation.grpc_adapter.__main__ import _verify_module_type  # type: ignore[import-not-found]

        # Object missing required Tool attributes
        bad_obj = object()
        with pytest.raises(TypeError):
            _verify_module_type(bad_obj, "tool")


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
