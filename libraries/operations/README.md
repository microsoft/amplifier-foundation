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
creates no state. Only a missing database with no WAL, SHM, or rollback-journal
sidecars is initialized as new. Existing stores, including empty files, must
retain both authoritative tables and their required columns. Startup validates
through a WAL-aware read-only connection before opening a writer; missing schema
refuses startup rather than recreating empty fence or release history. Refusal
preserves the main database and pre-existing nonempty WAL; SQLite may update
derived SHM coordination state. Valid original two-table stores and normal crash
WAL recovery remain supported. This bounded check is not a general corruption
recovery mechanism, and callers must retain uncertain evidence for review. The caller must hold exclusive lifetime ownership of its process
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

### Optional held SQLite snapshot set

`amplifier_operations.snapshots.VERSION == 1` provides `capture_snapshot` and
`restore_snapshot` for an explicit census of at most sixteen absolute SQLite
paths. The caller supplies a synchronous `assert_held` callback covering every
real writer for the entire operation, exclusive lifetime ownership, authenticated
archive authority, and reviewed private-content permission. This callback is a
trusted seam, not a grant of authority from the library. A digest binds reviewed
bytes; it does not authenticate their origin.

Capture uses SQLite's backup API to include committed WAL rows in independently
validatable standalone images. It checks the database and WAL revisions across
the entire census, runs image integrity checks, and seals a bounded manifest with
image hashes, byte counts, and explicit missing stores. SQLite readers may update
transient shared-memory coordination; database and WAL authority remain unchanged.
Symlinks, shared hardlinks, unresolved journals, orphan WALs, concurrent revision
changes and exceeded byte/time budgets refuse completion. Defaults are 64 MiB
across images and thirty seconds. Parent namespace trust remains the caller's
responsibility; this mechanism does not discover external storage or freeze other
processes. Partial output is preserved for original-command inspection, and an
existing destination refuses capture replay.

Restore authenticates a caller-supplied manifest digest and checks all declared
images before creating a new inactive destination. It preserves exact SQLite
records, including durable fences and unknown outcomes. No existing destination
is overwritten, receipt is rewritten, process starts, fence clears, or work is
replayed. Activation, host identity, canonical native history, external stores,
retained artifacts and signed runtime-code completeness belong to the existing
application archive/recovery coordinator. A SQLite set alone is not a complete
product backup or restoration. The [snapshot tests](tests/test_snapshots.py)
exercise WAL preservation, independent restoration, retained fences, whole-census
change detection, tamper/overwrite refusal and bounded partial failures.

### Journal and request startup authority

`OperationJournal(path)` declares the standalone three-table profile:
`operations`, `operation_events`, and `operation_output`. These retain original
observations and output evidence; no canonical rebuild route is promised.
Applications using admitted input receipts must declare
`OperationJournal(path, require_requests=True)` before construction. This
combined profile also requires `operation_requests` before any writable open or
recovery. A present optional request table is validated even in the standalone
profile. Existing standalone journals are never guessed to need a three-to-four
table migration. For compatibility, `OperationRequests` may attach once to a
new standalone journal in the same object's lifetime; that permission is consumed
and cannot recreate a request table lost later in that lifetime.

Existing stores are checked through bounded fixed-name schema metadata and
zero-row table reads on a WAL-aware read-only connection. Missing, empty or
unsupported authority refuses startup before a writer; original main and
nonempty WAL are retained (derived SHM may change). Fresh creation requires the
main and all WAL/SHM/rollback sidecars to be absent, including dangling symlinks.
Only indexes can be rebuilt. Startup does not scan, hash, copy or replay history.
The existing per-journal thread lock and SQLite transactions remain mechanisms;
applications own process partitioning and lifecycle serialization. Schema checks
do not detect erased rows or a complete valid schema recreated externally.

Main and existing sidecars must be regular files without symlinks before SQLite
is called, so retained FIFOs cannot block startup. Callers serialize storage
lifecycle changes; this is not an adversarial VFS or path-swap guarantee.

### Optional pre-retirement distribution admission abort

`DurableIntakeFence.ADMISSION_ABORT_VERSION == 1` declares
`abort_admission(context_with_proof, owner_id=trusted_owner_id, pending=0)` and
`admission_abort_receipt(context, owner_id=trusted_owner_id)`. Consumers authenticate
the coordinator, retain exclusive lifetime process ownership, and count every active
call, background effect and pending effect. These synchronous methods neither stop
processes nor authorize a distribution update.

For `distribution-update`, `acquire` journals its original acquisition or busy
refusal atomically in the existing `releases` table under the original fence ID.
A typed `admission-v1` record retains context, acquisition, ordinary release and
abort proof/receipt. No new database or required table is introduced; original
legacy release rows remain readable. An identical busy-refused fence stays refused
after work finishes; only a separately authorized new fence can acquire. Legacy
held/refused attempts without the new journal cannot obtain abort authority.

Abort requires exactly the Host admission-abort proof fields `kind`, `verified`,
`purpose`, `receiptId`, `commandId`, `fenceId`, `instanceId`, `dataScope`, with kind
`distribution-admission-abort`, literal verified true, distribution-update purpose
and all four original IDs. Ordinary running/release proof is insufficient. The
caller must authenticate pre-retirement applicability; the mechanism checks bindings.
A missing fence never proves not-acquired: that result needs the exact durable busy
refusal. An acquired fence returns released only when it is held or an exact
pre-effect admission-refused rollback is retained. Other ordinary release outcomes
refuse this path. Active/background/pending work refuses settlement.

The exact seven-field receipt contains the four original IDs, configured `ownerId`,
`status` released or not-acquired, and a durable `receiptId`. The full proof and
receipt are committed atomically with fence deletion. Lost replies and restart read
the same receipt; changed proof, owner or identity refuses. Reading an old completed
abort never clears a newer fence, and an aborted input cannot acquire again.
Aggregate owners retain every attempted subowner identity and require conclusive
receipts from each; this mechanism alone cannot certify an aggregate.

[Admission abort tests](tests/test_admission_abort.py) cover acquisition/refusal
commit before lost reply, restart, changed proof, legacy evidence, active work,
pre-effect rollback and newer-fence preservation. Production authenticated Updates
proofs, owner adapters, full composition and live adoption require separate review.

Original conclusive admission evidence can settle even after a distinct newer
fence was acquired: an exact pre-effect rollback returns `released`, and an
original durable refusal returns `not-acquired`. The original abort receipt
never clears or changes that newer fence. Generic release evidence remains
insufficient for first admission-abort settlement.
