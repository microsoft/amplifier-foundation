# Python gRPC Adapter Design

## Goal

Enable non-Python hosts (Rust, Go, C#, C++) to use Python Amplifier modules by bridging them via gRPC. The adapter wraps Python modules as gRPC services using the existing proto contracts, allowing any host with gRPC support to consume the entire Python module ecosystem.

## Background

The Amplifier kernel supports multiple transport types — native, WASM, and gRPC. Today, the Python host loads Python modules directly via import. But non-Python hosts (Rust, Go, C#, C++) cannot import Python modules. They see `Transport::Python` from `resolve_module()` and have no way to load it.

The gRPC transport is the universal bridge: every host language has mature gRPC libraries, and the kernel already defines proto contracts for all module types. The adapter turns any Python module into a gRPC service, filling the "can't load" cells in the host/module compatibility matrix:

| Host Language | Native Modules | Python Modules | WASM Modules | gRPC Modules |
|---|---|---|---|---|
| **Python** (today) | via PyO3 | Direct import | via WasmEngine | via gRPC bridges |
| **Rust** (future) | Direct | **via Adapter** | via WasmEngine | via gRPC bridges |
| **Go** (future) | via C ABI | **via Adapter** | via C ABI+WASM | via gRPC bridges |
| **C#** (future) | via P/Invoke | **via Adapter** | via P/Invoke+WASM | via gRPC bridges |
| **C++** (future) | Direct | **via Adapter** | via WASM runtime | via gRPC bridges |

This is enabling infrastructure — it must exist before any non-Python host can use the Python module ecosystem.

## Approach

**Option A: Host-responsible spawning** — the host spawns the adapter process, passes a manifest via stdin, reads `READY:<port>` from stdout, and connects via the existing `load_grpc_*()` functions. The kernel provides mechanisms (`resolve_module`, `load_grpc_*`); the host decides when and how to spawn. This follows the "mechanism not policy" kernel philosophy.

Rejected alternatives:

- **Option B (Kernel helper):** A Rust function like `spawn_python_adapter(module_name) -> (Child, endpoint)`. Would embed process management policy in the kernel — which `python` binary, what port range, health check timeout, etc. Violates mechanism-not-policy. Grows the kernel's surface area with `std::process` dependencies and platform-specific code. "What if Python isn't installed?" becomes a kernel error instead of a host policy decision. Fails the two-implementation rule (zero non-Python hosts exist yet to validate the abstraction).
- **Option C (Sidecar):** Adapter started separately by a process manager or orchestrator, not spawned per-module by the host. Requires external coordination and solves a deployment problem that doesn't exist yet. Natural evolution from A when production needs justify it.

## Testing Principle: Cross-Repo Integration

**amplifier-core has zero awareness** of foundation, CLI, or any higher-layer repo. It is the stability boundary that everything depends on.

**amplifier-foundation MUST test against amplifier-core** to ensure they work together. This is the lesson from the v1.2.3/v1.2.4 incidents where core's tests passed, foundation's tests passed, but they broke together because nobody tested the integration.

The adapter's Layer 2 integration tests validate that the adapter (foundation) correctly wraps Python modules and serves them via gRPC using the proto contracts defined in core. This is cross-repo integration testing by design.

## Architecture

### What the Adapter Is

The Python gRPC adapter is a CLI module inside `amplifier-foundation` at `amplifier_foundation/grpc_adapter/`. It's installed via an optional extra:

```bash
pip install amplifier-foundation[grpc-adapter]
```

And invoked as:

```bash
AMPLIFIER_AUTH_TOKEN=<uuid> AMPLIFIER_KERNEL_ENDPOINT=127.0.0.1:50050 \
  python -m amplifier_foundation.grpc_adapter --port 50051 < manifest.json
```

It does one thing: takes a manifest of a Python module spec via stdin, loads it using foundation's `ModuleActivator`, wraps it in a gRPC service implementation, and serves it on a gRPC server.

**Location:** `amplifier-foundation` (not `amplifier-core`) — because it depends on `ModuleActivator` which lives in foundation. Putting it in amplifier-core would create a circular dependency (`core → foundation → core`).

**v1 scope:** Tool + Provider + Lifecycle services only. Hook, Approval, Context, Orchestrator, and CompleteStreaming deferred to v2.

**What it is NOT:**

- Not a kernel component — the kernel never imports or calls it
- Not a daemon — exits on SIGTERM
- Not multi-module — one module per adapter process (v1). Multiple Python modules = multiple adapter processes, each on its own port
- Not a general-purpose tool — bridges specifically Python modules to gRPC

**Security model (v1):** Adapter binds to `127.0.0.1` only (localhost). No inbound authentication — security relies on OS-level port isolation. The adapter authenticates to the kernel's KernelService via `AMPLIFIER_AUTH_TOKEN`.

### Manifest Format

Single module, passed via stdin:

```json
{
  "module": "provider-anthropic",
  "type": "provider",
  "source": "git+https://...",
  "path": "/home/user/.amplifier/cache/provider-anthropic-abc123",
  "kernel_endpoint": "http://127.0.0.1:50050"
}
```

| Field | Required | Purpose |
|-------|----------|---------|
| `module` | Yes | Package name (matches `ModuleActivator`'s expected key) |
| `type` | Yes | Module type (`tool` or `provider` for v1) |
| `source` | If no `path` | Source URI for resolution |
| `path` | No | Pre-resolved local path — skips source resolution for fast start |
| `kernel_endpoint` | If module needs callbacks | For modules that call back into the kernel |

Auth token is passed via the `AMPLIFIER_AUTH_TOKEN` environment variable (not in manifest — temp files are world-readable).

### Components

**One module per adapter process.** Multiple Python modules = multiple adapter processes, each on its own port. This eliminates gRPC routing complexity and matches the proto's single-service-per-type design.

The adapter has **two files** in `amplifier_foundation/grpc_adapter/`:

#### `__main__.py` (~120 lines) — Entry point + module loading + server lifecycle

1. Parse CLI args (`--port`)
2. Read `AMPLIFIER_AUTH_TOKEN` and `AMPLIFIER_KERNEL_ENDPOINT` from env
3. Read manifest JSON from stdin (until EOF)
4. Redirect stdout→stderr during module activation (protect the READY protocol)
5. Call `ModuleActivator.activate()` for the module
6. Verify loaded object with `isinstance(obj, Protocol)` against declared type
7. If activation fails: print `ERROR:<message>` to stdout (with `flush=True`), exit non-zero
8. Create `grpc.aio.server()`, register the appropriate servicer
9. Bind port (0 = OS-assigned random port)
10. Print `READY:<port>` to stdout (with `flush=True`)
11. Handle SIGTERM for clean shutdown

#### `services.py` (~150 lines) — gRPC servicer classes

| Servicer | Wraps | Proto RPCs |
|----------|-------|------------|
| `ToolServiceAdapter` | Python `Tool` | `GetSpec`, `Execute` |
| `ProviderServiceAdapter` | Python `Provider` | `GetInfo`, `ListModels`, `Complete`, `ParseToolCalls` |
| `LifecycleServiceAdapter` | All modules | `Mount`, `HealthCheck`, `Cleanup` |

Each servicer: deserialize proto → call Python method (via `run_in_executor` for sync methods to avoid blocking the async event loop) → serialize proto response. Uses existing `_grpc_gen/` stubs from amplifier-core.

**Total v1:** ~270 lines across 2 files + `__init__.py`.

**Deferred to v2:** HookService, ApprovalService, ContextService, OrchestratorService, CompleteStreaming, multi-module-per-process routing.

**Dependencies:** `grpcio` (optional extra via `[grpc-adapter]`), `amplifier-core` (for proto stubs in `_grpc_gen/`), `amplifier-foundation` (for `ModuleActivator`). All available in foundation's dependency tree except `grpcio`.

## Data Flow

### Host-Adapter Lifecycle

```
1. Host calls resolve_module() for each module in the bundle
2. For each module with Transport::Python:
   a. Generate a single-module manifest JSON
   b. Spawn adapter, passing manifest via stdin:
      AMPLIFIER_AUTH_TOKEN=<token> \
      AMPLIFIER_KERNEL_ENDPOINT=127.0.0.1:<kernel_port> \
      python -m amplifier_foundation.grpc_adapter --port 0 < manifest.json
   c. Monitor both stdout AND process exit concurrently:
      - First complete line "READY:<port>" → connect
      - First complete line "ERROR:<message>" → log, mark module failed
      - Process exits before either line → immediate failure (check exit code + stderr)
      - No line after 30 seconds → timeout failure, kill process
   d. Connect: load_grpc_tool("127.0.0.1:<port>") → Arc<dyn Tool>
3. The resulting Arc<dyn Trait> is indistinguishable from a native module
4. On shutdown: SIGTERM → 5s → SIGKILL (POSIX); TerminateProcess (Windows)
```

### Stdout Protocol (Strict)

- The first complete newline-terminated line on stdout MUST be either `READY:<port>\n` or `ERROR:<message>\n`
- All print calls MUST use `flush=True` (Python block-buffers piped stdout by default — without flush, `READY` sits in the buffer and the host hangs forever)
- All other output goes to stderr
- The adapter redirects stdout→stderr during module activation to protect the protocol

### Environment Variables (Set by Host)

| Variable | Required | Purpose |
|----------|----------|---------|
| `AMPLIFIER_AUTH_TOKEN` | Yes | Shared secret for adapter→kernel KernelService calls (sent as `x-amplifier-token` gRPC metadata) |
| `AMPLIFIER_KERNEL_ENDPOINT` | If module needs callbacks | Host's KernelService gRPC address (e.g., `127.0.0.1:50050`) |

### Host-Side Auth Requirements

- Host generates the token at spawn time (one token per adapter process)
- Host configures its KernelService gRPC interceptor to validate the token
- Interceptor rejects calls with missing/mismatched token as `UNAUTHENTICATED`
- Token is tied to adapter lifetime, not session or request

### Host Stderr Handling

- Host SHOULD capture adapter stderr and route through its own logging with prefix `[adapter:<module_name>]`
- Adapter panics and tracebacks would otherwise be lost silently

## Error Handling

### Startup Failures

If activation fails for any reason (bad manifest, missing module, import error, type mismatch), the adapter:

1. Prints `ERROR:<message>` to stdout (with `flush=True`)
2. Exits with non-zero exit code

The host detects this by monitoring stdout and process exit concurrently. No partial serving — if activation fails, nothing is served.

### Runtime Failures

- Module raises exception during an RPC → gRPC error status with message
- Module returns `None` → handled gracefully (servicer returns appropriate empty/error response)
- Adapter process dies mid-session → host detects broken gRPC connection, marks module as failed

### v1 Limitation: No Mid-Session Restart

If the adapter dies mid-session, the module is failed. The host must tear down and recreate the full session. Automatic restart/reconnect is deferred to v2.

## Platform Support

| Platform | Status | Shutdown Behavior |
|----------|--------|-------------------|
| Linux | Supported | SIGTERM → 5s → SIGKILL |
| macOS | Supported | SIGTERM → 5s → SIGKILL |
| Windows | Out of scope (v1) | TerminateProcess (immediate kill) |

## Testing Strategy

### Layer 1: Unit Tests for Service Adapters (~20 tests)

Test each servicer class in isolation with in-suite mock module objects (defined in the adapter's own test suite — no imports from `amplifier_core.testing` to avoid cross-repo test dependency):

**ToolServiceAdapter:**
- GetSpec returns valid proto response
- Execute calls `tool.execute()`, returns result with correct `tool_call_id` correlation
- Execute with failing tool returns gRPC error status
- Execute with sync tool uses `run_in_executor`

**ProviderServiceAdapter:**
- GetInfo returns valid ProviderInfo
- ListModels with empty list returns valid empty response
- Complete calls `provider.complete()`, serializes correctly
- Complete with ThinkingBlock response preserves `signature` field through proto round-trip
- ParseToolCalls with multiple tool calls returns all
- ParseToolCalls with zero tool calls returns empty list

**LifecycleServiceAdapter:**
- Mount passes config to module (JSON-decode convention for complex values in `map<string,string>`)
- HealthCheck returns healthy
- Cleanup calls module cleanup

**Auth interceptor:**
- Missing `AMPLIFIER_AUTH_TOKEN` env var → defined behavior
- Inbound calls accepted without auth (localhost-only v1)

**Error handling:**
- Module raises exception → gRPC error status
- Module returns `None` → handled gracefully

### Layer 2: Integration Tests — Full Adapter Process (~8 tests)

Spawn adapter as subprocess with in-suite test fixtures (~20 lines each, satisfying Tool/Provider protocols via duck typing):

**Happy path:**
- Adapter prints `READY:<port>` to stdout
- gRPC client connects, calls GetSpec → valid response
- gRPC client calls Execute → correct result
- gRPC client calls HealthCheck → healthy
- SIGTERM → adapter exits within 5 seconds (bounded timeout in test)

**Failure paths:**
- Malformed JSON on stdin → `ERROR:<message>`, non-zero exit
- Missing required manifest fields → `ERROR:<message>`, non-zero exit
- Module fails to import → `ERROR:<message>`, non-zero exit
- Module doesn't satisfy declared protocol type → `ERROR:<message>`, non-zero exit

### Layer 3: E2E Test with Real Host

Deferred to when the first Rust/Go host exists.

### Test Fixtures

Minimal in-suite implementations of Tool and Provider protocols (~20 lines each). No cross-repo imports from amplifier-core test utilities. Satisfies structural protocols via duck typing.

### Explicitly Deferred (v1)

- Windows support
- CompleteStreaming
- Multi-module routing
- Mid-session restart recovery
- Concurrent Execute calls

## Open Questions

1. **`MountRequest.config` is `map<string,string>` not `dict[str,Any]`** — complex Python config values need JSON-encoding. Should we add a `config_json string` field to `MountRequest` in v2?

2. **Evolution to multi-module-per-process** — when performance evidence justifies it, the proto will need a routing field (`module_name` in requests). Design this before v2.

3. **DisplayService bridge** — not currently exported at crate root. If Go/C# hosts need display callbacks, it needs to be added.

4. **`CompleteWithProviderStreaming`** — currently simulated (sends full response as one stream element). Real token streaming requires `Provider::complete_stream()` on the trait.

## Dependency Graph

```
amplifier-core (kernel, proto stubs)
       ↑
amplifier-foundation (ModuleActivator, grpc_adapter)
       ↑
amplifier-grpc-adapter tests (Layer 2 integration tests against core's proto contracts)
```

amplifier-core never depends on foundation. Foundation tests against core. The adapter validates cross-repo integration by testing that Python modules served via gRPC satisfy the proto contracts defined in core.
