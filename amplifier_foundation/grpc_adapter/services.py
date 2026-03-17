"""ToolServiceAdapter: bridges Python tools to the gRPC ToolService contract."""

import asyncio
import inspect
import json
import logging
from typing import Any

import grpc  # noqa: F401  # required by spec

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
