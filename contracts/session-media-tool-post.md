# Host-owned tool:post bridge v1

**Draft, activation closed.** This source delivers a registration facade, the
single-execution structural callback, partial callback-witness mechanism and
reproducible public-Core boundary. It does **not** deliver a qualified positive
media path: the public callback currently always returns complete:false and
result:null. The forward-media outcome remains pending, not completed by this
safe fallback. Do not call the private `_observe` test seam from a runtime module.

This helper supplements, and does not add a seventh operation to,
`experimental.media.v1`. It neither publishes media nor acknowledges persistence.

## Public host installation

```python
from amplifier_foundation.session.media_hook_bridge import install_media_hook_bridge

async def before_initialize(session):
    bridge = install_media_hook_bridge(session.coordinator)
    # Every subsequent host hook registration uses this view, including callbacks
    # installed after initialization. Other coordinator operations delegate.
    bridge.coordinator.hooks.register("tool:post", host_callback, name="host")
```

The installer returns a per-coordinator `MediaHookBridge`, with `.hooks` and
`.coordinator` registration views. `.coordinator.get("hooks")` returns the same
view. Existing tool:post registrations refuse installation: public names cannot
recover their callback identities. Repeated installation by the same owner returns
the existing bridge, not another wrapper.

Core 2.0.2's session.coordinator, coordinator.hooks and registry.register are
read-only. This helper does not patch them or replace Core execution. It decorates
the **public coordinator.loader** setter. Python module loading, mount callbacks
and on_session_ready callbacks receive the registration view; the original loader
still resolves and loads modules. Native registry emit, priority ordering, deny
short-circuiting, injection and approval processing remain their existing owners.
Hosts retain their chosen session, orchestrator, resolver and provider selection.

This route covers Python loader callbacks and explicit host registrations only.
A module that registers through session.coordinator instead of the supplied view,
a native module that bypasses Python mounting, or later host raw registrations
are **not qualified**. Hosts must establish the exact supported graph before
enabling ref-producing tools. Do not infer native coverage or activation authority
from handler names, an installed callback, a loop report, or one passing fixture.

`bridge.readiness()` returns `contract:1`, `registration_consistent`,
`authoritative:false`, `scope:"python-loader-and-explicit-host"`, and
`native_coverage:false`. This is a passive consistency diagnostic, not a completed
execution witness or permission to enable. A host may use separately accepted
exact-graph qualification for bootstrap; it must not run a synthetic tool:post
chain to manufacture readiness. Such a probe could repeat real hook effects.

## Structural loop callback

The installer registers the bridge at `session.media.tool_post`:

```python
boundary = coordinator.get_capability("session.media.tool_post")
settled = await boundary(event, hooks=current_hooks, coordinator=current_coordinator)
# settled == {"hook_result": processed_actual_hook_result,
#             "result": None,
#             "complete": False}  # Until exact-graph contract acceptance.
```

`event` is the existing actual tool event, including its `tool_name` and result.
No tool_name/hook_name keyword is accepted. Optional omitted hooks/coordinator use
the bound originals. Supplied AttributedHooks and AttributedCoordinator are used,
not discarded. Public session_id and capability identity bind the coordinator;
an invalid coordinator refuses **before** emission. The supplied hook wrapper
must delegate exactly once to this registry. It must not synthesize or retry a
chain. Per-callback identities/order, not labels alone, witness delegation.

The boundary awaits current_hooks.emit("tool:post", event) once, then awaits
current_coordinator.process_hook_result(actual, "tool:post", event["tool_name"])
once, positionally. It returns that actual **processed** HookResult. Injections
can be consumed into Continue and approvals can produce Deny. It preserves nested
attribution/status observations in the supplied processing wrapper. Errors and
cancellation propagate according to the existing owner; they never trigger replay.

## Partial effective-result mechanism and failure rules

Wrapped callbacks record their actual return before Core's lossy summary:

- data.result is authoritative by field presence, including null, strings/lists,
  replacement mappings and mappings without an output member.
- Modify without result clears the effective result; later injection cannot
  resurrect it from a pre-hook carrier.
- Continue without result retains the current effective result.
- In-place result edits/deletion followed by Continue are returned through Core's
  existing Modify action so later callbacks see the redaction. Other fields and
  flags remain intact. This is normalization of the one executed callback, not a
  second callback or another hook engine.
- In-place result edits combined with injection/approval cannot be represented
  losslessly as one Core action. Preserve that special action and processing,
  return complete:false/result:null rather than reordering or dropping effects.
- Deny has no promotable result. Known native short-circuit prefixes are observed;
  unvisited suffix callbacks are not claimed executed.
- Callback exceptions, malformed/non-HookResult returns, serialization failures,
  missing/extra callbacks, changed registration generation, loader replacement or
  inconsistent registry names make the witness incomplete. Core's existing
  swallow/log or malformed-result denial semantics remain unchanged.

Public returns always carry result:null; the caller must preserve ordinary
non-media error/status handling but cannot publish original staged refs or report
media readiness from that call. Even a future accepted positive witness would not
be staged origin validation, publication, canonical append/checkpoint or transport
proof. Those remain with the six-operation capability and respective owners.

A ContextVar carries each actual emission's witness. Nested structural calls and
parallel batches get independent state; completed/cancelled witnesses are marked
inactive and reset. Ordinary facade emits/collects do not join an outer witness.
The helper never calls emit_and_collect to recover information after execution.

## Qualification boundary

The independent adversarial review reproduced an invisible callback:

1. A supplied emitter registers a raw callback at priority -100, then invokes the
   actual native emit exactly once.
2. The raw callback unregisters itself and raises before the first covered
   callback. Core logs and swallows the error, then runs the covered callbacks.
3. Public names before/after and all covered callback identities/order match.
   Even checking names inside every covered callback misses this prefix.

Core's native execution snapshot, callback identities and swallowed-failure
generation are not exposed by its public API. A loader facade cannot enforce a
registry lock. Therefore this source deliberately does not turn the partial
witness into complete:true. Names, a Boolean, a report, or a source digest alone
must not manufacture that proof.

### Proposed exact-host route (not implemented or accepted)

The smallest no-Core route would explicitly narrow the guarantee to an **exact
reviewed cooperative graph**, not arbitrary Python/native modules. It needs:

- Captured bound native emit/process identities and actual supported Core type.
- All Python mounts, on_session_ready, host/runtime registration paths and the
  selected loader accounted for by their source owners, with no uncontrolled raw
  registration/emission/collection/unregistration path in the accepted graph.
- Direct real-registry/bridge-view routes accepted by object identity; no arbitrary
  supplied emitter may self-attest completeness.
- One explicitly enrolled nested dispatcher route for the exact hooks/coordinator
  pair, actual parent route/source/observation, bounded lifetime and revocation.
  Arguments become route identity tokens. The bridge performs the known
  attribution-before, captured native emit once, reverse post-observation,
  captured real processing once, and denial-observation order. It must preserve
  the actual existing nested wrapper semantics, without private unwrapping or a
  general arbitrary-transform callback engine.
- The current per-emission callback identity/order/error/serialization/generation
  witness remains necessary, but does not replace that graph qualification.

Loop architecture owner and the actual streaming dispatcher owner must settle the
minimal public enrollment descriptor. Parent plus requested Astra/Fable reviews
must accept this changed-risk boundary before activation. A source-bound verifier
is accepted graph evidence, **not** a runtime lock or malicious-Python isolation.
No such approval or verifier is claimed here. This dependency is not permission
to edit Core or permanently discard forward images.

`tests/test_media_hook_bridge.py` uses the mounted actual Core 2.0.2 Rust registry,
coordinator and finite RustSession initialization with a fixture loader. It covers
the effective-output table, exact emit/process counts, real injection/approval,
Python mount/ready and late host registration, priority/deny/unregistration,
raw/same-name bypass, callback errors/malformed results, serialization, nested and
parallel calls, cancellation and unsupported wrapper behavior. These are focused
source integration fixtures, not DTU, live host adoption, all-loop acceptance or
native module parity. Private `_observe` tests qualify only the partial callback
mechanism; public ABI tests enforce the hold and reproduce the invisible raw
callback. Dependent loop owners still need a future accepted positive full-loop
pair, not just these fallback tests.

Foundation base dependency for this change is exact
`31766b42efe71920386975cf10d136b38b85fc14`.
No dependent merge or activation is implied by publishing a draft bridge PR.
