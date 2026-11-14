# Participating cooperative tool:post bridge v1

This Foundation helper preserves the existing Core 2.0.2 registry/processing
execution and exposes the structural `session.media.tool_post` capability. It
adds no seventh operation to `experimental.media.v1`, publishes no refs, and
acknowledges neither canonical persistence nor model transport.

## Guarantee and limits

The parent accepted implementation and synthetic qualification of an explicitly
participating **cooperative** graph. A positive witness is conditional on those
participants registering through their supplied views and not mutating/emitting
through retained raw registry paths. This is not malicious-Python isolation,
a universal third-party hook guarantee, source approval, or live adoption.

The original counterexample remains: a raw callback can register, self-unregister
and throw before the first covered callback. Core logs and swallows its failure;
public names before/after and all known callbacks can still match. No public
Core API exposes that hidden native snapshot/failure generation. The construction
receipt therefore records actual participating lifecycle/callbacks, not an
impossible guarantee about arbitrary raw/native intervention. Unknown graphs
remain incomplete. A source label, report, Boolean, hash, or copied receipt never
creates qualification. Do not claim a swallowed uncooperative error was witnessed.

Final Astra/Fable exact-source review and actual host composition checks remain
release gates, not a renewed generic permission requirement for ordinary repairs.
No Core edits, live host enabling/restart, Mac operations or DTU launches belong
to this worker. Prior f0d7149/default-false proofs remain historical evidence, not
a positive result for this implementation.

## Actual host construction

```python
from amplifier_foundation.session.media_hook_bridge import install_media_hook_bridge

# In before_initialize, BEFORE bind_media's policy hooks or other host callbacks:
bridge = install_media_hook_bridge(session.coordinator)
graph = bridge.begin_participating_graph(
    session,
    owner="cli.interactive",  # actual named host variant
    participants=("loop-basic", "context-simple", "provider-actual", "tool-actual",
                  "host.incremental_save"),
)
# Existing initialization loads modules, invokes mounts and on_session_ready.
# Do not replace session.coordinator, native identity checks, or mount wrappers.
await session.initialize()

# Late hooks use a persistent explicitly owned registration view:
registration = graph.participant("host.incremental_save")
registration.hooks.register("tool:post", actual_autosave, priority=900,
                            name="incremental-save")
# bridge.participant(name) is the public bound-graph lookup for later helpers.

# Only AFTER every actual late registration, before the first tool pipeline:
receipt = graph.seal()
assert bridge.validate_receipt(receipt)
```

This example is schematic, not a blanket accepted source inventory. Actual
selected orchestrator/context/provider/tool/hook module IDs must be explicitly
selected by the host's cooperative composition owner, not auto-approved merely
because they occur in configuration. The helper checks that each actual load's
module/config/source hint matches its detached initial plan, records returned
mount/ready callback identities and completion, and retains persistent module
participant views. Module IDs may originate in string/dictionary session config.
A cached mount/ready path must still traverse the decorated public loader.

`session` must be the actual fresh Core session bound to this coordinator, before
initialization. Calling this API on an arbitrary initialized coordinator, with a
user verifier, or with a caller-created receipt is unsupported. The helper uses
existing public Core type objects; currently qualified positive ABI is exactly
Core 2.0.2. Other ABI versions refuse positive construction explicitly.

An owner and distinct participant tuple are required. Every declared participant
must actually enter construction; every active tool:post registration must retain
its participating owner, original callable, installed observer callable and exact
registration identity. `seal()` requires actual successful initialization,
completed ordered load/mount/ready plan, prescribed late owners and consistent
native registry. It issues one opaque identity-only token. Its record retains
strong references to session/bridge/registry/loader/native endpoints/mounted
modules/configuration/actual callback identities and generation; no id()-only
registry or GC/ABA identity test is used.

`bridge.graph_receipt` returns that issuer-owned token. `validate_receipt(token)`
checks only that exact issuer/generation and returns False for foreign/copied/
reconstructed tokens. `receipt_status(token)` returns scalar valid/reason,
qualified Core ABI, owner, generation and conditional guarantee. These inspect a
real construction; they do not let reports or descriptors issue one.

Core's session.coordinator/hooks/register surfaces are read-only. The installer
uses only the public coordinator.loader setter, delegating resolution/loading and
passing owner-specific views into actual mounts/ready callbacks. It does not patch
Core, replace session.coordinator, or unwrap private callbacks. `get("hooks")`
returns the same registration view. The views delegate non-registration operations
to the actual coordinator, preserving host wrapper chains and capabilities.

## Sealing, invalidation and normal transitions

The lifecycle is OPEN -> SEALED -> INVALID. Sealing is a final explicit host step,
not the final module mount or automatic ready callback. Failed/skipped/unexpected
loads, repeated or failed ready/mount work, missing participants and unowned/raw
post handlers cannot seal. Default installed-but-unsealed paths execute ordinary
hooks without qualifying media.

Root CLI sealing is after saved settings, incremental/json trace/goal observers;
root Unified sealing is after Worker activity/app access/native/naming/controls
post observers, not its earlier media binding call. Finite/custom children need
their own explicit final phase; do not assume prepare_execution runs. Raw spawn,
retry and resumed coordinators each get a fresh construction and receipt.
RuntimeControls' independent tool_result ingress cannot borrow the ordinary
loop's witness unless its own actual effective result is honored by its adapter.

Only tool:post registration/removal advances the media registry generation.
Ordinary non-post per-turn badge, completion/provider-request observers and
cleanup registration/removal do not invalidate the receipt just by changing an
unrelated event. Module loading, ready work, actual config, mounted tool/provider/
context/orchestrator identity and captured endpoint changes are relevant and
invalidate. A post mutation invalidates before forwarding; restoring equal names
or removing the transient handler does not revive the receipt.

Normal settings/mode changes must reconstruct the qualifying generation rather
than permanently disable a functioning feature or silently reseal the old one:

```python
new_graph = graph.reconstruct(fresh_actual_session, participants=new_participants)
# Same named owner; old receipt invalidated, new actual session/bridge/generation.
# Host restores canonical rows/settings/authority through its existing owners,
# initializes modules and installs actual late observers; no tool re-execution.
new_receipt = new_graph.seal()
```

This is issuer-owned **distinct fresh session** reconstruction, not resealing the
old receipt or replaying tool effects. Hosts currently changing modes/providers
on the same coordinator need an actual coordinated reconstruction lifecycle;
this method alone does not prove those host entrypoints integrated. Stable normal
provider/tool instrumentation must be installed before sealing and reused across
turns; repeatedly replacing wrappers invalidates exact endpoint identity. Host
owners qualify first/second-turn behavior and transition restoration separately.

## Structural boundary and effective result

```python
settled = await bridge(event, hooks=current_hooks, coordinator=current_coordinator)
# {hook_result: actual_processed_HookResult,
#  result: final_effective_value_including_None_if_complete_else_None,
#  complete: bool}
```

The ABI is unchanged: no tool_name/hook_name keyword; event carries the actual
tool_name/tool_call_id/result. Omitted args, actual native roots or participant
views select captured native emit/process. Unknown/mixed/revoked arbitrary wrapper
objects refuse before effects. Known nested dispatcher identities enroll below.
No arbitrary supplied .emit/.process_hook_result methods execute as proof.

The existing native emit("tool:post", enriched_event) executes exactly once,
followed by captured native process_hook_result(actual, "tool:post", tool_name)
exactly once. Native priority/deny/injection/approval ordering is preserved.
Receipt identity/generation is captured before emission and checked around emit
and process. All actual participating callbacks are observed, not a terminal
last-priority sentinel. Their identity/order/error/serialization/deny-prefix
witness supplements the graph receipt. A positive result requires BOTH.

- data.result is authoritative by presence, including null, string/list or output
  removal. Modify without result clears the effective value.
- Continue without result retains the current effective value, never the original
  staged refs after another callback removed them.
- In-place result edits/deletion followed by Continue normalize into the existing
  native Modify action once, preserving flags and letting later hooks see edits.
- In-place edits plus injection/approval cannot be represented losslessly by one
  Core action: preserve the special action and return incomplete media rather
  than reorder effects. Native processing may consume injection into Continue.
- Deny has no promotable result. Actual native short-circuit prefixes are known;
  unvisited suffix callbacks are not falsely counted as executed.
- Callback errors, malformed/non-HookResult returns, lossy serialization,
  missing/extra identities, generation changes or failed receipt checks return
  complete:false/result:null; later outer callbacks cannot heal that failure.
- An actual participating outer redaction is part of the same native chain and
  wins before publication. Arbitrary outer transforms are not enrolled by labels.
- Mid-emission revocation/invalidation does not roll back or replay effects; the
  existing actual result is still processed once. Cancellation propagates and
  releases witness/route scopes. Native approval may outlive caller cancellation,
  matching baseline Core; do not claim its underlying operation was cancelled.

Complete=True is not publication, canonical ACK or provider-send authority.
The loop must validate surviving source-bound refs, publish assets, append the
settled canonical rows and acknowledge their durable checkpoint. Integration
checks must equate final effective refs, actual canonical ACK refs and private
candidate refs, with negative alteration-between checks. No report creates ACK.
Private `_observe` remains a partial mechanism test seam, not runtime acceptance.

## Explicit streaming attributed route

```python
lease = bridge.enroll_attributed_route(
    hooks=actual_attributed_hooks, coordinator=actual_attributed_coordinator,
    parent_hooks=actual_parent_hooks, parent_coordinator=actual_parent_coordinator,
    source=actual_dispatcher_source, observation=dispatcher_owned_observation,
    source_identity={"component": "loop-streaming", "adapter": "attributed-v1",
                     "contract": 1, "module": "amplifier_module_loop_streaming.dispatch"},
)
with lease:
    ...  # whole existing nested execute/stage/publish; finally revoke
```

The exact fixed descriptor is compatibility data, never source provenance or
retention authority. Source has exactly six keys: session_id/run_id/
parent_tool_call_id/tool_call_id/tool_name are nonempty strings, request_id is
nonempty string or null. Session/call/name bind the actual event. Host/dispatcher
owners still authorize actual run/parent/child admission. Source is detached.

Tokens are object identities, no private fields/attributes/equality calls.
Parent lease generations, not token re-lookup, pin ancestry; revocation cannot be
undone by reusing a parent's token objects. ContextVar active scope is checked
before/after awaits; parallel calls and nested structural emissions isolate frames.
Ordinary facade emit/collect does not join an outer witness.

Hook and coordinator chains are separate, matching actual dispatcher behavior:
current->parent hook enrichment means parent attribution overwrites inner fields;
post observations unwind parent->current before processing, denial observations
unwind the actual coordinator chain after processing. post_seen/post_result are
observer compatibility only. Emission-stage identity/revision checks run before
non-null post_result; later processing invalidation clears it. Null then injection
stays null. The dispatcher uses the settled result for actual success/output.
Non-post error/suspension/cancellation statuses remain their existing owners.

## Verification and retained limitations

Maintained tests use actual Core 2.0.2 RustSession initialization, loader mount/
ready, Rust registry and processing. Positive public ABI tests cover effective
null/in-place/string/list/deny, outer redaction, callback error/no later reattach,
receipt forgery, failure/missing ownership, config/loader/mount/callback identity
mutation, unrelated non-post lifecycle, nested enrolled route, parallel/cancel and
issuer-owned fresh reconstruction. Earlier partial tests remain labelled partial.

This is focused source evidence, not live installation, every host variant,
all capable model pixel roundtrips, native third-party parity or final review.
Actual owners pair immutable Foundation/loop/host candidates; their failures,
skips and unknowns remain distinct. No paid inference authority is added here.

This bridge branch originally depended on exact F1
31766b42efe71920386975cf10d136b38b85fc14. Original CI43539041037 six Ubuntu/Windows
collection failures (missing PIL at tests/test_session_media.py:14) remain failed;
separate installed/shared-source lanes were not full suite success. Owning newer
F1 assembly supplies its accepted dependency repair; do not rewrite this history
or borrow unreviewed moving-main inputs. This worker changes only its three files.
