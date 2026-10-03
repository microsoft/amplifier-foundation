# Amplifier Operations

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
