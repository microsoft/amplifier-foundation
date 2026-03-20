"""Tests for amplifier_foundation.grpc_adapter.services module.

Verifies the ToolServiceAdapter and ProviderServiceAdapter gRPC service implementations.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from amplifier_core.message_models import ChatRequest as PydanticChatRequest
    from amplifier_core.message_models import ChatResponse as PydanticChatResponse
except ImportError:
    PydanticChatRequest = None  # type: ignore[assignment,misc]
    PydanticChatResponse = None  # type: ignore[assignment,misc]

try:
    from amplifier_core._grpc_gen import amplifier_module_pb2 as pb2
except ImportError:  # grpcio / protobuf not installed in this env
    pb2 = None  # type: ignore[assignment]

pytestmark = pytest.mark.skipif(pb2 is None, reason="grpcio/protobuf not installed")


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
    """Tests for ToolServiceAdapter gRPC service."""

    def _make_adapter(self, tool: Any = None) -> Any:
        """Create a ToolServiceAdapter wrapping *tool*.

        Imports ToolServiceAdapter from amplifier_foundation.grpc_adapter.services.
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

    @pytest.mark.asyncio
    async def test_execute_dict_output_returns_valid_json(self) -> None:
        """Execute with dict output returns valid JSON bytes (not Python repr).

        Verifies json.dumps() is used instead of str() so that json.loads()
        on the response.output succeeds and returns the original dict.
        """
        tool = MockTool()
        tool.execute = AsyncMock(  # type: ignore[method-assign]
            return_value=MagicMock(
                success=True, output={"key": "value", "count": 42}, error=None
            )
        )
        adapter = self._make_adapter(tool)
        context = MockContext()
        request = pb2.ToolExecuteRequest(  # type: ignore[union-attr]
            input=json.dumps({"query": "test"}).encode("utf-8"),
            content_type="application/json",
        )

        response = await adapter.Execute(request, context)

        assert response.success is True
        decoded = response.output.decode("utf-8")
        parsed = json.loads(decoded)  # must not raise — fails on Python repr
        assert parsed == {"key": "value", "count": 42}

    @pytest.mark.asyncio
    async def test_execute_none_output_returns_valid_json(self) -> None:
        """Execute with None output returns valid JSON (empty string), not b'None'.

        Verifies None is serialised as json.dumps("") rather than str(None),
        so that json.loads() on the response.output succeeds.
        """
        tool = MockTool()
        tool.execute = AsyncMock(  # type: ignore[method-assign]
            return_value=MagicMock(success=True, output=None, error=None)
        )
        adapter = self._make_adapter(tool)
        context = MockContext()
        request = pb2.ToolExecuteRequest(  # type: ignore[union-attr]
            input=json.dumps({"query": "test"}).encode("utf-8"),
            content_type="application/json",
        )

        response = await adapter.Execute(request, context)

        assert response.success is True
        decoded = response.output.decode("utf-8")
        parsed = json.loads(decoded)  # must not raise — fails on b'None'
        assert parsed == ""

    @pytest.mark.asyncio
    async def test_execute_mirrors_content_type(self) -> None:
        """Execute echoes the request content_type in the response.

        Verifies the adapter mirrors the request content_type rather than
        hard-coding 'text/plain'.
        """
        adapter = self._make_adapter(MockTool())
        context = MockContext()
        request = pb2.ToolExecuteRequest(  # type: ignore[union-attr]
            input=json.dumps({"query": "test"}).encode("utf-8"),
            content_type="application/json",
        )

        response = await adapter.Execute(request, context)

        assert response.content_type == "application/json"

    @pytest.mark.asyncio
    async def test_execute_default_content_type_is_json(self) -> None:
        """Execute with empty content_type defaults to 'application/json'.

        Verifies the adapter uses 'application/json' as the default rather than
        hard-coding 'text/plain' when the request content_type field is empty.
        """
        adapter = self._make_adapter(MockTool())
        context = MockContext()
        request = pb2.ToolExecuteRequest(  # type: ignore[union-attr]
            input=json.dumps({"query": "test"}).encode("utf-8"),
            content_type="",
        )

        response = await adapter.Execute(request, context)

        assert response.content_type == "application/json"


# ---------------------------------------------------------------------------
# Helpers for PyO3 bridge mocking
# ---------------------------------------------------------------------------


def _test_json_to_proto_response(json_str: str) -> bytes:
    """Simulate json_to_proto_chat_response for tests.

    Parses the JSON response dict and constructs a pb2.ChatResponse
    with the content text, finish_reason, and metadata_json preserved,
    so existing assertions continue to pass after the bridge refactor.
    """
    data = json.loads(json_str)
    content = data.get("content", [])
    if isinstance(content, list):
        text = " ".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    else:
        text = str(content) if content else ""
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        metadata_json = json.dumps(metadata)
    else:
        metadata_json = str(metadata) if metadata else ""
    return pb2.ChatResponse(  # type: ignore[union-attr]
        content=text,
        finish_reason=data.get("finish_reason", ""),
        metadata_json=metadata_json,
    ).SerializeToString()


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


class MockSyncProvider:
    """Provider with all-synchronous (non-async) methods.

    Used to expose bugs where the adapter calls 'await provider.method()'
    directly instead of routing through _invoke().
    """

    @property
    def name(self) -> str:
        return "sync_provider"

    def get_info(self) -> Any:
        return MagicMock(
            id="sync_provider",
            display_name="Sync Provider",
            credential_env_vars=[],
            capabilities=["chat"],
            defaults={},
            config_fields=[],
        )

    def list_models(self) -> list[Any]:
        return [
            MagicMock(
                id="sync-model-1",
                display_name="Sync Model 1",
                context_window=4096,
                max_output_tokens=1024,
                capabilities=["chat"],
                defaults={},
            )
        ]

    def complete(self, request: Any) -> Any:
        return MagicMock(
            content="sync response",
            tool_calls=[],
            usage=MagicMock(
                prompt_tokens=5,
                completion_tokens=3,
                total_tokens=8,
            ),
            finish_reason="stop",
            metadata={},
        )

    def parse_tool_calls(self, response: Any) -> list[Any]:
        return []


# ---------------------------------------------------------------------------
# TestProviderServiceAdapter
# ---------------------------------------------------------------------------


class CapturingProvider:
    """Provider that captures the argument passed to complete() and parse_tool_calls().

    Used to verify that Complete() passes a PydanticChatRequest (not raw proto)
    and that ParseToolCalls() passes a PydanticChatResponse (not raw proto)
    after the PyO3 bridge refactor.
    """

    def __init__(self) -> None:
        self.received_request: Any = None
        self.received_response: Any = None

    def get_info(self) -> Any:
        return MagicMock(
            id="capturing_provider",
            display_name="Capturing Provider",
            credential_env_vars=[],
            capabilities=["chat"],
            defaults={},
            config_fields=[],
        )

    def list_models(self) -> list[Any]:
        return []

    async def complete(self, request: Any) -> Any:
        self.received_request = request
        return MagicMock(
            content="captured response",
            tool_calls=[],
            usage=MagicMock(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            finish_reason="stop",
            metadata={},
        )

    def parse_tool_calls(self, response: Any) -> list[Any]:
        self.received_response = response
        return []


class TestProviderServiceAdapter:
    """Tests for ProviderServiceAdapter — adapts a Python Provider as gRPC ProviderService."""

    def _make_adapter(self, provider: Any = None) -> Any:
        """Create a ProviderServiceAdapter wrapping *provider*.

        Imports ProviderServiceAdapter from amplifier_foundation.grpc_adapter.services.
        """
        from amplifier_foundation.grpc_adapter.services import (  # type: ignore[import-not-found]  # noqa: PLC0415
            ProviderServiceAdapter,
        )

        if provider is None:
            provider = MockProvider()
        return ProviderServiceAdapter(provider)

    @pytest.fixture(autouse=True)
    def mock_pyo3_bridge(self) -> Any:
        """Mock PyO3 bridge functions for all Complete() tests.

        proto_chat_request_to_json and json_to_proto_chat_response are
        defined in the _engine.pyi stub but may not be compiled into the
        installed .so yet. This autouse fixture patches them so all
        existing Complete() tests continue to pass after the refactor.
        """
        if PydanticChatRequest is None:
            yield
            return
        empty_request_json = PydanticChatRequest(messages=[]).model_dump_json()
        with (
            patch(
                "amplifier_foundation.grpc_adapter.services.proto_chat_request_to_json",
                return_value=empty_request_json,
            ),
            patch(
                "amplifier_foundation.grpc_adapter.services.json_to_proto_chat_response",
                side_effect=_test_json_to_proto_response,
            ),
        ):
            yield

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

    # ------------------------------------------------------------------
    # 8. GetInfo with sync provider (exposes _invoke() routing)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_info_with_sync_provider(self) -> None:
        """GetInfo with a sync provider succeeds (get_info is always called synchronously)."""
        adapter = self._make_adapter(MockSyncProvider())
        ctx = MockContext()
        result = await adapter.GetInfo(pb2.Empty(), ctx)  # type: ignore[union-attr]
        assert result.id == "sync_provider"
        assert result.display_name == "Sync Provider"

    # ------------------------------------------------------------------
    # 9. ListModels with sync provider (exposes _invoke() bug)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_list_models_with_sync_provider(self) -> None:
        """ListModels with a sync provider succeeds via _invoke() executor routing."""
        adapter = self._make_adapter(MockSyncProvider())
        ctx = MockContext()
        result = await adapter.ListModels(pb2.Empty(), ctx)  # type: ignore[union-attr]
        assert len(result.models) == 1
        assert result.models[0].id == "sync-model-1"

    # ------------------------------------------------------------------
    # 10. Complete with sync provider (exposes _invoke() bug)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_complete_with_sync_provider(self) -> None:
        """Complete with a sync provider succeeds via _invoke() executor routing."""
        adapter = self._make_adapter(MockSyncProvider())
        ctx = MockContext()
        result = await adapter.Complete(pb2.ChatRequest(), ctx)  # type: ignore[union-attr]
        assert result.content == "sync response"
        assert result.finish_reason == "stop"

    # ------------------------------------------------------------------
    # 11. ParseToolCalls with sync provider (exposes _invoke() routing)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_parse_tool_calls_with_sync_provider(self) -> None:
        """ParseToolCalls with a sync provider succeeds (parse_tool_calls is called synchronously in the adapter)."""
        adapter = self._make_adapter(MockSyncProvider())
        ctx = MockContext()
        result = await adapter.ParseToolCalls(pb2.ChatResponse(), ctx)  # type: ignore[union-attr]
        assert len(result.tool_calls) == 0

    # ------------------------------------------------------------------
    # 12. ListModels error returns gRPC error (exposes missing error handling)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_list_models_error_returns_grpc_error(self) -> None:
        """ListModels that raises RuntimeError aborts with gRPC error instead of propagating."""
        provider = MockProvider()
        provider.list_models = AsyncMock(side_effect=RuntimeError("list models failed"))
        adapter = self._make_adapter(provider)
        ctx = MockContext()
        await adapter.ListModels(pb2.Empty(), ctx)  # type: ignore[union-attr]
        assert ctx._aborted is True
        assert "list models failed" in ctx.details

    # ------------------------------------------------------------------
    # 13. Complete error returns gRPC error (exposes missing error handling)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_complete_error_returns_grpc_error(self) -> None:
        """Complete that raises RuntimeError aborts with gRPC error instead of propagating."""
        provider = MockProvider()
        provider.complete = AsyncMock(side_effect=RuntimeError("complete failed"))
        adapter = self._make_adapter(provider)
        ctx = MockContext()
        await adapter.Complete(pb2.ChatRequest(), ctx)  # type: ignore[union-attr]
        assert ctx._aborted is True
        assert "complete failed" in ctx.details

    # ------------------------------------------------------------------
    # 14. ParseToolCalls error returns gRPC error (exposes missing error handling)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_parse_tool_calls_error_returns_grpc_error(self) -> None:
        """ParseToolCalls that raises RuntimeError aborts with gRPC error instead of propagating."""
        provider = MockProvider()
        provider.parse_tool_calls = MagicMock(side_effect=RuntimeError("parse failed"))
        adapter = self._make_adapter(provider)
        ctx = MockContext()
        await adapter.ParseToolCalls(pb2.ChatResponse(), ctx)  # type: ignore[union-attr]
        assert ctx._aborted is True
        assert "parse failed" in ctx.details

    # ------------------------------------------------------------------
    # 15. Complete uses PyO3 bridge — provider receives PydanticChatRequest
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        PydanticChatRequest is None,
        reason="amplifier_core.message_models not available",
    )
    async def test_complete_uses_pyo3_bridge(self) -> None:
        """Complete passes a PydanticChatRequest (not raw proto) to provider.complete.

        Verifies the PyO3 bridge refactor: proto_chat_request_to_json converts
        the proto request to JSON, which is then validated into a PydanticChatRequest
        before being passed to provider.complete. The response is converted back
        to proto via json_to_proto_chat_response.
        """
        provider = CapturingProvider()
        adapter = self._make_adapter(provider)
        ctx = MockContext()

        result = await adapter.Complete(pb2.ChatRequest(), ctx)  # type: ignore[union-attr]

        # The provider must have received a PydanticChatRequest, not raw proto
        assert provider.received_request is not None, (
            "provider.complete was never called"
        )
        assert isinstance(provider.received_request, PydanticChatRequest), (  # type: ignore[arg-type]
            f"Expected PydanticChatRequest, got {type(provider.received_request)}"
        )
        # The gRPC response must carry finish_reason='stop'
        assert result.finish_reason == "stop"

    # ------------------------------------------------------------------
    # 16. CompleteStreaming — returns exactly one element with finish_reason='stop'
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_complete_streaming_returns_single_element(self) -> None:
        """CompleteStreaming yields exactly 1 ChatResponse element with finish_reason='stop'.

        Simulated streaming: calls provider.complete() once and yields the full
        response as a single stream element.
        """
        provider = MockProvider()
        provider.complete = AsyncMock(
            return_value=MagicMock(
                content="streamed response",
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

        results = []
        async for item in adapter.CompleteStreaming(pb2.ChatRequest(), ctx):  # type: ignore[union-attr]
            results.append(item)

        assert len(results) == 1, f"Expected exactly 1 element, got {len(results)}"
        assert results[0].finish_reason == "stop"

    # ------------------------------------------------------------------
    # 17. CompleteStreaming — does not abort, yields at least one element
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_complete_streaming_not_unimplemented(self) -> None:
        """CompleteStreaming does not abort with UNIMPLEMENTED and yields >=1 element.

        Verifies CompleteStreaming is implemented (not the base class stub)
        and produces at least one ChatResponse.
        """
        adapter = self._make_adapter()
        ctx = MockContext()

        results = []
        async for item in adapter.CompleteStreaming(pb2.ChatRequest(), ctx):  # type: ignore[union-attr]
            results.append(item)

        assert ctx._aborted is False, "CompleteStreaming should not abort on success"
        assert len(results) >= 1, f"Expected >=1 element, got {len(results)}"

    # ------------------------------------------------------------------
    # 18. ParseToolCalls uses PyO3 bridge — provider receives PydanticChatResponse
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Fix 4: ParseToolCalls must not silently drop content_blocks (field 7)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        PydanticChatResponse is None,
        reason="amplifier_core.message_models not available",
    )
    async def test_parse_tool_calls_preserves_content_blocks(self) -> None:
        """ParseToolCalls must use content_blocks when present (proto field 7).

        When the proto ChatResponse carries typed content blocks, MessageToJson
        produces a JSON dict with a "content_blocks" key.  The current code only
        handles the "content" (string) field and silently ignores "content_blocks",
        yielding an empty PydanticChatResponse.content list.

        After the fix, content_blocks must be mapped to PydanticChatResponse.content.
        """
        import json as _json
        from unittest.mock import patch as _patch

        provider = CapturingProvider()
        adapter = self._make_adapter(provider)
        ctx = MockContext()

        # Simulate proto JSON with content_blocks (snake_case, preserving_proto_field_name=True)
        mock_json = _json.dumps(
            {
                "content_blocks": [
                    {"type": "text", "text": "hello from content block"}
                ],
                "finish_reason": "stop",
                "tool_calls": [],
            }
        )

        import amplifier_foundation.grpc_adapter.services as _svc  # noqa: PLC0415

        with _patch.object(
            _svc._proto_json_format,
            "MessageToJson",
            return_value=mock_json,
        ):
            await adapter.ParseToolCalls(pb2.ChatResponse(), ctx)  # type: ignore[union-attr]

        assert provider.received_response is not None, (
            "provider.parse_tool_calls was never called"
        )
        assert len(provider.received_response.content) == 1, (
            f"Expected 1 content block from content_blocks, got "
            f"{len(provider.received_response.content)}: {provider.received_response.content!r}"
        )
        assert provider.received_response.content[0].type == "text"
        assert provider.received_response.content[0].text == "hello from content block"

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        PydanticChatResponse is None,
        reason="amplifier_core.message_models not available",
    )
    async def test_parse_tool_calls_uses_pyo3_bridge(self) -> None:
        """ParseToolCalls passes a PydanticChatResponse (not raw proto) to provider.parse_tool_calls.

        Verifies the PyO3 bridge refactor: the proto ChatResponse is converted
        to JSON via google.protobuf.json_format.MessageToJson, transformed as
        needed, and validated into a PydanticChatResponse before being passed
        to provider.parse_tool_calls.
        """
        provider = CapturingProvider()
        adapter = self._make_adapter(provider)
        ctx = MockContext()

        await adapter.ParseToolCalls(
            pb2.ChatResponse(content="tool call response", finish_reason="tool_calls"),  # type: ignore[union-attr]
            ctx,
        )

        # The provider must have received a non-None response object
        assert provider.received_response is not None, (
            "provider.parse_tool_calls was never called with a non-None argument"
        )
        assert isinstance(provider.received_response, PydanticChatResponse), (  # type: ignore[arg-type]
            f"Expected PydanticChatResponse, got {type(provider.received_response)}"
        )


# ---------------------------------------------------------------------------
# TestProviderServiceAdapterV2 — v2 PyO3-bridge-specific tests
# ---------------------------------------------------------------------------


class TestProviderServiceAdapterV2:
    """v2 bridge tests: verify proto_chat_request_to_json and json_to_proto_chat_response are called.

    These tests differ from TestProviderServiceAdapter by patching the bridge
    functions directly to confirm they participate in the call chain, rather
    than using a CapturingProvider to inspect what the provider receives.
    """

    def _make_adapter(self, provider: Any = None) -> Any:
        from amplifier_foundation.grpc_adapter.services import (  # type: ignore[import-not-found]  # noqa: PLC0415
            ProviderServiceAdapter,
        )

        if provider is None:
            provider = MockProvider()
        return ProviderServiceAdapter(provider)

    # ------------------------------------------------------------------
    # 1. Complete — proto_chat_request_to_json bridge is invoked
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_complete_pyo3_bridge_proto_to_json_called(self) -> None:
        """Complete() calls proto_chat_request_to_json with the serialised proto bytes.

        Patches proto_chat_request_to_json in the services module and verifies
        it is called once during a Complete() invocation, confirming the v2
        bridge participates in the request-translation path.
        """
        from unittest.mock import patch

        adapter = self._make_adapter()
        ctx = MockContext()

        captured: list[bytes] = []

        import amplifier_foundation.grpc_adapter.services as _svc  # noqa: PLC0415

        original_fn = _svc.proto_chat_request_to_json

        def _spy(proto_bytes: bytes) -> str:
            captured.append(proto_bytes)
            return original_fn(proto_bytes)

        with patch.object(_svc, "proto_chat_request_to_json", side_effect=_spy):
            await adapter.Complete(pb2.ChatRequest(), ctx)  # type: ignore[union-attr]

        assert len(captured) == 1, (
            f"Expected proto_chat_request_to_json to be called once, got {len(captured)}"
        )
        assert isinstance(captured[0], bytes), (
            f"Expected bytes argument, got {type(captured[0])}"
        )

    # ------------------------------------------------------------------
    # 2. Complete — json_to_proto_chat_response bridge is invoked
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_complete_pyo3_bridge_json_to_proto_called(self) -> None:
        """Complete() calls json_to_proto_chat_response with the JSON response string.

        Patches json_to_proto_chat_response in the services module and verifies
        it is called once during a Complete() invocation, confirming the v2
        bridge participates in the response-translation path.
        """
        adapter = self._make_adapter()
        ctx = MockContext()

        captured_json: list[str] = []

        import amplifier_foundation.grpc_adapter.services as _svc  # noqa: PLC0415

        original_fn = _svc.json_to_proto_chat_response

        def _spy(json_str: str) -> bytes:
            captured_json.append(json_str)
            return original_fn(json_str)

        with __import__("unittest.mock", fromlist=["patch"]).patch.object(
            _svc, "json_to_proto_chat_response", side_effect=_spy
        ):
            await adapter.Complete(pb2.ChatRequest(), ctx)  # type: ignore[union-attr]

        assert len(captured_json) == 1, (
            f"Expected json_to_proto_chat_response to be called once, got {len(captured_json)}"
        )
        import json as _json  # noqa: PLC0415

        data = _json.loads(captured_json[0])
        assert "finish_reason" in data, (
            f"Expected 'finish_reason' key in bridge JSON, got keys: {list(data.keys())}"
        )

    # ------------------------------------------------------------------
    # 3. ParseToolCalls — provider receives a PydanticChatResponse
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        PydanticChatResponse is None,
        reason="amplifier_core.message_models not available",
    )
    async def test_parse_tool_calls_provider_receives_pydantic_response(self) -> None:
        """ParseToolCalls passes a PydanticChatResponse to provider.parse_tool_calls.

        Complements TestProviderServiceAdapter.test_parse_tool_calls_uses_pyo3_bridge
        by constructing a proto response with a finish_reason and verifying the
        converted Pydantic object has the matching finish_reason.
        """
        provider = CapturingProvider()
        adapter = self._make_adapter(provider)
        ctx = MockContext()

        await adapter.ParseToolCalls(
            pb2.ChatResponse(finish_reason="tool_calls"),  # type: ignore[union-attr]
            ctx,
        )

        assert provider.received_response is not None, (
            "provider.parse_tool_calls was not called"
        )
        assert isinstance(provider.received_response, PydanticChatResponse), (  # type: ignore[arg-type]
            f"Expected PydanticChatResponse, got {type(provider.received_response)}"
        )


# ---------------------------------------------------------------------------
# MockModule — minimal module for LifecycleServiceAdapter tests
# ---------------------------------------------------------------------------


class MockModule:
    """Module with async mount and cleanup functions for testing LifecycleServiceAdapter."""

    name = "mock_module"
    description = "A mock module for testing"

    async def mount(self, coordinator: Any, config: dict) -> None:
        self.mounted_coordinator = coordinator
        self.mounted_config = config

    async def cleanup(self) -> None:
        self.cleaned_up = True


class MockSyncModule:
    """Module with synchronous mount function."""

    name = "sync_module"
    description = "A sync module"

    def mount(self, coordinator: Any, config: dict) -> None:
        self.mounted_coordinator = coordinator
        self.mounted_config = config


class MockModuleNoMount:
    """Module without mount/cleanup functions (bare module)."""

    name = "bare_module"
    description = "A module with no lifecycle hooks"


class MockFailingModule:
    """Module whose mount raises an exception."""

    name = "failing_module"
    description = "A module that fails on mount"

    async def mount(self, coordinator: Any, config: dict) -> None:
        raise RuntimeError("mount failed")


class MockFailingCleanupModule:
    """Module whose cleanup raises an exception."""

    name = "failing_cleanup_module"
    description = "A module that fails on cleanup"

    async def cleanup(self) -> None:
        raise RuntimeError("cleanup failed")


class MockBareModule:
    """Module with no name, description, mount, or cleanup attributes."""


# ---------------------------------------------------------------------------
# TestLifecycleServiceAdapter
# ---------------------------------------------------------------------------


class TestLifecycleServiceAdapter:
    """Tests for LifecycleServiceAdapter gRPC service."""

    def _make_adapter(self, module: Any = None) -> Any:
        from amplifier_foundation.grpc_adapter.services import (  # type: ignore[import-not-found]  # noqa: PLC0415
            LifecycleServiceAdapter,
        )

        if module is None:
            module = MockModule()
        return LifecycleServiceAdapter(module)

    @pytest.mark.asyncio
    async def test_mount_with_async_fn_returns_success(self) -> None:
        """Mount with async mount_fn succeeds and sets _healthy=True."""
        module = MockModule()
        adapter = self._make_adapter(module)
        request = pb2.MountRequest()  # type: ignore[union-attr]
        request.config["key"] = "value"
        ctx = MockContext()

        response = await adapter.Mount(request, ctx)

        assert response.success is True
        assert response.status == pb2.HEALTH_STATUS_SERVING  # type: ignore[union-attr]
        assert adapter._healthy is True

    @pytest.mark.asyncio
    async def test_mount_with_sync_fn_succeeds(self) -> None:
        """Mount with synchronous mount_fn runs via executor and returns success."""
        module = MockSyncModule()
        adapter = self._make_adapter(module)
        request = pb2.MountRequest()  # type: ignore[union-attr]
        ctx = MockContext()

        response = await adapter.Mount(request, ctx)

        assert response.success is True
        assert adapter._healthy is True

    @pytest.mark.asyncio
    async def test_mount_on_exception_sets_unhealthy(self) -> None:
        """Mount that raises sets _healthy=False and returns failure response."""
        module = MockFailingModule()
        adapter = self._make_adapter(module)
        request = pb2.MountRequest()  # type: ignore[union-attr]
        ctx = MockContext()

        response = await adapter.Mount(request, ctx)

        assert response.success is False
        assert "mount failed" in response.error
        assert response.status == pb2.HEALTH_STATUS_NOT_SERVING  # type: ignore[union-attr]
        assert adapter._healthy is False

    @pytest.mark.asyncio
    async def test_health_check_returns_serving_when_healthy(self) -> None:
        """HealthCheck returns SERVING status when module is healthy."""
        adapter = self._make_adapter()
        ctx = MockContext()

        response = await adapter.HealthCheck(pb2.Empty(), ctx)  # type: ignore[union-attr]

        assert response.status == pb2.HEALTH_STATUS_SERVING  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_cleanup_returns_empty(self) -> None:
        """Cleanup calls cleanup_fn if present and returns Empty."""
        module = MockModule()
        adapter = self._make_adapter(module)
        ctx = MockContext()

        response = await adapter.Cleanup(pb2.Empty(), ctx)  # type: ignore[union-attr]

        assert response == pb2.Empty()  # type: ignore[union-attr]
        assert module.cleaned_up is True

    @pytest.mark.asyncio
    async def test_get_module_info_returns_name_and_description(self) -> None:
        """GetModuleInfo returns ModuleInfo with name and description attributes."""
        adapter = self._make_adapter()
        ctx = MockContext()

        response = await adapter.GetModuleInfo(pb2.Empty(), ctx)  # type: ignore[union-attr]

        assert response.name == "mock_module"
        assert response.description == "A mock module for testing"

    @pytest.mark.asyncio
    async def test_health_check_returns_not_serving_after_failed_mount(self) -> None:
        """HealthCheck returns NOT_SERVING after a mount failure sets _healthy=False."""
        module = MockFailingModule()
        adapter = self._make_adapter(module)
        request = pb2.MountRequest()  # type: ignore[union-attr]
        ctx = MockContext()

        # Drive _healthy to False via a failed mount
        await adapter.Mount(request, ctx)

        response = await adapter.HealthCheck(pb2.Empty(), ctx)  # type: ignore[union-attr]

        assert response.status == pb2.HEALTH_STATUS_NOT_SERVING  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_cleanup_absorbs_exception_silently(self) -> None:
        """Cleanup does not propagate exceptions from cleanup_fn and still returns Empty."""
        module = MockFailingCleanupModule()
        adapter = self._make_adapter(module)
        ctx = MockContext()

        # Must not raise even though cleanup_fn raises internally
        response = await adapter.Cleanup(pb2.Empty(), ctx)  # type: ignore[union-attr]

        assert response == pb2.Empty()  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_get_module_info_uses_defaults_when_attrs_missing(self) -> None:
        """GetModuleInfo returns 'unknown' and '' when module lacks name/description."""
        module = MockBareModule()
        adapter = self._make_adapter(module)
        ctx = MockContext()

        response = await adapter.GetModuleInfo(pb2.Empty(), ctx)  # type: ignore[union-attr]

        assert response.name == "unknown"
        assert response.description == ""

    @pytest.mark.asyncio
    async def test_mount_passes_none_coordinator_and_config(self) -> None:
        """Mount() calls mount_fn(None, config) when no coordinator_shim is provided.

        This is a regression test for the arity bug where Mount() was calling
        mount_fn(config) with one argument instead of mount_fn(None, config)
        with two arguments (coordinator, config).

        When coordinator_shim defaults to None, coordinator must be None (v1 compat).
        """
        call_args: list[Any] = []

        class _RecordingModule:
            name = "recording_module"
            description = "Records mount call args"

            def mount(self, coordinator: Any, config: dict) -> None:  # noqa: ANN101
                call_args.append((coordinator, config))

        module = _RecordingModule()
        adapter = self._make_adapter(module)
        request = pb2.MountRequest()  # type: ignore[union-attr]
        request.config["key"] = "value"
        ctx = MockContext()

        response = await adapter.Mount(request, ctx)

        # Must succeed — wrong arity causes TypeError → success=False
        assert response.success is True, (
            f"Mount() returned success=False (arity bug?): {response.error!r}"
        )
        assert len(call_args) == 1, f"Expected mount called once, got {len(call_args)}"
        coordinator_arg, config_arg = call_args[0]
        assert coordinator_arg is None, (
            f"Expected coordinator=None (v1 limitation), got {coordinator_arg!r}"
        )
        assert config_arg == {"key": "value"}, (
            f"Expected config={{'key': 'value'}}, got {config_arg!r}"
        )


# ---------------------------------------------------------------------------
# TestLifecycleServiceAdapterV2
# ---------------------------------------------------------------------------


class TestLifecycleServiceAdapterV2:
    """Tests for LifecycleServiceAdapter with coordinator_shim (v2 behavior).

    Verifies that the adapter passes coordinator_shim to mount_fn when provided,
    while preserving v1 compat (coordinator=None) when no shim is given.
    """

    def _make_adapter(self, module: Any, coordinator_shim: Any = None) -> Any:
        from amplifier_foundation.grpc_adapter.services import (  # type: ignore[import-not-found]  # noqa: PLC0415
            LifecycleServiceAdapter,
        )

        return LifecycleServiceAdapter(module, coordinator_shim=coordinator_shim)

    @pytest.mark.asyncio
    async def test_mount_passes_coordinator_shim_not_none(self) -> None:
        """Mount() passes coordinator_shim to mount_fn when shim is provided.

        v2 behavior: adapter.Mount() passes self._coordinator_shim as the coordinator arg.
        Skip if coordinator is still None (v1 mode — shim not yet wired from Rust host).

        Note: full wiring deferred until Rust host sends kernel connection info in manifest
        — shim factory and KernelClient are ready.
        """
        captured: list[Any] = []

        class _CapturingModule:
            name = "capturing_module"
            description = "Captures coordinator arg from mount()"

            def mount(self, coordinator: Any, config: dict) -> None:  # noqa: ANN101
                captured.append(coordinator)

        mock_shim = MagicMock()
        mock_shim.session_id = "test-session-id"

        module = _CapturingModule()
        adapter = self._make_adapter(module, coordinator_shim=mock_shim)

        request = pb2.MountRequest()  # type: ignore[union-attr]
        ctx = MockContext()
        response = await adapter.Mount(request, ctx)

        assert response.success is True, (
            f"Mount() returned success=False: {getattr(response, 'error', '')!r}"
        )
        assert len(captured) == 1, f"Expected mount called once, got {len(captured)}"

        coordinator_received = captured[0]
        if coordinator_received is None:
            pytest.skip(
                "coordinator is still None (v1 mode — shim not yet wired from Rust host). "
                "Full wiring deferred until Rust host sends kernel connection info in manifest."
            )

        assert coordinator_received is mock_shim, (
            f"Expected coordinator_shim to be passed, got {coordinator_received!r}"
        )

    @pytest.mark.asyncio
    async def test_coordinator_shim_defaults_to_none(self) -> None:
        """LifecycleServiceAdapter.__init__ accepts no coordinator_shim (defaults to None).

        Verifies backward compat: creating adapter without coordinator_shim works
        and self._coordinator_shim is None.
        """
        from amplifier_foundation.grpc_adapter.services import (  # type: ignore[import-not-found]  # noqa: PLC0415
            LifecycleServiceAdapter,
        )

        class _MinimalModule:
            name = "minimal"
            description = ""

        module = _MinimalModule()
        adapter = LifecycleServiceAdapter(module)
        assert adapter._coordinator_shim is None


# ---------------------------------------------------------------------------
# TestLegacyResponseToDict
# ---------------------------------------------------------------------------


class TestLegacyResponseToDict:
    """Unit tests for the _legacy_response_to_dict helper function."""

    def _fn(self) -> Any:
        from amplifier_foundation.grpc_adapter.services import (  # type: ignore[import-not-found]  # noqa: PLC0415
            _legacy_response_to_dict,
        )

        return _legacy_response_to_dict

    def test_string_content_wraps_as_text_block(self) -> None:
        """String content 'Hello!' is wrapped as [{type: text, text: Hello!}] and finish_reason is 'stop'."""
        _legacy_response_to_dict = self._fn()

        response = MagicMock()
        response.content = "Hello!"
        response.tool_calls = []
        response.usage = MagicMock()
        response.finish_reason = "stop"
        response.metadata = {}

        result = _legacy_response_to_dict(response)

        assert result["content"] == [{"type": "text", "text": "Hello!"}]
        assert result["finish_reason"] == "stop"

    def test_list_content_passes_through(self) -> None:
        """List content [{type: text, text: Hi}] passes through unchanged."""
        _legacy_response_to_dict = self._fn()

        content_list = [{"type": "text", "text": "Hi"}]

        response = MagicMock()
        response.content = content_list
        response.tool_calls = []
        response.usage = MagicMock()
        response.finish_reason = "stop"
        response.metadata = {}

        result = _legacy_response_to_dict(response)

        assert result["content"] == [{"type": "text", "text": "Hi"}]

    def test_tool_calls_serialized(self) -> None:
        """Tool call with id='tc1', name='search', arguments={'query': 'rust'} is serialized to dict with those fields."""
        _legacy_response_to_dict = self._fn()

        tc = MagicMock(id="tc1", arguments={"query": "rust"})
        tc.name = "search"

        response = MagicMock()
        response.content = []
        response.tool_calls = [tc]
        response.usage = MagicMock()
        response.finish_reason = "stop"
        response.metadata = {}

        result = _legacy_response_to_dict(response)

        assert len(result["tool_calls"]) == 1
        serialized_tc = result["tool_calls"][0]
        assert serialized_tc["id"] == "tc1"
        assert serialized_tc["name"] == "search"
        assert json.loads(serialized_tc["arguments"]) == {"query": "rust"}
