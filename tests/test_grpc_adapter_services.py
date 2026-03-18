"""Tests for amplifier_foundation.grpc_adapter.services module.

Verifies the ToolServiceAdapter and ProviderServiceAdapter gRPC service implementations.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

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
        """Mount() calls mount_fn(None, config) — coordinator is None in v1 (adapter has no kernel access).

        This is a regression test for the arity bug where Mount() was calling
        mount_fn(config) with one argument instead of mount_fn(None, config)
        with two arguments (coordinator, config).
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
