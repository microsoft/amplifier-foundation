# Python gRPC Adapter Bugfix Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Fix 8 bugs (5 correctness, 2 security, 1 protocol) found by 3-agent code review, plus strengthen test infrastructure.

**Architecture:** All changes are in `amplifier-foundation` only. The bugs are in two files: `services.py` (4 `_invoke()` consistency bugs + 1 serialization bug + 1 content_type bug) and `__main__.py` (2 security issues + 1 stdout-guard gap). Tests are added/updated in existing test files, and one new mock provider module is created for integration tests.

**Tech Stack:** Python 3.11+, pytest, pytest-asyncio, grpcio, protobuf, amplifier-core interfaces

**Working directory:** `/home/bkrabach/dev/rust-devrust-core/amplifier-foundation`

---

## Task 1: Add sync-provider tests that expose `_invoke()` bugs in `ProviderServiceAdapter`

**Files:**
- Modify: `tests/test_grpc_adapter_services.py`

**Why:** The existing `MockProvider` uses `AsyncMock` for `list_models` and `complete`, and a bare sync function for `get_info` and `parse_tool_calls`. Because `AsyncMock` is itself a coroutine function, calling `await provider.list_models()` works whether or not `_invoke()` is used. We need a provider with **plain sync functions** (not `AsyncMock`) for `list_models` and `complete` to expose the bugs.

**Step 1: Add `MockSyncProvider` class and new test methods**

Open `tests/test_grpc_adapter_services.py`. After the existing `MockProvider` class (around line 244), add the following:

```python
class MockSyncProvider:
    """Provider where ALL methods are plain synchronous functions (not AsyncMock).

    This exposes bugs where the adapter calls 'await provider.method()' directly
    instead of routing through _invoke(), which handles sync-to-async dispatch.
    A plain sync function is NOT a coroutine, so 'await func()' would TypeError.
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
```

Then add the following test methods inside the `TestProviderServiceAdapter` class, after the existing `test_parse_tool_calls_empty` test (around line 415):

```python
    # ------------------------------------------------------------------
    # 8. Sync provider: GetInfo with sync get_info()
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_info_with_sync_provider(self) -> None:
        """GetInfo works correctly when provider.get_info() is a plain sync function."""
        provider = MockSyncProvider()
        adapter = self._make_adapter(provider)
        ctx = MockContext()
        result = await adapter.GetInfo(pb2.Empty(), ctx)  # type: ignore[union-attr]
        assert result.id == "sync_provider"
        assert result.display_name == "Sync Provider"

    # ------------------------------------------------------------------
    # 9. Sync provider: ListModels with sync list_models()
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_list_models_with_sync_provider(self) -> None:
        """ListModels works correctly when provider.list_models() is a plain sync function."""
        provider = MockSyncProvider()
        adapter = self._make_adapter(provider)
        ctx = MockContext()
        result = await adapter.ListModels(pb2.Empty(), ctx)  # type: ignore[union-attr]
        assert len(result.models) == 1
        assert result.models[0].id == "sync-model-1"

    # ------------------------------------------------------------------
    # 10. Sync provider: Complete with sync complete()
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_complete_with_sync_provider(self) -> None:
        """Complete works correctly when provider.complete() is a plain sync function."""
        provider = MockSyncProvider()
        adapter = self._make_adapter(provider)
        ctx = MockContext()
        result = await adapter.Complete(pb2.ChatRequest(), ctx)  # type: ignore[union-attr]
        assert result.content == "sync response"
        assert result.finish_reason == "stop"

    # ------------------------------------------------------------------
    # 11. Sync provider: ParseToolCalls with sync parse_tool_calls()
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_parse_tool_calls_with_sync_provider(self) -> None:
        """ParseToolCalls works correctly when provider.parse_tool_calls() is a plain sync function."""
        provider = MockSyncProvider()
        adapter = self._make_adapter(provider)
        ctx = MockContext()
        result = await adapter.ParseToolCalls(pb2.ChatResponse(), ctx)  # type: ignore[union-attr]
        assert len(result.tool_calls) == 0

    # ------------------------------------------------------------------
    # 12. Error handling: ListModels exception
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_list_models_error_returns_grpc_error(self) -> None:
        """ListModels propagates exception as gRPC INTERNAL error when provider raises."""
        provider = MockProvider()
        provider.list_models = AsyncMock(side_effect=RuntimeError("list failed"))
        adapter = self._make_adapter(provider)
        ctx = MockContext()
        result = await adapter.ListModels(pb2.Empty(), ctx)  # type: ignore[union-attr]
        assert ctx._aborted is True
        assert "list failed" in ctx.details

    # ------------------------------------------------------------------
    # 13. Error handling: Complete exception
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_complete_error_returns_grpc_error(self) -> None:
        """Complete propagates exception as gRPC INTERNAL error when provider raises."""
        provider = MockProvider()
        provider.complete = AsyncMock(side_effect=RuntimeError("complete failed"))
        adapter = self._make_adapter(provider)
        ctx = MockContext()
        result = await adapter.Complete(pb2.ChatRequest(), ctx)  # type: ignore[union-attr]
        assert ctx._aborted is True
        assert "complete failed" in ctx.details

    # ------------------------------------------------------------------
    # 14. Error handling: ParseToolCalls exception
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_parse_tool_calls_error_returns_grpc_error(self) -> None:
        """ParseToolCalls propagates exception as gRPC INTERNAL error when provider raises."""
        provider = MockProvider()
        provider.parse_tool_calls = MagicMock(side_effect=RuntimeError("parse failed"))
        adapter = self._make_adapter(provider)
        ctx = MockContext()
        result = await adapter.ParseToolCalls(pb2.ChatResponse(), ctx)  # type: ignore[union-attr]
        assert ctx._aborted is True
        assert "parse failed" in ctx.details
```

**Step 2: Run the new tests to verify they FAIL (exposing the bugs)**

Run:
```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
python -m pytest tests/test_grpc_adapter_services.py::TestProviderServiceAdapter::test_get_info_with_sync_provider tests/test_grpc_adapter_services.py::TestProviderServiceAdapter::test_list_models_with_sync_provider tests/test_grpc_adapter_services.py::TestProviderServiceAdapter::test_complete_with_sync_provider tests/test_grpc_adapter_services.py::TestProviderServiceAdapter::test_parse_tool_calls_with_sync_provider tests/test_grpc_adapter_services.py::TestProviderServiceAdapter::test_list_models_error_returns_grpc_error tests/test_grpc_adapter_services.py::TestProviderServiceAdapter::test_complete_error_returns_grpc_error tests/test_grpc_adapter_services.py::TestProviderServiceAdapter::test_parse_tool_calls_error_returns_grpc_error -v
```

Expected: Multiple FAIL results:
- `test_list_models_with_sync_provider` — TypeError: `object list can't be used in 'await' expression`
- `test_complete_with_sync_provider` — TypeError: `object MagicMock can't be used in 'await' expression`
- `test_parse_tool_calls_with_sync_provider` — may pass (sync path exists) but no error handling
- `test_list_models_error_returns_grpc_error` — unhandled exception (no try/except)
- `test_complete_error_returns_grpc_error` — unhandled exception (no try/except)
- `test_parse_tool_calls_error_returns_grpc_error` — unhandled exception (no try/except)
- `test_get_info_with_sync_provider` — may pass since `get_info()` is already sync but not wrapped in `_invoke()`, the bare call happens to work for sync functions that don't need the executor. Still, verify it passes.

**Step 3: Verify all 627 existing tests still pass**

Run:
```bash
python -m pytest --tb=short -q
```
Expected: 627 passed (existing tests unaffected by new test additions)

**Step 4: Commit**

```bash
git add tests/test_grpc_adapter_services.py
git commit -m "test: add sync-provider and error-handling tests exposing _invoke() bugs"
```

---

## Task 2: Fix `_invoke()` consistency and add error handling in `ProviderServiceAdapter`

**Files:**
- Modify: `amplifier_foundation/grpc_adapter/services.py`

**Step 1: Fix `GetInfo` to use `_invoke()`**

In `amplifier_foundation/grpc_adapter/services.py`, find line 74:

```python
            info = self._provider.get_info()
```

Replace with:

```python
            info = await _invoke(self._provider.get_info)
```

**Step 2: Fix `ListModels` to use `_invoke()` and add error handling**

Find the `ListModels` method (lines 93-113). Replace the entire method body:

```python
    async def ListModels(self, request: Any, context: Any) -> Any:
        """List available models and return as ListModelsResponse proto."""
        try:
            models = await _invoke(self._provider.list_models)
            model_protos = []
            for model in models:
                defaults = getattr(model, "defaults", None)
                if isinstance(defaults, dict):
                    defaults_json = json.dumps(defaults)
                else:
                    defaults_json = getattr(model, "defaults_json", "") or ""
                model_protos.append(
                    pb2.ModelInfo(  # type: ignore[attr-defined]
                        id=model.id,
                        display_name=model.display_name,
                        context_window=model.context_window,
                        max_output_tokens=model.max_output_tokens,
                        capabilities=list(model.capabilities),
                        defaults_json=defaults_json,
                    )
                )
            return pb2.ListModelsResponse(models=model_protos)  # type: ignore[attr-defined]
        except Exception as e:
            logger.exception("ListModels failed")
            await context.abort(grpc.StatusCode.INTERNAL, str(e))
            return pb2.ListModelsResponse()  # type: ignore[attr-defined]
```

**Step 3: Fix `Complete` to use `_invoke()` and add error handling**

Find the `Complete` method (lines 133-160). Replace the entire method body:

```python
    async def Complete(self, request: Any, context: Any) -> Any:
        """Execute a completion request and return ChatResponse proto."""
        try:
            response = await _invoke(self._provider.complete, request)
            # Serialize tool_calls
            tool_call_protos = self._to_tool_call_protos(response.tool_calls)
            # Serialize usage
            usage_obj = response.usage
            usage = pb2.Usage(  # type: ignore[attr-defined]
                prompt_tokens=getattr(usage_obj, "prompt_tokens", 0),
                completion_tokens=getattr(usage_obj, "completion_tokens", 0),
                total_tokens=getattr(usage_obj, "total_tokens", 0),
                reasoning_tokens=getattr(usage_obj, "reasoning_tokens", 0),
                cache_read_tokens=getattr(usage_obj, "cache_read_tokens", 0),
                cache_creation_tokens=getattr(usage_obj, "cache_creation_tokens", 0),
            )
            # Serialize metadata
            metadata = getattr(response, "metadata", None)
            if isinstance(metadata, dict):
                metadata_json = json.dumps(metadata)
            else:
                metadata_json = metadata or ""
            return pb2.ChatResponse(  # type: ignore[attr-defined]
                content=response.content or "",
                tool_calls=tool_call_protos,
                usage=usage,
                finish_reason=response.finish_reason or "",
                metadata_json=metadata_json,
            )
        except Exception as e:
            logger.exception("Complete failed")
            await context.abort(grpc.StatusCode.INTERNAL, str(e))
            return pb2.ChatResponse()  # type: ignore[attr-defined]
```

**Step 4: Fix `ParseToolCalls` to use `_invoke()` and add error handling**

Find the `ParseToolCalls` method (lines 162-170). Replace the entire method body:

```python
    async def ParseToolCalls(self, request: Any, context: Any) -> Any:
        """Parse tool calls from a ChatResponse and return ParseToolCallsResponse proto."""
        try:
            tool_calls = await _invoke(self._provider.parse_tool_calls, request)
            tool_call_protos = self._to_tool_call_protos(tool_calls)
            return pb2.ParseToolCallsResponse(tool_calls=tool_call_protos)  # type: ignore[attr-defined]
        except Exception as e:
            logger.exception("ParseToolCalls failed")
            await context.abort(grpc.StatusCode.INTERNAL, str(e))
            return pb2.ParseToolCallsResponse()  # type: ignore[attr-defined]
```

**Step 5: Run new tests to verify they PASS**

Run:
```bash
python -m pytest tests/test_grpc_adapter_services.py::TestProviderServiceAdapter -v
```
Expected: All 14 tests PASS (7 original + 7 new)

**Step 6: Verify all tests still pass**

Run:
```bash
python -m pytest --tb=short -q
```
Expected: 634 passed (627 original + 7 new)

**Step 7: Commit**

```bash
git add amplifier_foundation/grpc_adapter/services.py
git commit -m "fix: use _invoke() consistently in ProviderServiceAdapter and add error handling"
```

---

## Task 3: Add tests for `Execute` output serialization and `content_type` bugs

**Files:**
- Modify: `tests/test_grpc_adapter_services.py`

**Step 1: Add tests that expose the serialization and content_type bugs**

In `tests/test_grpc_adapter_services.py`, add the following test methods inside the `TestToolServiceAdapter` class, after `test_execute_with_sync_tool_uses_executor` (around line 201):

```python
    @pytest.mark.asyncio
    async def test_execute_dict_output_returns_valid_json(self) -> None:
        """Execute with dict output returns JSON-serialized bytes, not Python repr."""
        tool = MockTool()
        # Override execute to return a dict output
        async def _execute(input: dict[str, Any]) -> Any:
            return MagicMock(success=True, output={"key": "value", "count": 42}, error=None)
        tool.execute = _execute  # type: ignore[assignment]

        adapter = self._make_adapter(tool)
        context = MockContext()
        request = pb2.ToolExecuteRequest(  # type: ignore[union-attr]
            input=json.dumps({"query": "test"}).encode("utf-8"),
            content_type="application/json",
        )
        response = await adapter.Execute(request, context)
        assert response.success is True
        # Output must be valid JSON, not Python repr like "{'key': 'value', 'count': 42}"
        decoded = response.output.decode("utf-8")
        parsed = json.loads(decoded)  # This would raise if it's Python repr
        assert parsed == {"key": "value", "count": 42}

    @pytest.mark.asyncio
    async def test_execute_none_output_returns_valid_json(self) -> None:
        """Execute with None output returns JSON empty string, not 'None'."""
        tool = MockTool()
        async def _execute(input: dict[str, Any]) -> Any:
            return MagicMock(success=True, output=None, error=None)
        tool.execute = _execute  # type: ignore[assignment]

        adapter = self._make_adapter(tool)
        context = MockContext()
        request = pb2.ToolExecuteRequest(  # type: ignore[union-attr]
            input=json.dumps({}).encode("utf-8"),
            content_type="application/json",
        )
        response = await adapter.Execute(request, context)
        assert response.success is True
        decoded = response.output.decode("utf-8")
        # Must be valid JSON — json.loads must not raise
        parsed = json.loads(decoded)
        assert parsed == ""

    @pytest.mark.asyncio
    async def test_execute_mirrors_content_type(self) -> None:
        """Execute mirrors the input content_type in the response."""
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
        """Execute defaults to application/json when input content_type is empty."""
        adapter = self._make_adapter(MockTool())
        context = MockContext()
        request = pb2.ToolExecuteRequest(  # type: ignore[union-attr]
            input=json.dumps({}).encode("utf-8"),
            content_type="",
        )
        response = await adapter.Execute(request, context)
        assert response.content_type == "application/json"
```

**Step 2: Run new tests to verify they FAIL**

Run:
```bash
python -m pytest tests/test_grpc_adapter_services.py::TestToolServiceAdapter::test_execute_dict_output_returns_valid_json tests/test_grpc_adapter_services.py::TestToolServiceAdapter::test_execute_none_output_returns_valid_json tests/test_grpc_adapter_services.py::TestToolServiceAdapter::test_execute_mirrors_content_type tests/test_grpc_adapter_services.py::TestToolServiceAdapter::test_execute_default_content_type_is_json -v
```

Expected:
- `test_execute_dict_output_returns_valid_json` — FAIL: `json.loads` raises on Python repr `"{'key': 'value', 'count': 42}"`
- `test_execute_none_output_returns_valid_json` — FAIL: output is `b"None"` which is not valid JSON
- `test_execute_mirrors_content_type` — FAIL: `response.content_type` is `"text/plain"` not `"application/json"`
- `test_execute_default_content_type_is_json` — FAIL: `response.content_type` is `"text/plain"`

**Step 3: Commit**

```bash
git add tests/test_grpc_adapter_services.py
git commit -m "test: add Execute serialization and content_type tests exposing bugs"
```

---

## Task 4: Fix `Execute` output serialization and `content_type`

**Files:**
- Modify: `amplifier_foundation/grpc_adapter/services.py`

**Step 1: Fix the `Execute` method in `ToolServiceAdapter`**

In `amplifier_foundation/grpc_adapter/services.py`, find the `Execute` method of `ToolServiceAdapter` (lines 45-62). Replace the try block body:

Find this exact code:
```python
        try:
            input_data = json.loads(request.input.decode("utf-8"))
            result = await _invoke(self._tool.execute, input_data)
            return pb2.ToolExecuteResponse(  # type: ignore[attr-defined]
                success=result.success,
                output=str(result.output).encode("utf-8"),
                content_type="text/plain",
                error=result.error or "",
            )
```

Replace with:
```python
        try:
            # v1: input is always JSON-encoded bytes regardless of content_type
            input_data = json.loads(request.input.decode("utf-8"))
            result = await _invoke(self._tool.execute, input_data)
            return pb2.ToolExecuteResponse(  # type: ignore[attr-defined]
                success=result.success,
                output=json.dumps(result.output if result.output is not None else "").encode("utf-8"),
                content_type=request.content_type or "application/json",
                error=result.error or "",
            )
```

**Step 2: Run the serialization tests to verify they PASS**

Run:
```bash
python -m pytest tests/test_grpc_adapter_services.py::TestToolServiceAdapter -v
```
Expected: All 8 tests PASS (4 original + 4 new)

**Step 3: Run the full integration test to ensure the Execute integration test still passes**

The existing integration test `test_grpc_client_execute` checks for `b"echo: hello"` in the output. With `json.dumps`, the string `"echo: hello"` becomes `b'"echo: hello"'` (JSON-encoded). Verify:

Run:
```bash
python -m pytest tests/test_grpc_adapter_integration.py::TestAdapterHappyPath::test_grpc_client_execute -v
```

If the integration test assertion is `assert b"echo: hello" in response.output`, the JSON-encoded form `b'"echo: hello"'` still contains `b"echo: hello"` as a substring, so this should PASS. If it fails, update the integration test assertion to:
```python
assert b"echo: hello" in response.output
```
(This should already be the case — the substring check is inclusive of the JSON-quoted form.)

**Step 4: Verify all tests still pass**

Run:
```bash
python -m pytest --tb=short -q
```
Expected: 641 passed (627 + 7 from Task 1 + 4 from Task 3 + 3 implicitly from Task 2 = 641)

**Step 5: Commit**

```bash
git add amplifier_foundation/grpc_adapter/services.py
git commit -m "fix: use json.dumps for Execute output and mirror content_type from request"
```

---

## Task 5: Add tests for `__main__.py` security issues

**Files:**
- Modify: `tests/test_grpc_adapter_main.py`

**Step 1: Add security tests**

In `tests/test_grpc_adapter_main.py`, add a new test class at the end of the file (after `TestGetRunningLoopUsage`):

```python
# ---------------------------------------------------------------------------
# Tests: _load_module_object security hardening
# ---------------------------------------------------------------------------


class TestLoadModuleObjectSecurity:
    """Tests for security hardening of _load_module_object()."""

    @pytest.mark.asyncio
    async def test_sys_path_append_not_insert(self) -> None:
        """_load_module_object() uses sys.path.append (not insert at 0) for untrusted paths."""
        import inspect

        from amplifier_foundation.grpc_adapter.__main__ import _load_module_object  # type: ignore[import-not-found]

        source = inspect.getsource(_load_module_object)
        assert "sys.path.append(" in source, (
            "_load_module_object uses sys.path.insert(0, ...) which allows "
            "untrusted paths to shadow stdlib modules; must use sys.path.append()"
        )
        assert "sys.path.insert(0," not in source, (
            "_load_module_object still uses sys.path.insert(0, ...) — "
            "untrusted module paths must not have highest priority"
        )

    @pytest.mark.asyncio
    async def test_malicious_package_name_raises_value_error(self) -> None:
        """_load_module_object() rejects package names with unsafe characters."""
        from pathlib import Path

        from amplifier_foundation.grpc_adapter.__main__ import _load_module_object  # type: ignore[import-not-found]

        # A directory name that would produce a dangerous package name
        # "my-module; rm -rf /" -> "my_module; rm _rf /" after hyphen replacement
        malicious_path = Path("/tmp/my-module; rm -rf /")
        with pytest.raises(ValueError, match="Invalid package name"):
            await _load_module_object(malicious_path, "tool")

    @pytest.mark.asyncio
    async def test_valid_package_name_accepted(self) -> None:
        """_load_module_object() accepts valid Python identifier package names."""
        from pathlib import Path
        from unittest.mock import MagicMock, patch

        from amplifier_foundation.grpc_adapter.__main__ import _load_module_object  # type: ignore[import-not-found]

        mock_module = MagicMock()
        mock_module.mount = None  # no mount function

        with patch("importlib.import_module", return_value=mock_module):
            # "my-valid-module" -> "my_valid_module" which is a valid identifier
            result = await _load_module_object(Path("/tmp/my-valid-module"), "tool")

        assert result is mock_module

    @pytest.mark.asyncio
    async def test_dotted_package_name_rejected(self) -> None:
        """_load_module_object() rejects package names containing dots (path traversal)."""
        from pathlib import Path

        from amplifier_foundation.grpc_adapter.__main__ import _load_module_object  # type: ignore[import-not-found]

        malicious_path = Path("/tmp/os.system")
        with pytest.raises(ValueError, match="Invalid package name"):
            await _load_module_object(malicious_path, "tool")
```

**Step 2: Run the new security tests to verify they FAIL**

Run:
```bash
python -m pytest tests/test_grpc_adapter_main.py::TestLoadModuleObjectSecurity -v
```

Expected:
- `test_sys_path_append_not_insert` — FAIL: source still contains `sys.path.insert(0,`
- `test_malicious_package_name_raises_value_error` — FAIL: no validation, would try to import
- `test_valid_package_name_accepted` — PASS (normal path works)
- `test_dotted_package_name_rejected` — FAIL: no validation

**Step 3: Commit**

```bash
git add tests/test_grpc_adapter_main.py
git commit -m "test: add security tests for _load_module_object sys.path and package name validation"
```

---

## Task 6: Fix security issues in `__main__.py`

**Files:**
- Modify: `amplifier_foundation/grpc_adapter/__main__.py`

**Step 1: Add `import re` at top of file**

In `amplifier_foundation/grpc_adapter/__main__.py`, find the imports block (lines 10-21). Add `import re` after `import logging`:

```python
import logging
import re
import signal
```

**Step 2: Fix `sys.path.insert(0, ...)` to `sys.path.append(...)`**

Find line 95:
```python
        sys.path.insert(0, path_str)
```

Replace with:
```python
        sys.path.append(path_str)
```

**Step 3: Add package name validation**

Find line 98 (after the fix):
```python
    # Derive package name: directory basename with hyphens → underscores
    package_name = module_path.name.replace("-", "_")
```

Replace with:
```python
    # Derive package name: directory basename with hyphens → underscores
    package_name = module_path.name.replace("-", "_")

    # Validate package name to prevent code injection via crafted directory names
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", package_name):
        raise ValueError(
            f"Invalid package name '{package_name}' derived from module path "
            f"'{module_path.name}'. Package names must be valid Python identifiers."
        )
```

**Step 4: Extend stdout redirect guard to include `_create_server()`**

Find lines 296-305 in `_run()`. The current code restores stdout in the `finally` block at line 296, then calls `_create_server()` outside the guard at line 301:

```python
    finally:
        # Always restore stdout
        sys.stdout = real_stdout

    # 8. Create gRPC server
    try:
        server, actual_port = await _create_server(module_obj, module_type, args.port)
    except Exception as e:
        print(f"ERROR:Failed to create gRPC server: {e}", flush=True)
        sys.exit(1)
```

Replace that entire section with:

```python
        # 8. Create gRPC server (inside stdout guard — module init may print)
        try:
            server, actual_port = await _create_server(module_obj, module_type, args.port)
        except Exception as e:
            sys.stdout = real_stdout
            print(f"ERROR:Failed to create gRPC server: {e}", flush=True)
            sys.exit(1)

    finally:
        # Always restore stdout
        sys.stdout = real_stdout
```

The full rewritten `try/finally` block in `_run()` should now look like this (from the `# 4. Redirect stdout` comment through to `# 9. Signal readiness`):

```python
    # 4. Redirect stdout → stderr during activation to protect READY/ERROR protocol
    real_stdout = sys.stdout
    sys.stdout = sys.stderr  # type: ignore[assignment]

    try:
        # 5. Resolve module path: prefer manifest 'path', fallback to ModuleActivator
        path_field = manifest.get("path")
        if path_field:
            module_path = Path(path_field)
            if not module_path.exists():
                sys.stdout = real_stdout
                print(f"ERROR:Module path does not exist: {path_field}", flush=True)
                sys.exit(1)
        else:
            source = manifest.get("source")
            if not source:
                sys.stdout = real_stdout
                print(
                    "ERROR:Missing required field 'path' or 'source' in manifest",
                    flush=True,
                )
                sys.exit(1)
            try:
                from amplifier_foundation.modules.activator import ModuleActivator

                activator = ModuleActivator()
                module_path = await activator.activate(module_name, source)
            except Exception as e:
                sys.stdout = real_stdout
                print(f"ERROR:Module activation failed: {e}", flush=True)
                sys.exit(1)

        # 6. Load module object
        try:
            module_obj = await _load_module_object(module_path, module_type)
        except Exception as e:
            sys.stdout = real_stdout
            print(f"ERROR:Failed to load module: {e}", flush=True)
            sys.exit(1)

        # 7. Verify module type
        try:
            _verify_module_type(module_obj, module_type)
        except TypeError as e:
            sys.stdout = real_stdout
            print(f"ERROR:{e}", flush=True)
            sys.exit(1)

        # 8. Create gRPC server (inside stdout guard — module init may print)
        try:
            server, actual_port = await _create_server(module_obj, module_type, args.port)
        except Exception as e:
            sys.stdout = real_stdout
            print(f"ERROR:Failed to create gRPC server: {e}", flush=True)
            sys.exit(1)

    finally:
        # Always restore stdout
        sys.stdout = real_stdout
```

**Step 5: Add auth token trust model comment**

Find the `_create_server` function, specifically the line (around line 191 after edits):
```python
    server = grpc.aio.server()
```

Add a comment block above it:
```python
    # v1 trust model: localhost port isolation is the sole access gate.
    # No TLS or token-based auth is configured on the gRPC server itself.
    # AMPLIFIER_AUTH_TOKEN is available in os.environ for loaded modules
    # to use directly when making outbound calls to the kernel.
    server = grpc.aio.server()
```

**Step 6: Run security tests to verify they PASS**

Run:
```bash
python -m pytest tests/test_grpc_adapter_main.py::TestLoadModuleObjectSecurity -v
```
Expected: All 4 tests PASS

**Step 7: Verify all tests still pass**

Run:
```bash
python -m pytest --tb=short -q
```
Expected: All tests pass (existing + new)

**Step 8: Commit**

```bash
git add amplifier_foundation/grpc_adapter/__main__.py
git commit -m "fix: security hardening — sys.path.append, package name validation, extend stdout guard"
```

---

## Task 7: Add `pytestmark` skip guards to test files

**Files:**
- Modify: `tests/test_grpc_adapter_services.py`
- Modify: `tests/test_grpc_adapter_integration.py`

**Step 1: Add `pytestmark` to `test_grpc_adapter_services.py`**

In `tests/test_grpc_adapter_services.py`, find line 18 (after the `pb2` import try/except block):

```python
except ImportError:  # grpcio / protobuf not installed in this env
    pb2 = None  # type: ignore[assignment]
```

Add immediately after:

```python

pytestmark = pytest.mark.skipif(pb2 is None, reason="grpcio/protobuf not installed")
```

**Step 2: Add `pytest.importorskip` to `test_grpc_adapter_integration.py`**

In `tests/test_grpc_adapter_integration.py`, find line 19:

```python
import pytest
```

Add immediately after:

```python

pytest.importorskip("grpc", reason="grpcio not installed")
```

**Step 3: Verify all tests still pass**

Run:
```bash
python -m pytest --tb=short -q
```
Expected: All tests pass

**Step 4: Commit**

```bash
git add tests/test_grpc_adapter_services.py tests/test_grpc_adapter_integration.py
git commit -m "fix: add skipif guards so gRPC tests skip cleanly when grpcio is absent"
```

---

## Task 8: Fix dev dependency group in `pyproject.toml`

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add grpcio to the dev dependency group**

In `pyproject.toml`, find the dev dependency group (lines 58-64):

```toml
[dependency-groups]
dev = [
    "notebook>=7.5.0",
    "pytest>=8.4.2",
    "pytest-asyncio>=1.3.0",
    "pytest-timeout>=2.3.0",
]
```

Replace with:

```toml
[dependency-groups]
dev = [
    "grpcio>=1.60.0",
    "notebook>=7.5.0",
    "protobuf>=4.0.0",
    "pytest>=8.4.2",
    "pytest-asyncio>=1.3.0",
    "pytest-timeout>=2.3.0",
]
```

**Step 2: Verify the lock file updates**

Run:
```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
uv sync --group dev
```
Expected: Resolves and installs grpcio and protobuf into the dev environment.

**Step 3: Verify all tests still pass**

Run:
```bash
python -m pytest --tb=short -q
```
Expected: All tests pass

**Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "fix: add grpcio/protobuf to dev dependency group for test coverage"
```

---

## Task 9: Add mock provider module for integration tests

**Files:**
- Modify: `tests/test_grpc_adapter_integration.py`

**Step 1: Add `MOCK_PROVIDER_MODULE` inline source and helpers**

In `tests/test_grpc_adapter_integration.py`, after the `MOCK_TOOL_MODULE` string (after line 65), add:

```python
# ---------------------------------------------------------------------------
# Mock provider module source (inline)
# Written verbatim to tmpdir/test-provider/test_provider/__init__.py
# ---------------------------------------------------------------------------

MOCK_PROVIDER_MODULE = '''\
"""Minimal mock provider module for integration testing."""
from __future__ import annotations

from typing import Any


class _ProviderInfo:
    def __init__(self) -> None:
        self.id = "test-provider"
        self.display_name = "Test Provider"
        self.credential_env_vars: list[str] = []
        self.capabilities: list[str] = ["chat"]
        self.defaults: dict[str, Any] = {}


class _ModelInfo:
    def __init__(self) -> None:
        self.id = "test-model-1"
        self.display_name = "Test Model 1"
        self.context_window = 4096
        self.max_output_tokens = 1024
        self.capabilities: list[str] = ["chat"]
        self.defaults: dict[str, Any] = {}


class _Usage:
    def __init__(self) -> None:
        self.prompt_tokens = 10
        self.completion_tokens = 5
        self.total_tokens = 15
        self.reasoning_tokens = 0
        self.cache_read_tokens = 0
        self.cache_creation_tokens = 0


class _ChatResponse:
    def __init__(self) -> None:
        self.content = "Hello from test provider"
        self.tool_calls: list[Any] = []
        self.usage = _Usage()
        self.finish_reason = "stop"
        self.metadata: dict[str, Any] = {}


class _ToolCall:
    def __init__(self, id: str, name: str, arguments: dict[str, Any]) -> None:
        self.id = id
        self.name = name
        self.arguments = arguments


class TestProvider:
    """Test provider that returns canned responses."""

    name = "test-provider"

    def get_info(self) -> _ProviderInfo:
        return _ProviderInfo()

    async def list_models(self) -> list[_ModelInfo]:
        return [_ModelInfo()]

    async def complete(self, request: Any, **kwargs: Any) -> _ChatResponse:
        return _ChatResponse()

    def parse_tool_calls(self, response: Any) -> list[_ToolCall]:
        return []


_instance = TestProvider()

name = _instance.name
get_info = _instance.get_info
list_models = _instance.list_models
complete = _instance.complete
parse_tool_calls = _instance.parse_tool_calls


def mount() -> None:
    """No-op mount; called by _load_module_object during startup."""
    pass
'''
```

**Step 2: Add helper functions for provider module setup**

After `_write_test_module` function (around line 94), add:

```python
def _write_provider_module(tmpdir: str) -> Path:
    """Create the mock provider module directory structure under *tmpdir*.

    Creates::

        {tmpdir}/test-provider/
            test_provider/
                __init__.py   ← MOCK_PROVIDER_MODULE

    Returns:
        ``Path(tmpdir) / "test-provider"`` — the value to pass to ``_make_provider_manifest``.
    """
    module_dir = Path(tmpdir) / "test-provider"
    module_dir.mkdir(exist_ok=True)
    pkg_dir = module_dir / "test_provider"
    pkg_dir.mkdir(exist_ok=True)
    (pkg_dir / "__init__.py").write_text(MOCK_PROVIDER_MODULE)
    return module_dir


def _make_provider_manifest(module_path: Path) -> str:
    """Return a JSON manifest string for the test-provider at *module_path*."""
    return json.dumps(
        {
            "module": "test-provider",
            "type": "provider",
            "path": str(module_path),
        }
    )
```

**Step 3: Commit (no tests yet — this is the scaffolding)**

```bash
git add tests/test_grpc_adapter_integration.py
git commit -m "test: add mock provider module scaffolding for integration tests"
```

---

## Task 10: Add Provider RPC integration tests

**Files:**
- Modify: `tests/test_grpc_adapter_integration.py`

**Step 1: Add provider integration test methods**

In `tests/test_grpc_adapter_integration.py`, inside the `TestAdapterHappyPath` class, after `test_sigterm_shutdown_within_5_seconds` (around line 352), add:

```python
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
        """gRPC ``ListModels`` returns at least one model from the mock provider."""
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
                        f"Expected model id='test-model-1', got {response.models[0].id!r}"
                    )
                finally:
                    channel.close()
            finally:
                proc.terminate()
                proc.wait(timeout=5)

    def test_grpc_client_complete(self) -> None:
        """gRPC ``Complete`` returns a ChatResponse with expected content."""
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
                    assert response.content == "Hello from test provider", (
                        f"Expected content='Hello from test provider', got {response.content!r}"
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
        """gRPC ``HealthCheck`` returns ``HEALTH_STATUS_SERVING`` for provider adapters."""
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
```

**Step 2: Run the new provider integration tests**

Run:
```bash
python -m pytest tests/test_grpc_adapter_integration.py::TestAdapterHappyPath::test_provider_adapter_prints_ready tests/test_grpc_adapter_integration.py::TestAdapterHappyPath::test_grpc_client_list_models tests/test_grpc_adapter_integration.py::TestAdapterHappyPath::test_grpc_client_complete tests/test_grpc_adapter_integration.py::TestAdapterHappyPath::test_grpc_client_health_check_provider -v
```
Expected: All 4 PASS (since we already fixed `_invoke()` in Task 2)

**Step 3: Verify all tests pass**

Run:
```bash
python -m pytest --tb=short -q
```
Expected: All tests pass

**Step 4: Commit**

```bash
git add tests/test_grpc_adapter_integration.py
git commit -m "test: add Provider RPC integration tests (ListModels, Complete, HealthCheck)"
```

---

## Task 11: Final verification

**Files:** None (verification only)

**Step 1: Run the full test suite**

Run:
```bash
cd /home/bkrabach/dev/rust-devrust-core/amplifier-foundation
python -m pytest --tb=short -q
```
Expected: All tests pass — 627 original + 15 new = 642+ total (exact count depends on test discovery)

**Step 2: Verify no regressions in the integration tests**

Run:
```bash
python -m pytest tests/test_grpc_adapter_integration.py tests/test_grpc_adapter_services.py tests/test_grpc_adapter_main.py -v
```
Expected: All gRPC adapter tests pass

**Step 3: Review the changes**

Run:
```bash
git diff --stat HEAD~8  # Review all commits in this bugfix series
```

Verify the changes touch only:
- `amplifier_foundation/grpc_adapter/services.py` — `_invoke()` fixes + serialization fixes
- `amplifier_foundation/grpc_adapter/__main__.py` — security fixes
- `tests/test_grpc_adapter_services.py` — new sync provider tests, error handling tests, serialization tests, skip guard
- `tests/test_grpc_adapter_integration.py` — new provider integration tests, skip guard
- `tests/test_grpc_adapter_main.py` — new security tests
- `pyproject.toml` — dev dependency group fix
- `uv.lock` — lockfile update

**Step 4: Final commit (if any formatting/lint cleanup needed)**

```bash
# Run linting check
python -m ruff check amplifier_foundation/grpc_adapter/ tests/test_grpc_adapter_*.py
python -m ruff format --check amplifier_foundation/grpc_adapter/ tests/test_grpc_adapter_*.py
```

If any issues found, fix them and commit:
```bash
git add -u && git commit -m "chore: formatting cleanup"
```

---

## Summary of bugs fixed

| # | File | Bug | Fix |
|---|------|-----|-----|
| 1 | `services.py:74` | `GetInfo` calls `get_info()` directly, not via `_invoke()` | `await _invoke(self._provider.get_info)` |
| 2 | `services.py:95` | `ListModels` calls `await self._provider.list_models()` directly | `await _invoke(self._provider.list_models)` + try/except |
| 3 | `services.py:135` | `Complete` calls `await self._provider.complete(request)` directly | `await _invoke(self._provider.complete, request)` + try/except |
| 4 | `services.py:164-168` | `ParseToolCalls` hand-rolls async check without executor | `await _invoke(self._provider.parse_tool_calls, request)` + try/except |
| 5 | `services.py:52` | `str(result.output)` produces Python repr, not JSON | `json.dumps(result.output if result.output is not None else "")` |
| 6 | `services.py:53` | `content_type` hardcoded to `"text/plain"` | `request.content_type or "application/json"` |
| 7 | `__main__.py:95` | `sys.path.insert(0, ...)` allows stdlib shadowing | `sys.path.append(...)` |
| 8 | `__main__.py:98` | No package name validation | Regex validation `^[A-Za-z_][A-Za-z0-9_]*$` |
| 9 | `__main__.py:296-305` | Stdout guard ends before `_create_server()` | Extended guard to include `_create_server()` |
| 10 | `__main__.py:191` | No documentation of auth trust model | Comment documenting v1 trust model |
