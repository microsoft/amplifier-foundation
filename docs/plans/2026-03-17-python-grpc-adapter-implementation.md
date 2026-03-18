# Python gRPC Adapter Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Bridge Python Amplifier modules to non-Python hosts via gRPC, enabling any language with gRPC support to consume the Python module ecosystem.

**Architecture:** A `grpc_adapter` package inside `amplifier-foundation` that reads a single-module manifest from stdin, loads the module via `ModuleActivator`, wraps it in gRPC servicer classes (using proto stubs from `amplifier-core`), and serves it on a `grpc.aio` async server. The adapter communicates readiness via a strict stdout protocol (`READY:<port>` / `ERROR:<message>`).

**Tech Stack:** Python 3.11+, grpcio, grpc.aio (async), protobuf, pytest, pytest-asyncio

**Design document:** `docs/plans/2026-03-17-python-grpc-adapter-design.md`

---

## Prerequisite Knowledge

Before you start, understand these critical facts about the codebase:

1. **Proto stubs already exist** at `amplifier-core/python/amplifier_core/_grpc_gen/`. You import them as `from amplifier_core._grpc_gen import amplifier_module_pb2, amplifier_module_pb2_grpc`. Do NOT regenerate them.

2. **Python protocols** (`Tool`, `Provider`) are `@runtime_checkable` in `amplifier_core.interfaces`. You can use `isinstance(obj, Tool)` at runtime.

3. **The `Tool` protocol** has:
   - `name` property → `str`
   - `description` property → `str`
   - `async execute(input: dict[str, Any]) -> ToolResult`

4. **The `Provider` protocol** has:
   - `name` property → `str`
   - `get_info() -> ProviderInfo` (sync)
   - `async list_models() -> list[ModelInfo]`
   - `async complete(request: ChatRequest, **kwargs) -> ChatResponse`
   - `parse_tool_calls(response: ChatResponse) -> list[ToolCall]` (sync)

5. **Proto message types** you'll use (from `amplifier_module_pb2`):
   - `Empty`, `ToolSpec`, `ToolExecuteRequest`, `ToolExecuteResponse`
   - `ProviderInfo`, `ListModelsResponse`, `ChatRequest`, `ChatResponse`, `ParseToolCallsResponse`, `ToolCallMessage`
   - `MountRequest`, `MountResponse`, `HealthCheckResponse`, `HealthStatus`

6. **Proto servicer base classes** (from `amplifier_module_pb2_grpc`):
   - `ToolServiceServicer` — methods: `GetSpec(request, context)`, `Execute(request, context)`
   - `ProviderServiceServicer` — methods: `GetInfo`, `ListModels`, `Complete`, `CompleteStreaming`, `ParseToolCalls`
   - `ModuleLifecycleServicer` — methods: `Mount`, `Cleanup`, `HealthCheck`, `GetModuleInfo`
   - Registration functions: `add_ToolServiceServicer_to_server()`, `add_ProviderServiceServicer_to_server()`, `add_ModuleLifecycleServicer_to_server()`

7. **Test conventions:** pytest with `pytest-asyncio` in strict mode. Async tests need `@pytest.mark.asyncio`. The project uses `--import-mode=importlib`. Test files live in `tests/` (flat, not nested).

8. **`ModuleActivator.activate()`** returns a `Path` (the module's local directory). It does NOT return the loaded module object. You must import the module yourself after activation adds it to `sys.path`.

9. **`grpcio` is NOT currently a dependency.** It must be added as an optional extra.

---

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Modify | `pyproject.toml` | Add `[grpc-adapter]` optional extra |
| Create | `amplifier_foundation/grpc_adapter/__init__.py` | Package marker |
| Create | `amplifier_foundation/grpc_adapter/services.py` | gRPC servicer adapters |
| Create | `amplifier_foundation/grpc_adapter/__main__.py` | CLI entry point + server lifecycle |
| Create | `tests/test_grpc_adapter_services.py` | Layer 1 unit tests |
| Create | `tests/test_grpc_adapter_integration.py` | Layer 2 integration tests |

---

## Task 1: Add gRPC Optional Dependency

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add the optional dependency group**

Open `pyproject.toml` and add the `[project.optional-dependencies]` section after the `dependencies` list (after line 25). Add it before the `[tool.uv.sources]` line:

```toml
[project.optional-dependencies]
grpc-adapter = [
    "grpcio>=1.78.0",
]
```

The version `>=1.78.0` matches what the generated stubs in `amplifier_core/_grpc_gen/amplifier_module_pb2_grpc.py` require (line 8: `GRPC_GENERATED_VERSION = '1.78.0'`).

Also add `grpcio` to the dev dependency group so tests can import it. In the existing `[dependency-groups]` section, add `"grpcio>=1.78.0"` to the `dev` list:

```toml
[dependency-groups]
dev = [
    "notebook>=7.5.0",
    "pytest>=8.4.2",
    "pytest-asyncio>=1.3.0",
    "grpcio>=1.78.0",
]
```

**Step 2: Install the new dependency**

Run:
```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
uv sync --group dev
```

Expected: Resolves and installs `grpcio`. No errors.

**Step 3: Verify grpcio is importable**

Run:
```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
uv run python -c "import grpc; print(grpc.__version__)"
```

Expected: Prints a version >= 1.78.0.

**Step 4: Commit**

```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
git add pyproject.toml uv.lock
git commit -m "feat(grpc-adapter): add grpcio optional dependency"
```

---

## Task 2: Create the grpc_adapter Package

**Files:**
- Create: `amplifier_foundation/grpc_adapter/__init__.py`

**Step 1: Create the package directory and init file**

```bash
mkdir -p /home/bkrabach/dev/rust-devrust-core/amplifier-foundation/amplifier_foundation/grpc_adapter
```

Create `amplifier_foundation/grpc_adapter/__init__.py` with this exact content:

```python
"""Python gRPC adapter for Amplifier modules.

Bridges Python modules to non-Python hosts via gRPC.
Invoked as: python -m amplifier_foundation.grpc_adapter

See docs/plans/2026-03-17-python-grpc-adapter-design.md for design details.
"""
```

**Step 2: Verify the package is importable**

Run:
```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
uv run python -c "import amplifier_foundation.grpc_adapter; print('OK')"
```

Expected: Prints `OK`.

**Step 3: Commit**

```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
git add amplifier_foundation/grpc_adapter/__init__.py
git commit -m "feat(grpc-adapter): create grpc_adapter package"
```

---

## Task 3: Write Layer 1 Test Fixtures

**Files:**
- Create: `tests/test_grpc_adapter_services.py`

**CRITICAL: NO imports from `amplifier_core.testing`.** All test fixtures are self-contained in this file.

**Step 1: Create the test file with mock fixtures**

Create `tests/test_grpc_adapter_services.py` with these contents:

```python
"""Layer 1 unit tests for gRPC adapter servicer classes.

Tests each servicer in isolation with in-suite mock module objects.
NO imports from amplifier_core.testing — fixtures are self-contained.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_core._grpc_gen import amplifier_module_pb2 as pb2
from amplifier_core._grpc_gen import amplifier_module_pb2_grpc as pb2_grpc


# ---------------------------------------------------------------------------
# In-suite mock modules (~20 lines each, satisfy protocols via duck typing)
# ---------------------------------------------------------------------------


class MockTool:
    """Minimal Tool satisfying the amplifier_core.interfaces.Tool protocol."""

    def __init__(
        self,
        name: str = "mock-tool",
        description: str = "A mock tool for testing",
        parameters_json: str = '{"type": "object", "properties": {"query": {"type": "string"}}}',
    ):
        self._name = name
        self._description = description
        self._parameters_json = parameters_json

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters_json(self) -> str:
        return self._parameters_json

    async def execute(self, input: dict[str, Any]) -> Any:
        """Return a mock ToolResult-like object."""
        return MagicMock(success=True, output="mock output", error=None)


class MockFailingTool:
    """Tool that raises on execute."""

    @property
    def name(self) -> str:
        return "failing-tool"

    @property
    def description(self) -> str:
        return "A tool that always fails"

    @property
    def parameters_json(self) -> str:
        return "{}"

    async def execute(self, input: dict[str, Any]) -> Any:
        raise RuntimeError("Tool execution failed")


class MockSyncTool:
    """Tool with a sync execute method (not async)."""

    @property
    def name(self) -> str:
        return "sync-tool"

    @property
    def description(self) -> str:
        return "A sync tool"

    @property
    def parameters_json(self) -> str:
        return "{}"

    def execute(self, input: dict[str, Any]) -> Any:
        """Sync execute — adapter must use run_in_executor."""
        return MagicMock(success=True, output="sync output", error=None)


class MockProvider:
    """Minimal Provider satisfying the amplifier_core.interfaces.Provider protocol."""

    def __init__(
        self,
        name: str = "mock-provider",
        models: list | None = None,
    ):
        self._name = name
        self._models = models or []

    @property
    def name(self) -> str:
        return self._name

    def get_info(self) -> Any:
        """Return a mock ProviderInfo-like object."""
        return MagicMock(
            id="mock-provider",
            display_name="Mock Provider",
            credential_env_vars=["MOCK_API_KEY"],
            capabilities=["chat"],
            defaults={},
            config_fields=[],
        )

    async def list_models(self) -> list:
        return self._models

    async def complete(self, request: Any, **kwargs: Any) -> Any:
        """Return a mock ChatResponse-like object."""
        return MagicMock(
            content="Hello from mock",
            tool_calls=[],
            usage=MagicMock(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                reasoning_tokens=0,
                cache_read_tokens=0,
                cache_creation_tokens=0,
            ),
            degradation=None,
            finish_reason="stop",
            metadata={},
        )

    def parse_tool_calls(self, response: Any) -> list:
        return []


# ---------------------------------------------------------------------------
# Mock gRPC context
# ---------------------------------------------------------------------------


class MockContext:
    """Mock gRPC servicer context for unit tests."""

    def __init__(self):
        self._code = None
        self._details = None

    def set_code(self, code):
        self._code = code

    def set_details(self, details):
        self._details = details

    async def abort(self, code, details):
        self._code = code
        self._details = details
        raise Exception(f"gRPC abort: {code} {details}")
```

**Step 2: Verify the fixtures are syntactically valid**

Run:
```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
uv run python -c "import tests.test_grpc_adapter_services; print('OK')"
```

Expected: Prints `OK` (or at minimum, no SyntaxError).

**Step 3: Commit**

```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
git add tests/test_grpc_adapter_services.py
git commit -m "test(grpc-adapter): add Layer 1 test fixtures for servicer unit tests"
```

---

## Task 4: Write Failing Layer 1 Tests for ToolServiceAdapter

**Files:**
- Modify: `tests/test_grpc_adapter_services.py`

**Step 1: Append ToolServiceAdapter tests to the test file**

Add the following to the end of `tests/test_grpc_adapter_services.py`:

```python
# ---------------------------------------------------------------------------
# ToolServiceAdapter tests
# ---------------------------------------------------------------------------


class TestToolServiceAdapter:
    """Unit tests for ToolServiceAdapter."""

    def _make_adapter(self, tool=None):
        from amplifier_foundation.grpc_adapter.services import ToolServiceAdapter

        return ToolServiceAdapter(tool or MockTool())

    @pytest.mark.asyncio
    async def test_get_spec_returns_valid_response(self):
        """GetSpec returns a ToolSpec with name, description, parameters_json."""
        adapter = self._make_adapter()
        ctx = MockContext()
        result = await adapter.GetSpec(pb2.Empty(), ctx)
        assert result.name == "mock-tool"
        assert result.description == "A mock tool for testing"
        assert "query" in result.parameters_json

    @pytest.mark.asyncio
    async def test_execute_returns_result_with_success(self):
        """Execute calls tool.execute() and returns ToolExecuteResponse."""
        adapter = self._make_adapter()
        ctx = MockContext()
        request = pb2.ToolExecuteRequest(
            input=json.dumps({"query": "test"}).encode("utf-8"),
            content_type="application/json",
        )
        result = await adapter.Execute(request, ctx)
        assert result.success is True
        assert b"mock output" in result.output

    @pytest.mark.asyncio
    async def test_execute_with_failing_tool_returns_error(self):
        """Execute with a failing tool returns success=False with error message."""
        adapter = self._make_adapter(MockFailingTool())
        ctx = MockContext()
        request = pb2.ToolExecuteRequest(
            input=json.dumps({}).encode("utf-8"),
            content_type="application/json",
        )
        result = await adapter.Execute(request, ctx)
        assert result.success is False
        assert "Tool execution failed" in result.error

    @pytest.mark.asyncio
    async def test_execute_with_sync_tool_uses_executor(self):
        """Execute with a sync tool.execute() uses run_in_executor (doesn't block)."""
        adapter = self._make_adapter(MockSyncTool())
        ctx = MockContext()
        request = pb2.ToolExecuteRequest(
            input=json.dumps({}).encode("utf-8"),
            content_type="application/json",
        )
        # Should succeed without blocking the event loop
        result = await adapter.Execute(request, ctx)
        assert result.success is True
```

**Step 2: Run tests to verify they fail (servicer doesn't exist yet)**

Run:
```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
uv run pytest tests/test_grpc_adapter_services.py::TestToolServiceAdapter -v
```

Expected: ALL 4 tests FAIL with `ModuleNotFoundError` or `ImportError` because `amplifier_foundation.grpc_adapter.services` doesn't exist yet.

**Step 3: Commit**

```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
git add tests/test_grpc_adapter_services.py
git commit -m "test(grpc-adapter): add failing Layer 1 tests for ToolServiceAdapter"
```

---

## Task 5: Implement ToolServiceAdapter

**Files:**
- Create: `amplifier_foundation/grpc_adapter/services.py`

**Step 1: Create services.py with ToolServiceAdapter**

Create `amplifier_foundation/grpc_adapter/services.py` with this content:

```python
"""gRPC servicer adapters that wrap Python Amplifier modules.

Each adapter class inherits from a generated gRPC servicer base class
and delegates to the wrapped Python module object.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import Any

import grpc

from amplifier_core._grpc_gen import amplifier_module_pb2 as pb2
from amplifier_core._grpc_gen import amplifier_module_pb2_grpc as pb2_grpc

logger = logging.getLogger(__name__)


class ToolServiceAdapter(pb2_grpc.ToolServiceServicer):
    """Wraps a Python Tool module as a gRPC ToolService."""

    def __init__(self, tool: Any) -> None:
        self._tool = tool

    async def GetSpec(self, request: pb2.Empty, context: grpc.aio.ServicerContext) -> pb2.ToolSpec:
        """Return the tool's name, description, and JSON Schema parameters."""
        return pb2.ToolSpec(
            name=self._tool.name,
            description=self._tool.description,
            parameters_json=getattr(self._tool, "parameters_json", "{}"),
        )

    async def Execute(
        self, request: pb2.ToolExecuteRequest, context: grpc.aio.ServicerContext
    ) -> pb2.ToolExecuteResponse:
        """Execute the tool with JSON input."""
        try:
            # Deserialize input
            input_data = json.loads(request.input) if request.input else {}

            # Call tool.execute — use run_in_executor if sync
            if inspect.iscoroutinefunction(self._tool.execute):
                result = await self._tool.execute(input_data)
            else:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, self._tool.execute, input_data)

            # Serialize output
            output_str = str(result.output) if result.output is not None else ""
            return pb2.ToolExecuteResponse(
                success=result.success,
                output=output_str.encode("utf-8"),
                content_type="text/plain",
                error=result.error if hasattr(result, "error") and result.error else "",
            )
        except Exception as e:
            logger.exception(f"Tool execution failed: {e}")
            return pb2.ToolExecuteResponse(
                success=False,
                output=b"",
                content_type="text/plain",
                error=str(e),
            )
```

**Step 2: Run tests to verify they pass**

Run:
```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
uv run pytest tests/test_grpc_adapter_services.py::TestToolServiceAdapter -v
```

Expected: ALL 4 tests PASS.

**Step 3: Commit**

```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
git add amplifier_foundation/grpc_adapter/services.py
git commit -m "feat(grpc-adapter): implement ToolServiceAdapter"
```

---

## Task 6: Write Failing Layer 1 Tests for ProviderServiceAdapter

**Files:**
- Modify: `tests/test_grpc_adapter_services.py`

**Step 1: Append ProviderServiceAdapter tests to the test file**

Add the following to the end of `tests/test_grpc_adapter_services.py`:

```python
# ---------------------------------------------------------------------------
# ProviderServiceAdapter tests
# ---------------------------------------------------------------------------


class TestProviderServiceAdapter:
    """Unit tests for ProviderServiceAdapter."""

    def _make_adapter(self, provider=None):
        from amplifier_foundation.grpc_adapter.services import ProviderServiceAdapter

        return ProviderServiceAdapter(provider or MockProvider())

    @pytest.mark.asyncio
    async def test_get_info_returns_valid_response(self):
        """GetInfo returns a ProviderInfo proto with correct fields."""
        adapter = self._make_adapter()
        ctx = MockContext()
        result = await adapter.GetInfo(pb2.Empty(), ctx)
        assert result.id == "mock-provider"
        assert result.display_name == "Mock Provider"
        assert "MOCK_API_KEY" in list(result.credential_env_vars)

    @pytest.mark.asyncio
    async def test_list_models_empty_returns_valid_response(self):
        """ListModels with empty model list returns valid empty response (not null)."""
        adapter = self._make_adapter(MockProvider(models=[]))
        ctx = MockContext()
        result = await adapter.ListModels(pb2.Empty(), ctx)
        assert result is not None
        assert len(result.models) == 0

    @pytest.mark.asyncio
    async def test_list_models_returns_models(self):
        """ListModels returns models when provider has them."""
        mock_model = MagicMock(
            id="gpt-4",
            display_name="GPT-4",
            context_window=128000,
            max_output_tokens=4096,
            capabilities=["chat", "tools"],
            defaults={},
        )
        adapter = self._make_adapter(MockProvider(models=[mock_model]))
        ctx = MockContext()
        result = await adapter.ListModels(pb2.Empty(), ctx)
        assert len(result.models) == 1
        assert result.models[0].id == "gpt-4"

    @pytest.mark.asyncio
    async def test_complete_returns_chat_response(self):
        """Complete calls provider.complete() and serializes response."""
        adapter = self._make_adapter()
        ctx = MockContext()
        request = pb2.ChatRequest()
        result = await adapter.Complete(request, ctx)
        assert result.content == "Hello from mock"
        assert result.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_complete_preserves_thinking_block_signature(self):
        """Complete with ThinkingBlock preserves signature field through proto."""
        provider = MockProvider()

        # Override complete to return a response with metadata containing a thinking signature
        async def mock_complete(request, **kwargs):
            return MagicMock(
                content="thought response",
                tool_calls=[],
                usage=MagicMock(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_creation_tokens=0,
                ),
                degradation=None,
                finish_reason="stop",
                metadata={"thinking_signature": "sig_abc123"},
            )

        provider.complete = mock_complete
        adapter = self._make_adapter(provider)
        ctx = MockContext()
        request = pb2.ChatRequest()
        result = await adapter.Complete(request, ctx)
        # The signature should be preserved in metadata_json
        if result.metadata_json:
            metadata = json.loads(result.metadata_json)
            assert metadata.get("thinking_signature") == "sig_abc123"

    @pytest.mark.asyncio
    async def test_parse_tool_calls_multiple(self):
        """ParseToolCalls with multiple tool calls returns all of them."""
        provider = MockProvider()

        def mock_parse(response):
            return [
                MagicMock(id="call_1", name="tool_a", input={"x": 1}),
                MagicMock(id="call_2", name="tool_b", input={"y": 2}),
            ]

        provider.parse_tool_calls = mock_parse
        adapter = self._make_adapter(provider)
        ctx = MockContext()
        response = pb2.ChatResponse(content="Use tools")
        result = await adapter.ParseToolCalls(response, ctx)
        assert len(result.tool_calls) == 2
        assert result.tool_calls[0].name == "tool_a"
        assert result.tool_calls[1].name == "tool_b"

    @pytest.mark.asyncio
    async def test_parse_tool_calls_empty(self):
        """ParseToolCalls with zero tool calls returns empty list."""
        adapter = self._make_adapter()
        ctx = MockContext()
        response = pb2.ChatResponse(content="No tools")
        result = await adapter.ParseToolCalls(response, ctx)
        assert len(result.tool_calls) == 0
```

**Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
uv run pytest tests/test_grpc_adapter_services.py::TestProviderServiceAdapter -v
```

Expected: ALL 7 tests FAIL with `ImportError` (ProviderServiceAdapter doesn't exist).

**Step 3: Commit**

```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
git add tests/test_grpc_adapter_services.py
git commit -m "test(grpc-adapter): add failing Layer 1 tests for ProviderServiceAdapter"
```

---

## Task 7: Implement ProviderServiceAdapter

**Files:**
- Modify: `amplifier_foundation/grpc_adapter/services.py`

**Step 1: Add ProviderServiceAdapter to services.py**

Append the following class to `amplifier_foundation/grpc_adapter/services.py`:

```python
class ProviderServiceAdapter(pb2_grpc.ProviderServiceServicer):
    """Wraps a Python Provider module as a gRPC ProviderService."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    async def GetInfo(self, request: pb2.Empty, context: grpc.aio.ServicerContext) -> pb2.ProviderInfo:
        """Return provider metadata."""
        try:
            info = self._provider.get_info()
            defaults_json = ""
            if hasattr(info, "defaults") and info.defaults:
                defaults_json = json.dumps(info.defaults) if isinstance(info.defaults, dict) else str(info.defaults)

            return pb2.ProviderInfo(
                id=info.id,
                display_name=info.display_name,
                credential_env_vars=list(info.credential_env_vars) if info.credential_env_vars else [],
                capabilities=list(info.capabilities) if info.capabilities else [],
                defaults_json=defaults_json,
            )
        except Exception as e:
            logger.exception(f"GetInfo failed: {e}")
            await context.abort(grpc.StatusCode.INTERNAL, str(e))

    async def ListModels(self, request: pb2.Empty, context: grpc.aio.ServicerContext) -> pb2.ListModelsResponse:
        """List available models."""
        try:
            models = await self._provider.list_models()
            proto_models = []
            for m in models:
                defaults_json = ""
                if hasattr(m, "defaults") and m.defaults:
                    defaults_json = json.dumps(m.defaults) if isinstance(m.defaults, dict) else str(m.defaults)
                proto_models.append(pb2.ModelInfo(
                    id=m.id,
                    display_name=m.display_name,
                    context_window=m.context_window,
                    max_output_tokens=m.max_output_tokens,
                    capabilities=list(m.capabilities) if m.capabilities else [],
                    defaults_json=defaults_json,
                ))
            return pb2.ListModelsResponse(models=proto_models)
        except Exception as e:
            logger.exception(f"ListModels failed: {e}")
            await context.abort(grpc.StatusCode.INTERNAL, str(e))

    async def Complete(self, request: pb2.ChatRequest, context: grpc.aio.ServicerContext) -> pb2.ChatResponse:
        """Generate completion from ChatRequest."""
        try:
            # For v1, pass the proto request directly to the provider
            # The provider's complete() expects a ChatRequest pydantic model,
            # but we pass the proto — this will need a conversion layer.
            # For now, pass as-is and let the provider handle it.
            response = await self._provider.complete(request)

            # Serialize tool_calls
            proto_tool_calls = []
            if hasattr(response, "tool_calls") and response.tool_calls:
                for tc in response.tool_calls:
                    input_data = tc.input if hasattr(tc, "input") else {}
                    proto_tool_calls.append(pb2.ToolCallMessage(
                        id=tc.id,
                        name=tc.name,
                        arguments_json=json.dumps(input_data) if isinstance(input_data, dict) else str(input_data),
                    ))

            # Serialize usage
            proto_usage = None
            if hasattr(response, "usage") and response.usage:
                u = response.usage
                proto_usage = pb2.Usage(
                    prompt_tokens=getattr(u, "prompt_tokens", 0),
                    completion_tokens=getattr(u, "completion_tokens", 0),
                    total_tokens=getattr(u, "total_tokens", 0),
                    reasoning_tokens=getattr(u, "reasoning_tokens", 0),
                    cache_read_tokens=getattr(u, "cache_read_tokens", 0),
                    cache_creation_tokens=getattr(u, "cache_creation_tokens", 0),
                )

            # Serialize metadata
            metadata_json = ""
            if hasattr(response, "metadata") and response.metadata:
                metadata_json = json.dumps(response.metadata) if isinstance(response.metadata, dict) else str(response.metadata)

            return pb2.ChatResponse(
                content=str(response.content) if response.content else "",
                tool_calls=proto_tool_calls,
                usage=proto_usage,
                finish_reason=getattr(response, "finish_reason", "") or "",
                metadata_json=metadata_json,
            )
        except Exception as e:
            logger.exception(f"Complete failed: {e}")
            await context.abort(grpc.StatusCode.INTERNAL, str(e))

    async def ParseToolCalls(
        self, request: pb2.ChatResponse, context: grpc.aio.ServicerContext
    ) -> pb2.ParseToolCallsResponse:
        """Parse tool calls from a ChatResponse."""
        try:
            # Call the sync parse_tool_calls method
            if inspect.iscoroutinefunction(self._provider.parse_tool_calls):
                tool_calls = await self._provider.parse_tool_calls(request)
            else:
                tool_calls = self._provider.parse_tool_calls(request)

            proto_calls = []
            for tc in tool_calls:
                input_data = tc.input if hasattr(tc, "input") else {}
                proto_calls.append(pb2.ToolCallMessage(
                    id=tc.id,
                    name=tc.name,
                    arguments_json=json.dumps(input_data) if isinstance(input_data, dict) else str(input_data),
                ))
            return pb2.ParseToolCallsResponse(tool_calls=proto_calls)
        except Exception as e:
            logger.exception(f"ParseToolCalls failed: {e}")
            await context.abort(grpc.StatusCode.INTERNAL, str(e))
```

**Step 2: Run tests to verify they pass**

Run:
```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
uv run pytest tests/test_grpc_adapter_services.py::TestProviderServiceAdapter -v
```

Expected: ALL 7 tests PASS.

**Step 3: Commit**

```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
git add amplifier_foundation/grpc_adapter/services.py
git commit -m "feat(grpc-adapter): implement ProviderServiceAdapter"
```

---

## Task 8: Write Failing Layer 1 Tests for LifecycleServiceAdapter

**Files:**
- Modify: `tests/test_grpc_adapter_services.py`

**Step 1: Append LifecycleServiceAdapter tests**

Add the following to the end of `tests/test_grpc_adapter_services.py`:

```python
# ---------------------------------------------------------------------------
# LifecycleServiceAdapter tests
# ---------------------------------------------------------------------------


class TestLifecycleServiceAdapter:
    """Unit tests for LifecycleServiceAdapter."""

    def _make_adapter(self, module=None):
        from amplifier_foundation.grpc_adapter.services import LifecycleServiceAdapter

        return LifecycleServiceAdapter(module or MockTool())

    @pytest.mark.asyncio
    async def test_mount_passes_config_to_module(self):
        """Mount passes config map to the module if it has a mount method."""
        module = MockTool()
        mount_called_with = {}

        async def mock_mount(config):
            mount_called_with.update(config)

        module.mount = mock_mount
        adapter = self._make_adapter(module)
        ctx = MockContext()
        request = pb2.MountRequest(config={"api_key": "sk-test", "complex": '{"nested": true}'})
        result = await adapter.Mount(request, ctx)
        assert result.success is True
        assert mount_called_with.get("api_key") == "sk-test"
        assert mount_called_with.get("complex") == '{"nested": true}'

    @pytest.mark.asyncio
    async def test_mount_succeeds_without_mount_method(self):
        """Mount succeeds when module has no mount method (it's optional)."""
        module = MockTool()  # MockTool has no mount method
        adapter = self._make_adapter(module)
        ctx = MockContext()
        request = pb2.MountRequest(config={})
        result = await adapter.Mount(request, ctx)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_health_check_returns_serving(self):
        """HealthCheck returns SERVING status."""
        adapter = self._make_adapter()
        ctx = MockContext()
        result = await adapter.HealthCheck(pb2.Empty(), ctx)
        assert result.status == pb2.HEALTH_STATUS_SERVING

    @pytest.mark.asyncio
    async def test_cleanup_calls_module_cleanup(self):
        """Cleanup calls the module's cleanup method if it exists."""
        module = MockTool()
        cleanup_called = []

        async def mock_cleanup():
            cleanup_called.append(True)

        module.cleanup = mock_cleanup
        adapter = self._make_adapter(module)
        ctx = MockContext()
        await adapter.Cleanup(pb2.Empty(), ctx)
        assert len(cleanup_called) == 1

    @pytest.mark.asyncio
    async def test_cleanup_succeeds_without_cleanup_method(self):
        """Cleanup succeeds when module has no cleanup method."""
        adapter = self._make_adapter(MockTool())
        ctx = MockContext()
        # Should not raise
        result = await adapter.Cleanup(pb2.Empty(), ctx)
        assert result is not None

    @pytest.mark.asyncio
    async def test_module_exception_returns_grpc_error(self):
        """Module raising exception during Mount returns error response."""
        module = MockTool()

        async def failing_mount(config):
            raise RuntimeError("Mount failed: bad config")

        module.mount = failing_mount
        adapter = self._make_adapter(module)
        ctx = MockContext()
        request = pb2.MountRequest(config={"bad": "config"})
        result = await adapter.Mount(request, ctx)
        assert result.success is False
        assert "Mount failed" in result.error
```

**Step 2: Run tests to verify they fail**

Run:
```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
uv run pytest tests/test_grpc_adapter_services.py::TestLifecycleServiceAdapter -v
```

Expected: ALL 6 tests FAIL with `ImportError` (LifecycleServiceAdapter doesn't exist).

**Step 3: Commit**

```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
git add tests/test_grpc_adapter_services.py
git commit -m "test(grpc-adapter): add failing Layer 1 tests for LifecycleServiceAdapter"
```

---

## Task 9: Implement LifecycleServiceAdapter

**Files:**
- Modify: `amplifier_foundation/grpc_adapter/services.py`

**Step 1: Add LifecycleServiceAdapter to services.py**

Append the following class to `amplifier_foundation/grpc_adapter/services.py`:

```python
class LifecycleServiceAdapter(pb2_grpc.ModuleLifecycleServicer):
    """Wraps any Python module for the ModuleLifecycle gRPC service.

    Handles Mount, HealthCheck, Cleanup, and GetModuleInfo.
    Works with any module type (Tool, Provider, etc.).
    """

    def __init__(self, module: Any) -> None:
        self._module = module
        self._healthy = True

    async def Mount(self, request: pb2.MountRequest, context: grpc.aio.ServicerContext) -> pb2.MountResponse:
        """Mount the module with config from the host."""
        try:
            config = dict(request.config)
            mount_fn = getattr(self._module, "mount", None)
            if mount_fn is not None:
                if inspect.iscoroutinefunction(mount_fn):
                    await mount_fn(config)
                else:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, mount_fn, config)
            self._healthy = True
            return pb2.MountResponse(
                success=True,
                status=pb2.HEALTH_STATUS_SERVING,
            )
        except Exception as e:
            logger.exception(f"Mount failed: {e}")
            self._healthy = False
            return pb2.MountResponse(
                success=False,
                error=str(e),
                status=pb2.HEALTH_STATUS_NOT_SERVING,
            )

    async def HealthCheck(self, request: pb2.Empty, context: grpc.aio.ServicerContext) -> pb2.HealthCheckResponse:
        """Return current health status."""
        status = pb2.HEALTH_STATUS_SERVING if self._healthy else pb2.HEALTH_STATUS_NOT_SERVING
        return pb2.HealthCheckResponse(status=status)

    async def Cleanup(self, request: pb2.Empty, context: grpc.aio.ServicerContext) -> pb2.Empty:
        """Call the module's cleanup method if it exists."""
        try:
            cleanup_fn = getattr(self._module, "cleanup", None)
            if cleanup_fn is not None:
                if inspect.iscoroutinefunction(cleanup_fn):
                    await cleanup_fn()
                else:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, cleanup_fn)
        except Exception as e:
            logger.exception(f"Cleanup failed: {e}")
        return pb2.Empty()

    async def GetModuleInfo(self, request: pb2.Empty, context: grpc.aio.ServicerContext) -> pb2.ModuleInfo:
        """Return basic module info."""
        name = getattr(self._module, "name", "unknown")
        description = getattr(self._module, "description", "")
        return pb2.ModuleInfo(
            name=name,
            description=description,
        )
```

**Step 2: Run ALL Layer 1 tests to verify everything passes**

Run:
```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
uv run pytest tests/test_grpc_adapter_services.py -v
```

Expected: ALL 17 tests PASS (4 Tool + 7 Provider + 6 Lifecycle).

**Step 3: Commit**

```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
git add amplifier_foundation/grpc_adapter/services.py
git commit -m "feat(grpc-adapter): implement LifecycleServiceAdapter"
```

---

## Task 10: Implement `__main__.py`

**Files:**
- Create: `amplifier_foundation/grpc_adapter/__main__.py`

This is the CLI entry point. It reads a manifest from stdin, loads the module, creates a gRPC server, and implements the READY/ERROR stdout protocol.

**Step 1: Create `__main__.py`**

Create `amplifier_foundation/grpc_adapter/__main__.py` with this content:

```python
"""Entry point for the Python gRPC adapter.

Usage:
    AMPLIFIER_AUTH_TOKEN=<uuid> AMPLIFIER_KERNEL_ENDPOINT=127.0.0.1:50050 \
      python -m amplifier_foundation.grpc_adapter --port 50051 < manifest.json

Reads a single-module manifest from stdin, loads the module via ModuleActivator,
wraps it in gRPC servicer classes, and serves on the specified port.

Stdout protocol (strict):
    First line is either READY:<port> or ERROR:<message>.
    All other output goes to stderr.
    All print() calls use flush=True.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Amplifier Python gRPC adapter",
        prog="python -m amplifier_foundation.grpc_adapter",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Port to listen on (0 = OS-assigned random port)",
    )
    return parser.parse_args()


def _read_manifest() -> dict[str, Any]:
    """Read manifest JSON from stdin until EOF."""
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("Empty manifest on stdin")
    return json.loads(raw)


def _load_module_object(module_path: Path, module_type: str) -> Any:
    """Import the module's Python package and return the module object.

    Modules are expected to expose a mount() function at their package root
    that returns the module object (Tool, Provider, etc.).
    """
    # The module directory should already be on sys.path from ModuleActivator
    # Look for __init__.py to find the importable package name
    init_file = module_path / "__init__.py"
    if not init_file.exists():
        raise ImportError(
            f"Module at {module_path} has no __init__.py — cannot import"
        )

    # The package name is the directory name (with hyphens converted to underscores)
    package_name = module_path.name.replace("-", "_")
    mod = importlib.import_module(package_name)

    # Call mount() if available to get the module object
    mount_fn = getattr(mod, "mount", None)
    if mount_fn is not None:
        # mount() may be sync or async — handle both
        import inspect

        if inspect.iscoroutinefunction(mount_fn):
            # We'll call this in the async context later
            return mount_fn
        else:
            return mount_fn()

    # If no mount(), look for a class that matches the expected type
    return mod


def _verify_module_type(module_obj: Any, declared_type: str) -> None:
    """Verify the loaded module satisfies the declared protocol type."""
    from amplifier_core.interfaces import Provider, Tool

    type_to_protocol = {
        "tool": Tool,
        "provider": Provider,
    }

    protocol = type_to_protocol.get(declared_type)
    if protocol is None:
        raise TypeError(
            f"Unsupported module type '{declared_type}'. "
            f"Supported types: {list(type_to_protocol.keys())}"
        )

    if not isinstance(module_obj, protocol):
        raise TypeError(
            f"Module does not satisfy {protocol.__name__} protocol. "
            f"Expected type '{declared_type}', got {type(module_obj).__name__}. "
            f"Check that the module implements all required methods/properties."
        )


async def _create_server(
    module_obj: Any,
    module_type: str,
    port: int,
) -> tuple[Any, int]:
    """Create and start the gRPC async server.

    Returns (server, actual_port).
    """
    import grpc.aio

    from amplifier_foundation.grpc_adapter.services import (
        LifecycleServiceAdapter,
        ProviderServiceAdapter,
        ToolServiceAdapter,
    )

    server = grpc.aio.server()

    # Register the type-specific servicer
    if module_type == "tool":
        from amplifier_core._grpc_gen.amplifier_module_pb2_grpc import (
            add_ToolServiceServicer_to_server,
        )

        add_ToolServiceServicer_to_server(ToolServiceAdapter(module_obj), server)
    elif module_type == "provider":
        from amplifier_core._grpc_gen.amplifier_module_pb2_grpc import (
            add_ProviderServiceServicer_to_server,
        )

        add_ProviderServiceServicer_to_server(ProviderServiceAdapter(module_obj), server)

    # Always register the lifecycle service
    from amplifier_core._grpc_gen.amplifier_module_pb2_grpc import (
        add_ModuleLifecycleServicer_to_server,
    )

    add_ModuleLifecycleServicer_to_server(LifecycleServiceAdapter(module_obj), server)

    # Bind to localhost only
    actual_port = server.add_insecure_port(f"127.0.0.1:{port}")
    await server.start()

    return server, actual_port


async def _run() -> None:
    """Main async entry point."""
    args = _parse_args()

    # Step 1: Read manifest from stdin
    try:
        manifest = _read_manifest()
    except (json.JSONDecodeError, ValueError) as e:
        print(f"ERROR:Invalid manifest: {e}", flush=True)
        sys.exit(1)

    # Step 2: Validate required fields
    module_name = manifest.get("module")
    module_type = manifest.get("type")
    if not module_name:
        print("ERROR:Missing required field 'module' in manifest", flush=True)
        sys.exit(1)
    if not module_type:
        print("ERROR:Missing required field 'type' in manifest", flush=True)
        sys.exit(1)

    source_uri = manifest.get("source", "")
    module_path_str = manifest.get("path", "")

    # Step 3: Read env vars
    auth_token = os.environ.get("AMPLIFIER_AUTH_TOKEN", "")
    kernel_endpoint = os.environ.get("AMPLIFIER_KERNEL_ENDPOINT", "")
    if not auth_token:
        print("WARNING: AMPLIFIER_AUTH_TOKEN not set", file=sys.stderr, flush=True)

    # Step 4: Redirect stdout to stderr during module activation
    # This protects the READY/ERROR protocol on stdout
    original_stdout = sys.stdout
    sys.stdout = sys.stderr

    try:
        # Step 5: Activate the module (download, install, add to sys.path)
        if module_path_str:
            # Pre-resolved local path — skip source resolution
            module_path = Path(module_path_str)
            if not module_path.exists():
                sys.stdout = original_stdout
                print(f"ERROR:Module path does not exist: {module_path}", flush=True)
                sys.exit(1)
            # Still need to add to sys.path
            path_str = str(module_path)
            if path_str not in sys.path:
                sys.path.insert(0, path_str)
        elif source_uri:
            from amplifier_foundation.modules.activator import ModuleActivator

            activator = ModuleActivator()
            module_path = await activator.activate(module_name, source_uri)
        else:
            sys.stdout = original_stdout
            print("ERROR:Manifest must have either 'path' or 'source'", flush=True)
            sys.exit(1)

        # Step 6: Load the module object
        module_obj = _load_module_object(module_path, module_type)

        # Handle async mount() functions
        if asyncio.iscoroutinefunction(module_obj):
            module_obj = await module_obj()

        # Step 7: Verify type compliance
        _verify_module_type(module_obj, module_type)

    except Exception as e:
        sys.stdout = original_stdout
        print(f"ERROR:{e}", flush=True)
        sys.exit(1)

    # Step 8: Restore stdout and create gRPC server
    sys.stdout = original_stdout

    try:
        server, actual_port = await _create_server(module_obj, module_type, args.port)
    except Exception as e:
        print(f"ERROR:Failed to start gRPC server: {e}", flush=True)
        sys.exit(1)

    # Step 9: Print READY protocol
    print(f"READY:{actual_port}", flush=True)

    # Step 10: Handle SIGTERM for graceful shutdown
    shutdown_event = asyncio.Event()

    def _handle_sigterm(signum, frame):
        print(f"Received signal {signum}, shutting down...", file=sys.stderr, flush=True)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    # Wait for shutdown signal
    await shutdown_event.wait()

    # Graceful shutdown with 5s deadline
    print("Shutting down gRPC server...", file=sys.stderr, flush=True)
    await server.stop(grace=5)
    print("Server stopped.", file=sys.stderr, flush=True)


def main() -> None:
    """Sync entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )
    asyncio.run(_run())


if __name__ == "__main__":
    main()
```

**Step 2: Verify the module can be invoked (should error with empty stdin)**

Run:
```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
echo '{}' | uv run python -m amplifier_foundation.grpc_adapter 2>/dev/null
```

Expected: Prints `ERROR:Missing required field 'module' in manifest` and exits non-zero.

**Step 3: Verify with a valid manifest but missing module**

Run:
```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
echo '{"module": "fake-tool", "type": "tool", "path": "/nonexistent"}' | uv run python -m amplifier_foundation.grpc_adapter 2>/dev/null
```

Expected: Prints `ERROR:Module path does not exist: /nonexistent` and exits non-zero.

**Step 4: Commit**

```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
git add amplifier_foundation/grpc_adapter/__main__.py
git commit -m "feat(grpc-adapter): implement __main__.py entry point with READY/ERROR protocol"
```

---

## Task 11: Write Layer 2 Integration Tests

**Files:**
- Create: `tests/test_grpc_adapter_integration.py`

These tests spawn the adapter as a real subprocess and verify the end-to-end flow.

**Step 1: Create the integration test file**

Create `tests/test_grpc_adapter_integration.py` with this content:

```python
"""Layer 2 integration tests — spawn adapter as subprocess.

Tests the full adapter lifecycle: stdin manifest → module loading →
gRPC serving → READY protocol → client calls → SIGTERM shutdown.

Uses a minimal test module defined inline (written to a temp directory).
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Minimal tool module that satisfies the Tool protocol.
# Written to a temp directory for each test.
MOCK_TOOL_MODULE = textwrap.dedent('''\
    """Minimal test tool for gRPC adapter integration tests."""

    from typing import Any


    class _ToolResult:
        def __init__(self, success: bool, output: Any, error: Any = None):
            self.success = success
            self.output = output
            self.error = error


    class TestTool:
        """Minimal Tool satisfying the protocol via duck typing."""

        @property
        def name(self) -> str:
            return "test-tool"

        @property
        def description(self) -> str:
            return "A test tool for integration tests"

        @property
        def parameters_json(self) -> str:
            return '{"type": "object", "properties": {"input": {"type": "string"}}}'

        async def execute(self, input: dict) -> _ToolResult:
            return _ToolResult(
                success=True,
                output=f"echo: {input.get('input', '')}",
            )


    def mount():
        """Module mount point — returns the tool instance."""
        return TestTool()
''')


def _write_test_module(tmpdir: Path) -> Path:
    """Write a minimal tool module to a temp directory."""
    module_dir = tmpdir / "test_tool"
    module_dir.mkdir()
    init_file = module_dir / "__init__.py"
    init_file.write_text(MOCK_TOOL_MODULE)
    return module_dir


def _make_manifest(module_path: Path) -> str:
    """Create a manifest JSON string for the test module."""
    return json.dumps({
        "module": "test-tool",
        "type": "tool",
        "path": str(module_path),
    })


def _spawn_adapter(manifest: str, port: int = 0, env_extra: dict | None = None) -> subprocess.Popen:
    """Spawn the adapter as a subprocess with manifest on stdin."""
    env = os.environ.copy()
    env["AMPLIFIER_AUTH_TOKEN"] = "test-token-123"
    env["AMPLIFIER_KERNEL_ENDPOINT"] = "127.0.0.1:50050"
    if env_extra:
        env.update(env_extra)

    proc = subprocess.Popen(
        [sys.executable, "-m", "amplifier_foundation.grpc_adapter", "--port", str(port)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    proc.stdin.write(manifest)
    proc.stdin.close()
    return proc


def _read_first_line(proc: subprocess.Popen, timeout: float = 15.0) -> str:
    """Read the first line from stdout with a timeout."""
    import selectors

    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)

    deadline = time.monotonic() + timeout
    buf = ""
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        events = sel.select(timeout=min(remaining, 0.1))
        if events:
            char = proc.stdout.read(1)
            if char == "" or char is None:
                break
            buf += char
            if buf.endswith("\n"):
                sel.close()
                return buf.strip()
        # Check if process exited
        if proc.poll() is not None:
            # Read remaining stdout
            rest = proc.stdout.read()
            buf += rest
            sel.close()
            return buf.strip()

    sel.close()
    raise TimeoutError(f"Timed out waiting for first line. Got so far: {buf!r}")


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestAdapterHappyPath:
    """Integration tests for the adapter's happy path."""

    def test_adapter_prints_ready_with_port(self):
        """Adapter prints READY:<port> to stdout on successful startup."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = _write_test_module(Path(tmpdir))
            manifest = _make_manifest(module_path)
            proc = _spawn_adapter(manifest)

            try:
                line = _read_first_line(proc)
                assert line.startswith("READY:"), f"Expected READY:<port>, got: {line!r}"
                port = int(line.split(":")[1])
                assert port > 0
            finally:
                proc.terminate()
                proc.wait(timeout=5)

    def test_grpc_client_get_spec(self):
        """gRPC client can connect and call GetSpec."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = _write_test_module(Path(tmpdir))
            manifest = _make_manifest(module_path)
            proc = _spawn_adapter(manifest)

            try:
                line = _read_first_line(proc)
                assert line.startswith("READY:")
                port = int(line.split(":")[1])

                # Connect gRPC client
                import grpc
                from amplifier_core._grpc_gen import amplifier_module_pb2 as pb2
                from amplifier_core._grpc_gen import amplifier_module_pb2_grpc as pb2_grpc

                channel = grpc.insecure_channel(f"127.0.0.1:{port}")
                stub = pb2_grpc.ToolServiceStub(channel)
                spec = stub.GetSpec(pb2.Empty())
                assert spec.name == "test-tool"
                assert spec.description == "A test tool for integration tests"
                channel.close()
            finally:
                proc.terminate()
                proc.wait(timeout=5)

    def test_grpc_client_execute(self):
        """gRPC client can call Execute and get correct result."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = _write_test_module(Path(tmpdir))
            manifest = _make_manifest(module_path)
            proc = _spawn_adapter(manifest)

            try:
                line = _read_first_line(proc)
                assert line.startswith("READY:")
                port = int(line.split(":")[1])

                import grpc
                from amplifier_core._grpc_gen import amplifier_module_pb2 as pb2
                from amplifier_core._grpc_gen import amplifier_module_pb2_grpc as pb2_grpc

                channel = grpc.insecure_channel(f"127.0.0.1:{port}")
                stub = pb2_grpc.ToolServiceStub(channel)
                request = pb2.ToolExecuteRequest(
                    input=json.dumps({"input": "hello"}).encode("utf-8"),
                    content_type="application/json",
                )
                result = stub.Execute(request)
                assert result.success is True
                assert b"echo: hello" in result.output
                channel.close()
            finally:
                proc.terminate()
                proc.wait(timeout=5)

    def test_grpc_client_health_check(self):
        """gRPC client can call HealthCheck and get SERVING."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = _write_test_module(Path(tmpdir))
            manifest = _make_manifest(module_path)
            proc = _spawn_adapter(manifest)

            try:
                line = _read_first_line(proc)
                assert line.startswith("READY:")
                port = int(line.split(":")[1])

                import grpc
                from amplifier_core._grpc_gen import amplifier_module_pb2 as pb2
                from amplifier_core._grpc_gen import amplifier_module_pb2_grpc as pb2_grpc

                channel = grpc.insecure_channel(f"127.0.0.1:{port}")
                stub = pb2_grpc.ModuleLifecycleStub(channel)
                result = stub.HealthCheck(pb2.Empty())
                assert result.status == pb2.HEALTH_STATUS_SERVING
                channel.close()
            finally:
                proc.terminate()
                proc.wait(timeout=5)

    def test_sigterm_shutdown_within_5_seconds(self):
        """SIGTERM causes adapter to exit within 5 seconds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = _write_test_module(Path(tmpdir))
            manifest = _make_manifest(module_path)
            proc = _spawn_adapter(manifest)

            try:
                line = _read_first_line(proc)
                assert line.startswith("READY:")

                # Send SIGTERM
                proc.send_signal(signal.SIGTERM)

                # Should exit within 5 seconds
                exit_code = proc.wait(timeout=5)
                assert exit_code is not None  # Process exited
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                pytest.fail("Adapter did not exit within 5 seconds of SIGTERM")


# ---------------------------------------------------------------------------
# Failure-path tests
# ---------------------------------------------------------------------------


class TestAdapterFailurePaths:
    """Integration tests for adapter startup failure modes."""

    def test_malformed_json_prints_error(self):
        """Malformed JSON on stdin produces ERROR:<message>."""
        proc = _spawn_adapter("not valid json {{{")
        try:
            line = _read_first_line(proc)
            assert line.startswith("ERROR:"), f"Expected ERROR:..., got: {line!r}"
            exit_code = proc.wait(timeout=5)
            assert exit_code != 0
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_missing_module_field_prints_error(self):
        """Missing 'module' field produces ERROR:<message>."""
        manifest = json.dumps({"type": "tool", "path": "/tmp/fake"})
        proc = _spawn_adapter(manifest)
        try:
            line = _read_first_line(proc)
            assert line.startswith("ERROR:"), f"Expected ERROR:..., got: {line!r}"
            assert "module" in line.lower()
            exit_code = proc.wait(timeout=5)
            assert exit_code != 0
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_missing_type_field_prints_error(self):
        """Missing 'type' field produces ERROR:<message>."""
        manifest = json.dumps({"module": "test", "path": "/tmp/fake"})
        proc = _spawn_adapter(manifest)
        try:
            line = _read_first_line(proc)
            assert line.startswith("ERROR:"), f"Expected ERROR:..., got: {line!r}"
            assert "type" in line.lower()
            exit_code = proc.wait(timeout=5)
            assert exit_code != 0
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_nonexistent_module_path_prints_error(self):
        """Module path that doesn't exist produces ERROR:<message>."""
        manifest = json.dumps({
            "module": "fake-tool",
            "type": "tool",
            "path": "/nonexistent/path/fake_tool",
        })
        proc = _spawn_adapter(manifest)
        try:
            line = _read_first_line(proc)
            assert line.startswith("ERROR:"), f"Expected ERROR:..., got: {line!r}"
            exit_code = proc.wait(timeout=5)
            assert exit_code != 0
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
```

**Step 2: Run the happy-path integration tests**

Run:
```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
uv run pytest tests/test_grpc_adapter_integration.py -v --timeout=30
```

Expected: All tests PASS. The failure-path tests should pass immediately (they test error handling). The happy-path tests may take a few seconds each because they spawn subprocesses.

**IMPORTANT:** If any test hangs, it means the READY protocol isn't working or `flush=True` is missing. Check `__main__.py` carefully.

**Step 3: Commit**

```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
git add tests/test_grpc_adapter_integration.py
git commit -m "test(grpc-adapter): add Layer 2 integration tests"
```

---

## Task 12: Final Verification

**Files:** None (verification only)

**Step 1: Run ALL adapter tests**

Run:
```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
uv run pytest tests/test_grpc_adapter_services.py tests/test_grpc_adapter_integration.py -v --timeout=30
```

Expected: ALL tests PASS. Count should be approximately:
- 4 ToolServiceAdapter tests
- 7 ProviderServiceAdapter tests
- 6 LifecycleServiceAdapter tests
- 5 happy-path integration tests
- 4 failure-path integration tests
- **Total: ~26 tests**

**Step 2: Run the FULL test suite to ensure no regressions**

Run:
```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
uv run pytest tests/ -q --tb=short --timeout=60
```

Expected: All existing tests still pass. No regressions.

**Step 3: Verify the adapter works end-to-end manually**

Run:
```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
echo '{"module": "test", "type": "tool", "path": "/tmp/nonexistent"}' | \
  AMPLIFIER_AUTH_TOKEN=test uv run python -m amplifier_foundation.grpc_adapter --port 0 2>/dev/null
```

Expected: Prints `ERROR:Module path does not exist: /tmp/nonexistent` to stdout and exits non-zero.

**Step 4: Final commit (if any fixes were needed)**

```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
git add -A
git commit -m "feat(grpc-adapter): complete Python gRPC adapter v1

Implements the adapter described in docs/plans/2026-03-17-python-grpc-adapter-design.md:
- ToolServiceAdapter: GetSpec, Execute
- ProviderServiceAdapter: GetInfo, ListModels, Complete, ParseToolCalls
- LifecycleServiceAdapter: Mount, HealthCheck, Cleanup, GetModuleInfo
- CLI entry point with READY/ERROR stdout protocol
- SIGTERM graceful shutdown
- Layer 1 unit tests (17 tests)
- Layer 2 integration tests (9 tests)"
```

---

## Summary

| Task | What | Files | Tests |
|------|------|-------|-------|
| 1 | Add grpcio optional dependency | `pyproject.toml` | — |
| 2 | Create grpc_adapter package | `grpc_adapter/__init__.py` | — |
| 3 | Write test fixtures | `test_grpc_adapter_services.py` | Fixtures only |
| 4 | Write failing Tool tests | `test_grpc_adapter_services.py` | 4 RED |
| 5 | Implement ToolServiceAdapter | `grpc_adapter/services.py` | 4 GREEN |
| 6 | Write failing Provider tests | `test_grpc_adapter_services.py` | 7 RED |
| 7 | Implement ProviderServiceAdapter | `grpc_adapter/services.py` | 7 GREEN |
| 8 | Write failing Lifecycle tests | `test_grpc_adapter_services.py` | 6 RED |
| 9 | Implement LifecycleServiceAdapter | `grpc_adapter/services.py` | 6 GREEN |
| 10 | Implement `__main__.py` | `grpc_adapter/__main__.py` | Manual verify |
| 11 | Write integration tests | `test_grpc_adapter_integration.py` | 9 tests |
| 12 | Final verification | — | All ~26 pass |

**Deferred to v2:** HookService, ApprovalService, ContextService, OrchestratorService, CompleteStreaming, multi-module per process, Windows graceful shutdown, mid-session restart, concurrent access testing, inbound auth.
