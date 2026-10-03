# Amplifier Operations

## Purpose and public contract

This package owns operation journals, admitted input receipts, output cursors,
coordination delivery and durable intake-fence records. It follows Foundation's
[mechanism, not policy](../../README.md#philosophy) design and
[optional package boundary](../README.md). Applications install it independently
and select storage explicitly. Amplifier Unified is a consumer, not the authority
for this generic API.

Public mechanisms are [`OperationJournal`](src/amplifier_operations/journal.py),
[`OperationRequests`](src/amplifier_operations/requests.py),
[`coordination`](src/amplifier_operations/coordination.py), and
[`DurableIntakeFence`](src/amplifier_operations/quiescence.py). Callers supply
scoped operation events, actor/argument identities, cursor requests and trusted
lifecycle proofs. Returned records distinguish committed, refused and unknown
outcomes; output bounds expose truncation/gaps rather than inventing delivery.
The library validates proof bindings but does not authenticate the coordinator.
Process ownership, effect accounting, execution, permissions, UI and stop/restart
orchestration remain caller responsibilities. It grants no native writer lease.

## Contract acceptance

Run an explicit Python 3.11 or 3.13 interpreter from the Foundation checkout:
`python libraries/qualify.py operations --output /owned/qualification/path`.
The [installed-consumer harness](../qualify.py) builds wheel/sdist, installs the
wheel separately, checks runtime isolation and import state, and runs
[journal/cursor](tests/test_journal.py), [unknown admission](tests/test_requests.py)
and [durable lifecycle proof](tests/test_quiescence.py) checks. Public contract
changes also require declared consumer compatibility checks.
[Provenance](extraction.json), [landing](LANDING.json),
[original qualification](QUALIFICATION.json),
[intake-fence qualification](QUALIFICATION-QUIESCENCE.json) and
[service-stop qualification](QUALIFICATION-SERVICE-STOP.json) retain their exact
artifact scopes. They do not establish platform process observation, full host
coverage, browser/account acceptance or deployment.

Independent Python library extracted from Amplifier Unified. Standard-library runtime; no client, model, Amplifier runtime or host application imports. See extraction.json for provenance.

Run `uv sync` and `uv run pytest`. Durable SQLite receipts preserve uncertainty and never replay work. Applications own authentication, admission, execution and user presentation.

`OperationJournal.list/page` use indexed descending keyset pages; `read` bounds output bytes and explicitly reports source/retention gaps. `OperationRequests` (requests submodule) preserves admitted/unknown input receipts across restart, with actor/argument identity binding. This library never starts or controls a process itself. Default output retention remains10MB per operation with explicit truncation metadata; schedule/native history is not deleted by this library.

`amplifier_operations.quiescence.DurableIntakeFence` stores a held intake fence
and exact release receipts in a caller-selected private SQLite file. Importing it
creates no state. The caller must hold exclusive lifetime ownership of its process
partition, count admitted calls/background effects and detached work, and block new
mutations while `fence` is set. `acquire(context, pending=...)` refuses active work
without closing intake; otherwise it persists the fence before returning.

Context binds bounded opaque `fenceId`, `commandId`, `purpose`, `instanceId`, and
`dataScope` strings. `release` preserves an unknown outcome. A ready/unchanged
release requires an exact authenticated coordinator proof; the library validates
its binding but does not authenticate a `verified` flag from untrusted callers.
The explicit pre-effect admission-refused proof is for a trusted coordinator that
has not started maintenance. Ready identifies a replacement process; unchanged
identifies the original process. Exact release receipts are idempotent, persist
across restart, and forbid reusing a released fence. There is no TTL reopening,
automatic process retirement, historical receipt scanning or execution replay.

Consumers decide which reads are passive and which jobs belong to their owner.
The mechanism supplies neither application authorization nor an idle proof for
external/native/remote work. Its `calls` and `background` counters are process-local
and require the caller's exclusive lifetime lock; durable fences remain on disk.


### Optional persistent service lifecycle

`DurableIntakeFence.SERVICE_STOP_VERSION == 1` declares additive support for
`purpose: "service-stop"`. A consumer must check this marker before advertising a
service-stop participant. Older installed libraries are unavailable for this
purpose; composition must not stamp them as supported.

A service context additionally requires `serviceIdentity` containing exactly
`installationId`, `dataScope`, `ownerId`, `instanceId`, and `releaseDigest` bounded
strings. Its instance and data scope must equal the enclosing context. The entire
identity is copied into the durable fence. Other existing purpose values retain
their previous behavior.

Service release requires trusted proof with `kind: "service-lifecycle"`, exact
`expected` and `observed` identities, and all ordinary fence/command/scope/instance
bindings. `outcome: "ready"` also requires `serviceOutcome: "resumed"`, a distinct
observed instance, and `resumeCommandId`, `exitReceiptId`, `readyReceiptId`.
`outcome: "unchanged"` requires `serviceOutcome: "stop-refused"`, identical
identities, and `refusalReceiptId`. Installation, owner, release and data scope
cannot change. A generic update/recovery proof never releases a service fence.
The complete accepted service proof is retained: a changed exit/readiness/refusal
receipt cannot silently pass an exact retry after restart.

Pre-effect `admission-refused` can unwind a service lease only if that same
`DurableIntakeFence` instance newly acquired it. Reopening a stored fence or
calling `acquire` on an existing fence does not gain rollback authority. Reporting
`outcome: "unknown"` permanently retires that live rollback authority, including
within the original process. A
recovered held fence requires the full authenticated service proof. Repeating an
already completed exact release is a read of its durable receipt.

The library does not authenticate proof, observe process exit, signal processes,
launch replacements, or grant maintenance/native-admin permission. These remain
caller responsibilities; no PID or missing endpoint is considered evidence.
