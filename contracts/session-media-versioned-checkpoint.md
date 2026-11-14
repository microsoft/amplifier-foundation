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
The store's old hardcoded `legacy-checkpoint-backed` output label is unchanged
in this scoped delta; version-aware label integration belongs to assembly.

Precommit asset admission remains a separate host obligation. The current
writer's zero-argument admission callback does not attest to its exact input.
This ACK proves actual selected canonical reachability, joined by the media
store to actual published assets; it cannot retroactively prove publication
ordering or replace production fencing/asset admission qualification.
