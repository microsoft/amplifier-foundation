"""CLI entry point for the Python gRPC adapter.

Invoked as: python -m amplifier_foundation.grpc_adapter [--port PORT]
Reads a JSON manifest from stdin, loads the specified Python module, wraps it
in a gRPC server, and prints 'READY:<port>' on stdout when ready.

On any startup failure, prints 'ERROR:<message>' to stdout and exits non-zero.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import re
import signal
import sys
from pathlib import Path
from typing import Any

try:
    from amplifier_core._grpc_gen import amplifier_module_pb2  # type: ignore[attr-defined]
except ImportError:
    amplifier_module_pb2 = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# KernelClient
# ---------------------------------------------------------------------------


class KernelClient:
    """Thin wrapper for KernelService gRPC callbacks.

    Provides a clean Python API for out-of-process modules to call back into
    the kernel via its KernelService gRPC interface. This is NOT a coordinator
    proxy — KernelService is the cross-process API.
    """

    def __init__(
        self,
        stub: Any,
        metadata: list[tuple[str, str]],
        session_id: str,
        parent_id: str | None = None,
    ) -> None:
        """Initialise the KernelClient.

        Args:
            stub: A KernelService gRPC stub.
            metadata: Auth metadata tuples, e.g. [("authorization", "Bearer <token>")].
            session_id: The current session ID.
            parent_id: Optional parent session ID.
        """
        self._stub = stub
        self.metadata = metadata
        self.session_id = session_id
        self.parent_id = parent_id

    def emit_hook(self, event: str, data: dict[str, Any] | None = None) -> Any:
        """Emit a hook event to the kernel.

        Args:
            event: The event name to emit.
            data: Optional dict payload; serialised to JSON before sending.

        Returns:
            The HookResult response from the kernel.
        """
        data_json = json.dumps(data) if data is not None else ""
        request = amplifier_module_pb2.EmitHookRequest(event=event, data_json=data_json)  # type: ignore[union-attr]
        return self._stub.EmitHook(request, metadata=self.metadata)

    def get_capability(self, name: str) -> Any | None:
        """Retrieve a named capability from the kernel.

        Args:
            name: The capability name to look up.

        Returns:
            The parsed JSON value if found, otherwise None.
        """
        request = amplifier_module_pb2.GetCapabilityRequest(name=name)  # type: ignore[union-attr]
        resp = self._stub.GetCapability(request, metadata=self.metadata)
        if resp.found and resp.value_json:
            return json.loads(resp.value_json)
        return None


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Namespace with .port attribute (int, default 0 for OS-assigned).
    """
    parser = argparse.ArgumentParser(
        description="Python gRPC adapter for Amplifier modules",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Port to bind to (0 = OS-assigned random port)",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Manifest reading
# ---------------------------------------------------------------------------


def _read_manifest() -> dict[str, Any]:
    """Read and parse the JSON manifest from stdin.

    Returns:
        Parsed manifest as a dict.

    Raises:
        ValueError: If stdin is empty (no content).
        json.JSONDecodeError: If stdin content is not valid JSON.
    """
    content = sys.stdin.read()
    if not content.strip():
        raise ValueError("Empty manifest: no content on stdin")
    return json.loads(content)


# ---------------------------------------------------------------------------
# Module loading
# ---------------------------------------------------------------------------


async def _load_module_object(module_path: Path, module_type: str) -> Any:
    """Load a Python module object from the given path.

    Adds module_path to sys.path, derives the package name from the directory
    name (replacing hyphens with underscores), and imports the package.
    mount() is NOT called here — real initialization happens via the Mount() RPC.

    Args:
        module_path: Local path to the module directory.
        module_type: Declared type ('tool' or 'provider') — informational.

    Returns:
        The imported Python module object.
    """
    # Ensure module_path is on sys.path
    path_str = str(module_path)
    if path_str not in sys.path:
        sys.path.append(path_str)

    # Derive package name: directory basename with hyphens → underscores
    package_name = module_path.name.replace("-", "_")
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", package_name):
        raise ValueError(
            f"Invalid package name '{package_name}' derived from module path "
            f"'{module_path.name}'. Package names must be valid Python identifiers."
        )

    # Import the package — mount() is intentionally not called here.
    # The real initialization comes from the Mount() RPC with coordinator=None and config.
    return importlib.import_module(package_name)


# ---------------------------------------------------------------------------
# Type verification
# ---------------------------------------------------------------------------


def _verify_module_type(module_obj: Any, declared_type: str) -> None:
    """Verify the module has the expected entry point for the declared type.

    For v1: checks structural conformance (mount callable exists).
    Instance type verification happens after Mount() RPC is called.

    Args:
        module_obj: The loaded module object to verify.
        declared_type: The declared type from the manifest ('tool' or 'provider') —
                       informational only at this stage; instance verification is
                       deferred to the Mount() RPC call.

    Raises:
        TypeError: If module_obj does not expose a callable 'mount' entry point.
    """
    mount_fn = getattr(module_obj, "mount", None)
    if mount_fn is None or not callable(mount_fn):
        raise TypeError(
            f"Module does not have a callable 'mount' entry point. "
            f"Amplifier modules must expose a top-level mount(coordinator, config) function. "
            f"Declared type: {declared_type}"
        )
    # v1: we trust the manifest's declared_type; instance verification deferred to Mount() RPC


# ---------------------------------------------------------------------------
# Server creation
# ---------------------------------------------------------------------------


async def _create_server(
    module_obj: Any, module_type: str, port: int
) -> tuple[Any, int]:
    """Create and start the gRPC server for the given module.

    Args:
        module_obj: The loaded and verified module object.
        module_type: The module type ('tool' or 'provider').
        port: Port to bind to (0 = OS-assigned).

    Returns:
        Tuple of (server, actual_port).
    """
    try:
        import grpc
        import grpc.aio
    except ImportError:
        raise ImportError(
            "grpcio is required for the gRPC adapter. "
            "Install it with: pip install amplifier-foundation[grpc-adapter]"
        )

    from amplifier_core._grpc_gen import amplifier_module_pb2_grpc as pb2_grpc

    from amplifier_foundation.grpc_adapter.services import (
        LifecycleServiceAdapter,
        ProviderServiceAdapter,
        ToolServiceAdapter,
    )

    # v1 trust model: localhost port isolation is the sole access gate.
    # No TLS or token-based auth is configured on the gRPC server itself.
    # AMPLIFIER_AUTH_TOKEN is available in os.environ for loaded modules
    # to use directly when making outbound calls to the kernel.
    server = grpc.aio.server()

    # Register type-specific servicer
    if module_type == "tool":
        pb2_grpc.add_ToolServiceServicer_to_server(
            ToolServiceAdapter(module_obj), server
        )
    elif module_type == "provider":
        pb2_grpc.add_ProviderServiceServicer_to_server(
            ProviderServiceAdapter(module_obj), server
        )

    # Always register lifecycle servicer
    pb2_grpc.add_ModuleLifecycleServicer_to_server(
        LifecycleServiceAdapter(module_obj), server
    )

    # Bind to localhost only
    listen_addr = f"127.0.0.1:{port}"
    actual_port = server.add_insecure_port(listen_addr)
    await server.start()

    return server, actual_port


# ---------------------------------------------------------------------------
# Main async entry point
# ---------------------------------------------------------------------------


async def _run() -> None:
    """Main async flow: parse args, read manifest, load module, serve gRPC."""
    # 1. Parse arguments
    args = _parse_args()

    # 2. Read manifest from stdin
    try:
        manifest = _read_manifest()
    except json.JSONDecodeError as e:
        print(f"ERROR:Invalid JSON manifest: {e}", flush=True)
        sys.exit(1)
    except ValueError as e:
        print(f"ERROR:{e}", flush=True)
        sys.exit(1)

    # 3. Validate required fields
    module_name = manifest.get("module")
    module_type = manifest.get("type")

    if not module_name:
        print("ERROR:Missing required field 'module' in manifest", flush=True)
        sys.exit(1)

    if not module_type:
        print("ERROR:Missing required field 'type' in manifest", flush=True)
        sys.exit(1)

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

        # 8. Create gRPC server
        try:
            server, actual_port = await _create_server(
                module_obj, module_type, args.port
            )
        except Exception as e:
            sys.stdout = real_stdout
            print(f"ERROR:Failed to create gRPC server: {e}", flush=True)
            sys.exit(1)

    finally:
        # Always restore stdout
        sys.stdout = real_stdout

    # 9. Signal readiness
    print(f"READY:{actual_port}", flush=True)

    # 10. Install signal handlers for graceful shutdown
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _set_shutdown() -> None:
        shutdown_event.set()

    try:
        loop.add_signal_handler(signal.SIGTERM, _set_shutdown)
        loop.add_signal_handler(signal.SIGINT, _set_shutdown)
    except (NotImplementedError, ValueError):
        # Signal handlers not supported on this platform (e.g., Windows)
        pass

    # 11. Wait for shutdown signal
    await shutdown_event.wait()

    # 12. Graceful server stop (5-second grace period)
    await server.stop(grace=5)


# ---------------------------------------------------------------------------
# Sync entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Synchronous entry point. Configures logging to stderr and runs _run()."""
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    asyncio.run(_run())


if __name__ == "__main__":
    main()
