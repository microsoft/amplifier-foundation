"""Tests for amplifier_foundation.grpc_adapter.services module.

Verifies the ToolServiceAdapter gRPC service implementation.

These tests will fail with ModuleNotFoundError/ImportError until
amplifier_foundation.grpc_adapter.services is implemented.
"""

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

try:
    from amplifier_core._grpc_gen import amplifier_module_pb2 as pb2
except ImportError:  # grpcio / protobuf not installed in this env
    pb2 = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Mock fixtures for ToolServiceAdapter tests
# ---------------------------------------------------------------------------


class MockTool:
    """Mock tool with 'query' parameter for testing ToolServiceAdapter."""

    @property
    def name(self) -> str:
        return "mock-tool"

    @property
    def description(self) -> str:
        return "A mock tool for testing"

    @property
    def parameters_json(self) -> str:
        return json.dumps(
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The query to execute"},
                },
            }
        )

    async def execute(self, input: dict[str, Any]) -> Any:
        return MagicMock(success=True, output=b"mock output", error=None)


class MockFailingTool:
    """Tool that raises RuntimeError on execute.

    Used to test error-handling paths in the gRPC adapter.
    """

    @property
    def name(self) -> str:
        return "failing_tool"

    @property
    def description(self) -> str:
        return "A mock tool that always fails"

    @property
    def parameters_json(self) -> str:
        return json.dumps({"type": "object", "properties": {}})

    async def execute(self, input: dict[str, Any]) -> Any:
        raise RuntimeError("Tool execution failed")


class MockSyncTool:
    """Tool with a synchronous (non-async) execute method.

    Used to verify that the gRPC adapter handles sync tools correctly
    without blocking the event loop.
    """

    @property
    def name(self) -> str:
        return "sync_tool"

    @property
    def description(self) -> str:
        return "A mock tool with a synchronous execute method"

    @property
    def parameters_json(self) -> str:
        return json.dumps({"type": "object", "properties": {}})

    def execute(self, input: dict[str, Any]) -> Any:  # intentionally synchronous
        return MagicMock(success=True, output=b"sync output", error=None)


class MockContext:
    """Mock gRPC servicer context.

    Provides the minimal interface used by gRPC servicer methods:
    set_code(), set_details(), and async abort().
    """

    def __init__(self) -> None:
        self.code: Any = None
        self.details: str = ""
        self._aborted: bool = False

    def set_code(self, code: Any) -> None:
        self.code = code

    def set_details(self, details: str) -> None:
        self.details = details

    async def abort(self, code: Any, details: str) -> None:
        self.code = code
        self.details = details
        self._aborted = True


# ---------------------------------------------------------------------------
# TestToolServiceAdapter
# ---------------------------------------------------------------------------


class TestToolServiceAdapter:
    """Tests for ToolServiceAdapter gRPC service.

    All tests fail with ModuleNotFoundError until
    amplifier_foundation.grpc_adapter.services is implemented.
    """

    def _make_adapter(self, tool: Any = None) -> Any:
        """Create a ToolServiceAdapter wrapping *tool*.

        Imports ToolServiceAdapter from amplifier_foundation.grpc_adapter.services.
        Raises ModuleNotFoundError / ImportError until the module is implemented.
        """
        from amplifier_foundation.grpc_adapter.services import (  # type: ignore[import-not-found]  # noqa: PLC0415
            ToolServiceAdapter,
        )

        if tool is None:
            tool = MockTool()
        return ToolServiceAdapter(tool)

    @pytest.mark.asyncio
    async def test_get_spec_returns_valid_response(self) -> None:
        """GetSpec returns a ToolSpec with name, description, and parameters_json."""
        adapter = self._make_adapter(MockTool())
        context = MockContext()

        response = await adapter.GetSpec(MagicMock(), context)

        assert response.name == "mock-tool"
        assert response.description == "A mock tool for testing"
        assert "query" in response.parameters_json

    @pytest.mark.asyncio
    async def test_execute_returns_result_with_success(self) -> None:
        """Execute with valid JSON input returns success=True and expected bytes output."""
        adapter = self._make_adapter(MockTool())
        context = MockContext()
        request = pb2.ToolExecuteRequest(  # type: ignore[union-attr]
            input=json.dumps({"query": "test"}).encode("utf-8"),
            content_type="application/json",
        )

        response = await adapter.Execute(request, context)

        assert response.success is True
        assert b"mock output" in response.output

    @pytest.mark.asyncio
    async def test_execute_with_failing_tool_returns_error(self) -> None:
        """Execute with a tool that raises returns success=False with error message."""
        adapter = self._make_adapter(MockFailingTool())
        context = MockContext()
        request = pb2.ToolExecuteRequest(  # type: ignore[union-attr]
            input=json.dumps({}).encode("utf-8"),
            content_type="application/json",
        )

        response = await adapter.Execute(request, context)

        assert response.success is False
        assert "Tool execution failed" in response.error

    @pytest.mark.asyncio
    async def test_execute_with_sync_tool_uses_executor(self) -> None:
        """Execute with a synchronous tool succeeds without blocking the event loop."""
        adapter = self._make_adapter(MockSyncTool())
        context = MockContext()
        request = pb2.ToolExecuteRequest(  # type: ignore[union-attr]
            input=json.dumps({}).encode("utf-8"),
            content_type="application/json",
        )

        response = await adapter.Execute(request, context)

        assert response.success is True
