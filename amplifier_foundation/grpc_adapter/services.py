"""gRPC service adapters: bridges Python tools and providers to gRPC service contracts."""

import asyncio
import inspect
import json
import logging
from typing import Any

import grpc

from amplifier_core._grpc_gen import amplifier_module_pb2 as pb2  # type: ignore[attr-defined]
from amplifier_core._grpc_gen import amplifier_module_pb2_grpc as pb2_grpc

logger = logging.getLogger(__name__)


class ToolServiceAdapter(pb2_grpc.ToolServiceServicer):
    """gRPC servicer that adapts a Python tool object to the ToolService contract."""

    def __init__(self, tool: Any) -> None:
        self._tool = tool

    async def GetSpec(self, request: Any, context: Any) -> Any:
        """Return the tool's name, description, and JSON Schema parameters."""
        return pb2.ToolSpec(  # type: ignore[attr-defined]
            name=self._tool.name,
            description=self._tool.description,
            parameters_json=getattr(self._tool, "parameters_json", "{}"),
        )

    async def Execute(self, request: Any, context: Any) -> Any:
        """Execute the tool with JSON input and return the serialized result."""
        try:
            input_data = json.loads(request.input.decode("utf-8"))
            if inspect.iscoroutinefunction(self._tool.execute):
                result = await self._tool.execute(input_data)
            else:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, self._tool.execute, input_data
                )
            return pb2.ToolExecuteResponse(  # type: ignore[attr-defined]
                success=result.success,
                output=str(result.output).encode("utf-8"),
                content_type="text/plain",
                error=result.error or "",
            )
        except Exception as e:
            logger.exception("Tool execution failed")
            return pb2.ToolExecuteResponse(  # type: ignore[attr-defined]
                success=False,
                output=b"",
                error=str(e),
            )


class ProviderServiceAdapter(pb2_grpc.ProviderServiceServicer):
    """gRPC servicer that adapts a Python provider object to the ProviderService contract."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    async def GetInfo(self, request: Any, context: Any) -> Any:
        """Return provider metadata as ProviderInfo proto."""
        try:
            info = self._provider.get_info()
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
            await context.abort(grpc.StatusCode.INTERNAL, str(e))
            return pb2.ProviderInfo()  # type: ignore[attr-defined]

    async def ListModels(self, request: Any, context: Any) -> Any:
        """List available models and return as ListModelsResponse proto."""
        models = await self._provider.list_models()
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

    async def Complete(self, request: Any, context: Any) -> Any:
        """Execute a completion request and return ChatResponse proto."""
        response = await self._provider.complete(request)
        # Serialize tool_calls
        tool_call_protos = []
        for tc in response.tool_calls or []:
            arguments = getattr(tc, "arguments", None)
            if isinstance(arguments, dict):
                arguments_json = json.dumps(arguments)
            else:
                arguments_json = getattr(tc, "arguments_json", "{}") or "{}"
            tool_call_protos.append(
                pb2.ToolCallMessage(  # type: ignore[attr-defined]
                    id=tc.id,
                    name=tc.name,
                    arguments_json=arguments_json,
                )
            )
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

    async def ParseToolCalls(self, request: Any, context: Any) -> Any:
        """Parse tool calls from a ChatResponse and return ParseToolCallsResponse proto."""
        parse_fn = self._provider.parse_tool_calls
        if inspect.iscoroutinefunction(parse_fn):
            tool_calls = await parse_fn(request)
        else:
            tool_calls = parse_fn(request)
        tool_call_protos = []
        for tc in tool_calls or []:
            arguments = getattr(tc, "arguments", None)
            if isinstance(arguments, dict):
                arguments_json = json.dumps(arguments)
            else:
                arguments_json = getattr(tc, "arguments_json", "{}") or "{}"
            tool_call_protos.append(
                pb2.ToolCallMessage(  # type: ignore[attr-defined]
                    id=tc.id,
                    name=tc.name,
                    arguments_json=arguments_json,
                )
            )
        return pb2.ParseToolCallsResponse(tool_calls=tool_call_protos)  # type: ignore[attr-defined]
