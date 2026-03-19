"""gRPC service adapters: bridges Python tools and providers to gRPC service contracts."""

import asyncio
import inspect
import json
import logging
from typing import Any

from google.protobuf import json_format as _proto_json_format
from pydantic import BaseModel as _PydanticBaseModel

try:
    import grpc
except ImportError:
    raise ImportError(
        "grpcio is required for the gRPC adapter. "
        "Install it with: pip install amplifier-foundation[grpc-adapter]"
    )

from amplifier_core._grpc_gen import amplifier_module_pb2 as pb2  # type: ignore[attr-defined]
from amplifier_core._grpc_gen import amplifier_module_pb2_grpc as pb2_grpc

from amplifier_core.message_models import ChatRequest as PydanticChatRequest
from amplifier_core.message_models import ChatResponse as PydanticChatResponse

try:
    from amplifier_core._engine import (  # type: ignore[attr-defined]
        json_to_proto_chat_response,
        proto_chat_request_to_json,
    )
except ImportError:

    def proto_chat_request_to_json(proto_bytes: bytes) -> str:  # type: ignore[misc]
        raise NotImplementedError(
            "proto_chat_request_to_json is not available in this build of amplifier_core._engine"
        )

    def json_to_proto_chat_response(json_str: str) -> bytes:  # type: ignore[misc]
        raise NotImplementedError(
            "json_to_proto_chat_response is not available in this build of amplifier_core._engine"
        )


logger = logging.getLogger(__name__)


def _legacy_response_to_dict(response: Any) -> dict[str, Any]:
    """Convert a non-Pydantic provider response to a dict for JSON serialization.

    Backward-compatible helper for provider implementations that return plain
    objects (or MagicMocks in tests) instead of a Pydantic ChatResponse.

    - String content is wrapped as [{"type": "text", "text": content}].
    - Tool calls are serialized with id, name, and arguments.
    - Usage tokens are extracted from the usage sub-object.
    """
    # Content: wrap bare string as a typed text block
    content = getattr(response, "content", None)
    if isinstance(content, str):
        content_blocks: list[dict[str, Any]] = [{"type": "text", "text": content}]
    elif isinstance(content, list):
        content_blocks = content
    else:
        content_blocks = []

    # Tool calls
    tool_calls = []
    for tc in getattr(response, "tool_calls", None) or []:
        arguments = getattr(tc, "arguments", None)
        if isinstance(arguments, dict):
            arguments_str = json.dumps(arguments)
        else:
            arguments_str = getattr(tc, "arguments_json", "{}") or "{}"
        tool_calls.append(
            {
                "id": getattr(tc, "id", ""),
                "name": getattr(tc, "name", ""),
                "arguments": arguments_str,
            }
        )

    # Usage tokens
    usage_obj = getattr(response, "usage", None)
    usage = {
        "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0),
        "completion_tokens": getattr(usage_obj, "completion_tokens", 0),
        "total_tokens": getattr(usage_obj, "total_tokens", 0),
    }

    # Metadata (pass through as-is for downstream serialization)
    metadata = getattr(response, "metadata", None)

    return {
        "content": content_blocks,
        "tool_calls": tool_calls,
        "usage": usage,
        "finish_reason": getattr(response, "finish_reason", "") or "",
        "metadata": metadata,
    }


async def _invoke(fn: Any, *args: Any) -> Any:
    """Call *fn* with *args*, awaiting if it is a coroutine, else running in executor."""
    if inspect.iscoroutinefunction(fn):
        return await fn(*args)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fn, *args)


class ToolServiceAdapter(pb2_grpc.ToolServiceServicer):
    """gRPC servicer that adapts a Python tool object to the ToolService contract."""

    def __init__(self, tool: Any) -> None:
        self._tool = tool

    async def GetSpec(self, request: Any, context: Any) -> Any:
        """Return the tool's name, description, and JSON Schema parameters."""
        try:
            return pb2.ToolSpec(  # type: ignore[attr-defined]
                name=self._tool.name,
                description=self._tool.description,
                parameters_json=getattr(self._tool, "parameters_json", "{}"),
            )
        except Exception as e:
            logger.exception("GetSpec failed")
            await context.abort(
                grpc.StatusCode.INTERNAL, str(e)
            )  # v1: caller is trusted host (localhost-only)
            return pb2.ToolSpec()  # type: ignore[attr-defined]

    async def Execute(self, request: Any, context: Any) -> Any:
        """Execute the tool with JSON input and return the serialized result."""
        try:
            # v1: input is always JSON-encoded bytes regardless of content_type
            input_data = json.loads(request.input.decode("utf-8"))
            result = await _invoke(self._tool.execute, input_data)
            output_val = result.output if result.output is not None else ""
            if isinstance(output_val, bytes):
                output_val = output_val.decode("utf-8")
            return pb2.ToolExecuteResponse(  # type: ignore[attr-defined]
                success=result.success,
                output=json.dumps(output_val).encode("utf-8"),
                content_type=request.content_type or "application/json",
                error=result.error or "",
            )
        except Exception as e:
            logger.exception("Tool execution failed")
            return pb2.ToolExecuteResponse(  # type: ignore[attr-defined]
                success=False,
                output=b"",
                error=str(e),  # v1: caller is trusted host (localhost-only)
            )


class ProviderServiceAdapter(pb2_grpc.ProviderServiceServicer):
    """gRPC servicer that adapts a Python provider object to the ProviderService contract."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    async def GetInfo(self, request: Any, context: Any) -> Any:
        """Return provider metadata as ProviderInfo proto."""
        try:
            info = await _invoke(self._provider.get_info)
            # Handle defaults: JSON-serialize if dict, otherwise use as-is or empty string
            defaults = getattr(info, "defaults", None)
            if isinstance(defaults, dict):
                defaults_json = json.dumps(defaults)
            else:
                defaults_json = getattr(info, "defaults_json", "") or ""
            return pb2.ProviderInfo(  # type: ignore[attr-defined]
                id=info.id,
                display_name=info.display_name,
                credential_env_vars=list(info.credential_env_vars),
                capabilities=list(info.capabilities),
                defaults_json=defaults_json,
            )
        except Exception as e:
            logger.exception("GetInfo failed")
            await context.abort(
                grpc.StatusCode.INTERNAL, str(e)
            )  # v1: caller is trusted host (localhost-only)
            return pb2.ProviderInfo()  # type: ignore[attr-defined]

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
            await context.abort(
                grpc.StatusCode.INTERNAL, str(e)
            )  # v1: caller is trusted host (localhost-only)
            return pb2.ListModelsResponse()  # type: ignore[attr-defined]

    def _to_tool_call_protos(self, tool_calls: Any) -> list[Any]:
        """Map a list of tool call objects to ToolCallMessage protos."""
        protos = []
        for tc in tool_calls or []:
            arguments = getattr(tc, "arguments", None)
            if isinstance(arguments, dict):
                arguments_json = json.dumps(arguments)
            else:
                arguments_json = getattr(tc, "arguments_json", "{}") or "{}"
            protos.append(
                pb2.ToolCallMessage(  # type: ignore[attr-defined]
                    id=tc.id,
                    name=tc.name,
                    arguments_json=arguments_json,
                )
            )
        return protos

    async def Complete(self, request: Any, context: Any) -> Any:
        """Execute a completion request and return ChatResponse proto."""
        try:
            # 1. Serialize proto request to bytes
            proto_bytes = request.SerializeToString()
            # 2. Convert proto bytes to JSON via PyO3 bridge
            json_str = proto_chat_request_to_json(proto_bytes)
            # 3. Validate JSON into PydanticChatRequest
            pydantic_request = PydanticChatRequest.model_validate_json(json_str)
            # 4. Call provider with the Pydantic request object
            response = await _invoke(self._provider.complete, pydantic_request)
            # 5. Serialize response to JSON
            if isinstance(response, _PydanticBaseModel):
                # Pydantic ChatResponse — use native serialization
                response_json = response.model_dump_json()
            else:
                # Legacy response object — convert via helper
                response_dict = _legacy_response_to_dict(response)
                response_json = json.dumps(response_dict)
            # 6. Convert response JSON to proto bytes via PyO3 bridge
            response_proto_bytes = json_to_proto_chat_response(response_json)
            # 7. Deserialize proto bytes to ChatResponse
            return pb2.ChatResponse.FromString(response_proto_bytes)  # type: ignore[attr-defined]
        except Exception as e:
            logger.exception("Complete failed")
            await context.abort(
                grpc.StatusCode.INTERNAL, str(e)
            )  # v1: caller is trusted host (localhost-only)
            return pb2.ChatResponse()  # type: ignore[attr-defined]

    async def ParseToolCalls(self, request: Any, context: Any) -> Any:
        """Parse tool calls from a ChatResponse and return ParseToolCallsResponse proto."""
        try:
            # 1. Serialize proto to JSON via google.protobuf.json_format
            json_str = _proto_json_format.MessageToJson(
                request, preserving_proto_field_name=True
            )
            # 2. Parse JSON and transform content field if needed
            #    Proto ChatResponse has content as a string (or missing when empty),
            #    but PydanticChatResponse expects content as a list of content blocks.
            data = json.loads(json_str) if json_str else {}
            content_val = data.get("content")
            if isinstance(content_val, str):
                data["content"] = (
                    [{"type": "text", "text": content_val}] if content_val else []
                )
            elif content_val is None:
                data["content"] = []
            # 3. Validate into PydanticChatResponse
            pydantic_response = PydanticChatResponse.model_validate(data)
            # 4. Call provider with the Pydantic response object
            tool_calls = await _invoke(
                self._provider.parse_tool_calls, pydantic_response
            )
            tool_call_protos = self._to_tool_call_protos(tool_calls)
            return pb2.ParseToolCallsResponse(tool_calls=tool_call_protos)  # type: ignore[attr-defined]
        except Exception as e:
            logger.exception("ParseToolCalls failed")
            await context.abort(
                grpc.StatusCode.INTERNAL, str(e)
            )  # v1: caller is trusted host (localhost-only)
            return pb2.ParseToolCallsResponse()  # type: ignore[attr-defined]


class LifecycleServiceAdapter(pb2_grpc.ModuleLifecycleServicer):
    """gRPC servicer that adapts a Python module object to the ModuleLifecycle contract."""

    def __init__(self, module: Any) -> None:
        self._module = module
        self._healthy = True

    async def Mount(self, request: Any, context: Any) -> Any:
        """Mount the module with the given config."""
        try:
            config = dict(request.config)
            mount_fn = getattr(self._module, "mount", None)
            if mount_fn is not None:
                # v1: coordinator is None — adapter doesn't have kernel coordinator access.
                # Modules that require coordinator for initialization will need v2 changes.
                await _invoke(mount_fn, None, config)
            self._healthy = True
            return pb2.MountResponse(  # type: ignore[attr-defined]
                success=True,
                status=pb2.HEALTH_STATUS_SERVING,  # type: ignore[attr-defined]
            )
        except Exception as e:
            logger.exception("Mount failed")
            self._healthy = False
            return pb2.MountResponse(  # type: ignore[attr-defined]
                success=False,
                error=str(e),  # v1: caller is trusted host (localhost-only)
                status=pb2.HEALTH_STATUS_NOT_SERVING,  # type: ignore[attr-defined]
            )

    async def HealthCheck(self, request: Any, context: Any) -> Any:
        """Return health status based on current _healthy flag."""
        if self._healthy:
            status = pb2.HEALTH_STATUS_SERVING  # type: ignore[attr-defined]
        else:
            status = pb2.HEALTH_STATUS_NOT_SERVING  # type: ignore[attr-defined]
        return pb2.HealthCheckResponse(status=status)  # type: ignore[attr-defined]

    async def Cleanup(self, request: Any, context: Any) -> Any:
        """Call the module's cleanup function if present, then return Empty."""
        try:
            cleanup_fn = getattr(self._module, "cleanup", None)
            if cleanup_fn is not None:
                await _invoke(cleanup_fn)
        except Exception:
            logger.exception("Cleanup failed")
        return pb2.Empty()  # type: ignore[attr-defined]

    async def GetModuleInfo(self, request: Any, context: Any) -> Any:
        """Return module name and description with defaults."""
        name = getattr(self._module, "name", "unknown")
        description = getattr(self._module, "description", "")
        return pb2.ModuleInfo(name=name, description=description)  # type: ignore[attr-defined]
