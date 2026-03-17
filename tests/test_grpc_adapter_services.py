"""Tests for amplifier_foundation.grpc_adapter.services module.

Verifies the ToolServiceAdapter and ProviderServiceAdapter gRPC service implementations.

These tests will fail with ModuleNotFoundError/ImportError until
amplifier_foundation.grpc_adapter.services is implemented.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

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


# ---------------------------------------------------------------------------
# MockProvider — minimal Provider for ProviderServiceAdapter tests
# ---------------------------------------------------------------------------


class MockProvider:
    """Minimal Provider satisfying amplifier_core.interfaces.Provider protocol."""

    def __init__(self) -> None:
        self.list_models: Any = AsyncMock(return_value=[])
        self.complete: Any = AsyncMock(
            return_value=MagicMock(
                content="mock response",
                tool_calls=[],
                usage=MagicMock(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                ),
                finish_reason="stop",
                metadata={},
            )
        )

    @property
    def name(self) -> str:
        return "mock_provider"

    def get_info(self) -> Any:
        return MagicMock(
            id="mock_provider",
            display_name="Mock Provider",
            credential_env_vars=[],
            capabilities=["chat"],
            defaults={},
            config_fields=[],
        )

    def parse_tool_calls(self, response: Any) -> list[Any]:
        return []


# ---------------------------------------------------------------------------
# TestProviderServiceAdapter
# ---------------------------------------------------------------------------


class TestProviderServiceAdapter:
    """Tests for ProviderServiceAdapter — adapts a Python Provider as gRPC ProviderService."""

    def _make_adapter(self, provider: Any = None) -> Any:
        """Create a ProviderServiceAdapter wrapping *provider*.

        Imports ProviderServiceAdapter from amplifier_foundation.grpc_adapter.services.
        Raises ImportError until the implementation is written.
        """
        from amplifier_foundation.grpc_adapter.services import (  # type: ignore[import-not-found]  # noqa: PLC0415
            ProviderServiceAdapter,
        )

        if provider is None:
            provider = MockProvider()
        return ProviderServiceAdapter(provider)

    # ------------------------------------------------------------------
    # 1. GetInfo
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_info_returns_valid_response(self) -> None:
        """GetInfo returns ProviderInfo proto with id, display_name, and credential_env_vars."""
        provider = MockProvider()
        provider.get_info = MagicMock(
            return_value=MagicMock(
                id="mock-provider",
                display_name="Mock Provider",
                credential_env_vars=["MOCK_API_KEY"],
                capabilities=["chat"],
                defaults={},
                config_fields=[],
            )
        )
        adapter = self._make_adapter(provider)
        ctx = MockContext()
        result = await adapter.GetInfo(pb2.Empty(), ctx)  # type: ignore[union-attr]
        assert result.id == "mock-provider"
        assert result.display_name == "Mock Provider"
        assert "MOCK_API_KEY" in result.credential_env_vars

    # ------------------------------------------------------------------
    # 2. ListModels — empty list
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_list_models_empty_returns_valid_response(self) -> None:
        """ListModels with no models from provider returns response with len(result.models)==0."""
        provider = MockProvider()
        provider.list_models = AsyncMock(return_value=[])
        adapter = self._make_adapter(provider)
        ctx = MockContext()
        result = await adapter.ListModels(pb2.Empty(), ctx)  # type: ignore[union-attr]
        assert len(result.models) == 0

    # ------------------------------------------------------------------
    # 3. ListModels — with models
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_list_models_returns_models(self) -> None:
        """ListModels returns proto ModelInfo entries matching the provider's model list."""
        provider = MockProvider()
        mock_model = MagicMock(
            id="gpt-4",
            display_name="GPT-4",
            context_window=128000,
            max_output_tokens=4096,
            capabilities=["chat", "tools"],
            defaults={},
        )
        provider.list_models = AsyncMock(return_value=[mock_model])
        adapter = self._make_adapter(provider)
        ctx = MockContext()
        result = await adapter.ListModels(pb2.Empty(), ctx)  # type: ignore[union-attr]
        assert len(result.models) == 1
        assert result.models[0].id == "gpt-4"

    # ------------------------------------------------------------------
    # 4. Complete — basic response
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_complete_returns_chat_response(self) -> None:
        """Complete returns ChatResponse with expected content and finish_reason."""
        provider = MockProvider()
        provider.complete = AsyncMock(
            return_value=MagicMock(
                content="Hello from mock",
                tool_calls=[],
                usage=MagicMock(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                ),
                finish_reason="stop",
                metadata={},
            )
        )
        adapter = self._make_adapter(provider)
        ctx = MockContext()
        result = await adapter.Complete(pb2.ChatRequest(), ctx)  # type: ignore[union-attr]
        assert result.content == "Hello from mock"
        assert result.finish_reason == "stop"

    # ------------------------------------------------------------------
    # 5. Complete — thinking block signature preserved
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_complete_preserves_thinking_block_signature(self) -> None:
        """Complete preserves thinking_signature from response metadata in metadata_json."""
        provider = MockProvider()
        provider.complete = AsyncMock(
            return_value=MagicMock(
                content="",
                tool_calls=[],
                usage=MagicMock(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                ),
                finish_reason="stop",
                metadata={"thinking_signature": "sig_abc123"},
            )
        )
        adapter = self._make_adapter(provider)
        ctx = MockContext()
        result = await adapter.Complete(pb2.ChatRequest(), ctx)  # type: ignore[union-attr]
        assert "thinking_signature" in result.metadata_json
        assert "sig_abc123" in result.metadata_json

    # ------------------------------------------------------------------
    # 6. ParseToolCalls — multiple tool calls
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_parse_tool_calls_multiple(self) -> None:
        """ParseToolCalls returns both tool calls when the provider returns two."""
        provider = MockProvider()
        # Use direct attribute assignment — MagicMock(name=...) sets repr, not .name attr
        tool_call_1 = MagicMock(id="tc1", arguments={})
        tool_call_1.name = "tool_one"
        tool_call_2 = MagicMock(id="tc2", arguments={})
        tool_call_2.name = "tool_two"
        provider.parse_tool_calls = MagicMock(return_value=[tool_call_1, tool_call_2])
        adapter = self._make_adapter(provider)
        ctx = MockContext()
        result = await adapter.ParseToolCalls(pb2.ChatResponse(), ctx)  # type: ignore[union-attr]
        assert len(result.tool_calls) == 2
        assert result.tool_calls[0].name == "tool_one"
        assert result.tool_calls[1].name == "tool_two"

    # ------------------------------------------------------------------
    # 7. ParseToolCalls — empty (default MockProvider)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_parse_tool_calls_empty(self) -> None:
        """ParseToolCalls with default MockProvider (returns []) yields 0 tool_calls."""
        adapter = self._make_adapter()
        ctx = MockContext()
        result = await adapter.ParseToolCalls(pb2.ChatResponse(), ctx)  # type: ignore[union-attr]
        assert len(result.tool_calls) == 0
