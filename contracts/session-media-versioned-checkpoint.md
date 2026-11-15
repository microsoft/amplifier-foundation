# Versioned transcript media checkpoint acceptance

This opt-in POSIX seam joins an actual `TranscriptWriter` current committed
prefix to existing `SessionMediaStore` acceptance. It neither activates a host
writer nor migrates a session. No Core, provider, live-data or configuration
behavior is changed.

```python
from amplifier_foundation.session.versioned_checkpoint import (
    acknowledge_versioned_checkpoint,
    verified_versioned_checkpoint_refs,
)

# Construct the store with checkpoint_verifier=verified_versioned_checkpoint_refs.
# writer has the host's real all-writer quiescence fence, not a journal substitute.
# ack is the result of the host's completed append/rewrite/initialization.
with acknowledge_versioned_checkpoint(
    writer, session_id=session_id, expected=ack,
) as receipt:
    acceptance = store.acknowledge_checkpoint(receipt)
# Report durable acceptance ONLY here, after successful context exit.
```

`expected` must have exact type `CommitAck` or `CommittedPrefix`. It only selects
the actual current prefix; the unsealed dataclass cannot mint authority. A
historical reconciled ACK, dictionary, Boolean, lookalike or writer subclass is
not a substitute. Actual source/hash/header/message-count/final-LF verification
is performed under the exact writer's `_owned` fence. The existing `_settle`
finishes durability for the **selected current operation only**. It may create
its missing `published.json`; it never calls admission, append, tool execution,
rewrite or migration and never truncates an existing tail.

Writer-created closure is mandatory: matching current/result/publication,
operation intent, migration journal, ready marker and both legacy directory
fences. The selected intent's exact schema/predecessor/result bounds and the
journal's complete source-selection schema are checked before settlement.
Reader-only fixtures cannot establish this closure. Ancestor directory
identities and each control file's identity stamp and raw bytes are pinned.
Files must be no-follow, unaliased owner-private regular files. Body verification
is capped at `committed_bytes`; existing uncommitted tails are ignored and cannot
add references. Any observed post-pin body mutation, including tail-only writes,
requires fresh acknowledgement. Identical-byte pointer/file replacement or
observed ancestor replacement also invalidates the prior receipt. These are
conservative observed identity checks, not a claim to detect invisible
adversarial mutation history between observations.

`VersionedCheckpointReceipt.verify()` exposes only bounded JSON facts:
`session_id`, `generation`, `body`, `sequence`, `committed_bytes`, `message_count`,
`sha256`, `operation_id`, and `kind="versioned-checkpoint-backed-v1"`. The exact
receipt type, issuer seal and process-local HMAC bind all public fields, private
path/control/body witnesses, issuing PID and object/lifecycle identities. An
issuer-owned weak registry makes exception revocation irreversible by restoring
receipt-carried fields; a forked child must obtain its own owned acknowledgement.
Copies,
serialization, dictionaries and altered fields do not recreate authority.
Successful context exit leaves the receipt valid for later source-checked
resolution; every context exception, including final ownership/directory/fence
exit failure, revokes it. A resumed process must acknowledge the current actual
source again. Serialized media-record checkpoint facts alone are insufficient.

`iter_refs()` and `verified_versioned_checkpoint_refs()` stream only top-level
canonical row `metadata["experimental.media.v1"]["refs"]`. Absent/null metadata
has no refs; malformed present metadata/namespace/refs refuses. The ordinary
refs-only F1 envelope is accepted; an optional namespace version must be exact
integer 1. Nested metadata, content and JSON-looking text are never promoted.
Duplicate keys and malformed JSON refuse through the owner transducer. Each ref
must satisfy the unchanged F1 PNG/JPEG `validate_blob_ref` shape and have the
exact owning session ID; adopted provenance may retain its original session.

The scanner never decodes whole messages, metadata, ref arrays or arbitrary huge
strings. It uses the existing 64KiB scanner, bounded object/key/depth/primitive
limits and at most one decoded ref (maximum encoded ref size 64KiB). Oversized
individual control refs refuse explicitly, without truncation. Verification and
reference exhaustion are honestly O(committed history), with a disk-backed
source offset index, not warm O(1) append evidence.

Exhaust the iterator: final source verification occurs **after the last yield**.
The existing store's disk spool exhausts before binding any acceptance, and its
resolution verifier exhausts again even after finding the wanted ref. Use this
V2-only callback or an explicit exact-type legacy/V2 dispatcher; never duck-type
serialized receipt reports. Existing legacy/candidate ACK paths are unchanged.
The assembled F1 `checkpoint.verified_media_checkpoint_refs` is an explicit
opt-in exact-type dispatcher for legacy and V2 receipts; the existing
`verified_checkpoint_refs` stays legacy-only. Neither callback accepts a report,
dictionary, Boolean or lookalike as receipt authority. The assembled store emits
`versioned-checkpoint-backed` only for an exact local `VersionedCheckpointReceipt`
after verification and complete configured-verifier exhaustion; legacy/custom
host verifiers retain `legacy-checkpoint-backed`. The serialized `kind` never
selects this durability label. This source integration does not establish actual
host lifetime ownership or installed-composition acceptance.

Precommit asset admission remains a separate host obligation. The current
writer's zero-argument admission callback does not attest to its exact input.
This ACK proves actual selected canonical reachability, joined by the media
store to actual published assets; it cannot retroactively prove publication
ordering or replace production fencing/asset admission qualification.

## Prospective source-lifetime custody

`session.transcript_lifetime.VersionedSessionBinding` is an opt-in,
host-neutral owner for **new IDs only**, initially qualified on Linux with
`renameat2(RENAME_NOREPLACE)`. It does not convert existing sessions, activate
configuration, or establish unknown historical/open-FD writer quiescence.
CLI and Unified must use this same mechanism before their first native
transcript open; runtime wiring and installed qualification remain host work.

The parent sessions directory must already be private, owned and free of
symlinked ancestors. `create_new(path, session_id=..., operation_id=..., admit=...)`
creates a private unpredictable staging directory, takes a distinct persistent
FD lease, and fsyncs path/session/inode/token enrollment. Foundation alone
initializes the transcript. Only after successful initialization and both
nonempty legacy directory fences does no-replace rename publish the selected
ID. A preexisting target or publication race refuses without overwriting it.
The final writer is freshly bound to the published path; staging source
witnesses or media receipts are not carried across the rename.

Failures retain preparation evidence. An unpublished staging directory is not
a resumable ID and must not be renamed or reinitialized to erase uncertainty.
`resume(path, session_id=..., expected=exact_prefix)` opens the existing lease
without creation, requires exact enrolled path/root/lease identity and verifies
the actual committed source including both Foundation fences. Arbitrary existing
T2 directories are not enrolled. After explicit `close`, a fresh owner may
reacquire this enrolled source. The lease file is never unlinked at retirement.

`append(settled_suffix, expected=..., operation_id=..., admit=..., media_store=...)`
requires actual producer-settled suffix intent. `rewrite` (also named `commit`)
is an explicit full-canonical-context replacement, O(history), not a guessed
append slice. Calls freeze the selected rows, bind asset validation to those
rows and the exact owned session media root, and forward to Foundation's sole
format writer. Precommit admission must publish/fsync every reachable asset;
the adapter validates canonical media refs before Foundation commits.
Published identity alone does not establish durable eligibility: under the
exact owning store lock, admission requires its actual retained record to have
`retention="durable"` and accepted publication state. Request-only observations
refuse before canonical publication, even when legitimately published. Admission
also verifies physical original asset bytes using the store's existing
private-file/hash rules, except for the same expired committed or explicitly
adopted expired metadata-only history cases accepted by checkpoint validation.
It neither hydrates expired history nor renews provenance or model authority.
Malformed present metadata or media envelopes refuse before publication:
metadata may be absent/null or an object, while a present media namespace must
be a strict refs envelope (optional exact integer `v=1`, required array `refs`).
False/array/scalar metadata and a present null namespace are not absence.
An empty refs array is structurally valid with the owned media store. A supplied
store must be writable and have its checkpoint verifier configured before the
canonical mutation; typed acknowledgement remains mandatory afterward.

When a store is supplied, exact typed acknowledgement occurs inside
`acknowledge_versioned_checkpoint` while custody is held. `last_receipt` and
the write result become reportable only after successful acknowledgement and
fence exit. A late error is not an effect-free refusal: retain it as uncertain,
do not report durable acceptance, and do not replay tools or allocate a new
operation. `reconcile` requires the original operation/predecessor/kind and
canonical JSONL digest, delegating outcome settlement to Foundation without
replaying admission or rewriting. No committed effect and unknown effect stay
distinct.

The binding holds custody while idle and serializes complete write, ACK,
reconcile and read operations. Nested mutation callbacks refuse rather than
deadlocking. Namespace/enrollment/lease identities and creator PID are checked
at ownership entry and exit. Forked children invalidate and close inherited
FDs without unlocking the parent's lease. `close()` drains a physical call
before releasing it; new admissions stop once close starts. Async callers use
`await binding.call(method, ...)` and `await binding.aclose()` so cancellation
joins the executor call and final exits before returning cancellation.
For host-dependent metadata/control/CAS publication following a canonical write,
`with binding.publication() as check:` retains that same physical owner until
the auxiliary effects and final check settle; it is not a grant to another
transcript writer. Nested publication and retirement from inside that scope
refuse. A host must disclose a committed transcript if a later auxiliary effect
fails, rather than describe the failure as effect-free.

This is cooperative exclusion for the enrolled prospective cohort plus
supported legacy entry-filename fences, not protection from arbitrary
same-UID filesystem mutation, proof about every historical writer, or
permission to migrate an old root. Real root/child/resume caller wiring,
canonical context intent, media transport and installed composition require
their own affected qualification.

## Prepared serialized-candidate data lens

`scan_serialized_refs(stream, *, prefix: CommittedPrefix, check)` is the shared
bounded media scanner for the format owner's `PreparedTranscriptCandidate`.
It consumes an exact header-bearing V2 snapshot: `prefix.sha256` and
`committed_bytes` describe the raw header plus rows, not the prepared canonical
rows-only rewrite digest. It requires exact physical EOF, unlike a committed
receipt which ignores its preexisting uncommitted tail.

This helper yields **data only**, not receipt, publication, admission, or source
adoption authority. It is not a store `checkpoint_verifier`. The format owner
must wrap it inside the actual issued prepared object's `open_verified()`,
using that object's state-bound `candidate_prefix` and `check` guard. Exact
issued-object membership, PID, lifetime, source ownership and revocation remain
with that owner. Its preparation context must span write and typed ACK; report
only after the preparation context also exits successfully. No duck refs query,
raw prefix/digest tuple, or converter database can substitute for that binding.
Use a method-local import from the writer to avoid a reverse top-level cycle.

The helper checks complete selected hash/header/role/schema/count/final-LF
semantics before exposing refs, repeats integrity verification after exhaustion,
and checks the supplied guard around bounded I/O and before/after each yielded
ref. Exhaustion after the last yield is mandatory. The caller owns the stream;
the scanner does not close it and leaves its cursor unspecified.

Prepared precommit envelope checks preserve F1's refs-only form: metadata absent
or null and namespace absent mean no media. Other metadata must be an object;
a present namespace must be a non-null object with required array `refs` and
only optional `v`. If present, `v` must be exact integer 1. Unknown envelope keys
refuse in this prepared path. Existing receipt envelope compatibility is not
tightened: its prior unknown-key behavior remains unchanged. Both paths reuse
the same bounded top-level ref extraction and unchanged PNG/JPEG ref validation;
the outer owning session must match, while original provenance may differ.

All reads are explicit and at most 64KiB; no message, metadata, refs array or huge
scalar is decoded wholesale, and at most one <=64KiB ref is materialized. This
scan is O(serialized history), not a warm O(1) append or final integration claim.

### Bounded serialized candidate replacement

`binding.rewrite_serialized(candidate, *, candidate_prefix, candidate_check,
expected, operation_id, admit, media_store, on_prepared=None)` accepts the format
owner's header-bearing v2 candidate as an owned regular seekable binary stream.
It delegates preparation and publication to `transcript_writer`'s existing
`prepare_serialized_candidate` / `rewrite_prepared` APIs; it does not encode a
second format, strip headers, construct message dictionaries, replace fences,
or create another transcript journal. The ordinary `rewrite` remains an explicit
materializing full-context API, not the huge-candidate route.

The binding owns the preparation context, whose anonymous raw snapshot and
canonical-row spool are immutable and locally issued. Admission enumerates refs
from exactly those verified bytes through the canonical ref owner's data-only
`scan_serialized_refs` parser and exhausts final checks, then applies the same durable-record,
physical-asset and expiry checks described above. An exact owning media store
is required even for zero-ref candidates. Selector fields alone never mint
authority; the typed checkpoint still establishes actual selected reachability.

The optional synchronous `on_prepared(facts)` observer receives a detached,
bounded dictionary before any target writer invocation: `candidate_prefix`,
`canonical_sha256`, `canonical_bytes`, `message_count`, `expected`,
`operation_id`, and `kind="rewrite"`. A converter may durably record these facts
in its existing operation journal. They are not a media/source authority token,
and changing them cannot change the prepared bytes. The canonical digest covers
message JSONL excluding the header, matching existing `reconcile(kind="rewrite")`
rather than the candidate's raw header-inclusive hash. Observer failure leaves
the target untouched; callers retain their own journal and do not infer replay.

The exact predecessor, operation and canonical digest are retained before
publication. Store acceptance occurs inside the typed acknowledgement context,
and preparation's final exit is settled inside that same context so a late
candidate-exit failure revokes a retained receipt. Only after preparation,
acknowledgement and ownership exits succeed are the result/receipt exposed and
the binding's expected cursor advanced. Late failures leave an uncertain
original intent; recovery uses the existing exact-digest reconciliation without
reopening tools, rewriting the candidate, or allocating another operation.
The caller must additionally settle its outer converter-owned candidate context
before reporting its overall conversion result. `candidate_check` pins candidate
artifacts and live custody; it must not demand the old target descriptor after
a legitimate canonical CAS.

Async serialized application uses `binding.call("rewrite_serialized", ...)`;
cancellation joins the physical read/write and all final exits before its stream
or owner may be closed. Preparation, reference scanning, typed acknowledgement
and rewrite remain O(history) I/O, with anonymous disk spools and bounded Python
memory even for one huge row; this is not an O(page), constant-disk, or RSS claim.