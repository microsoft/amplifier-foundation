# Canonical media activation v1

Opt-in Foundation mechanism for a **full same-session canonical v1** converter
candidate. Not T2, a writer/fence installer, general migration framework, policy
service or host maintenance operation. No live root selection, live conversion,
metadata edits, deletion, host lifecycle actions or Windows durability claims.

## API and actual host admission

`amplifier_foundation.session.media_activation` exports synchronous:

```python
apply_candidate(operation_dir, session_dir, *, session_id, owner, fence, admit,
                asset_reader, commit_media,
                acknowledge=acknowledge_candidate_checkpoint,
                cancelled=lambda: False) -> ActivationResult
resume_candidate(operation_dir, session_dir, *, session_id, owner, fence, admit,
                 asset_reader, commit_media,
                 acknowledge=acknowledge_candidate_checkpoint,
                 cancelled=lambda: False) -> ActivationResult
reconcile_activation(operation_dir, session_dir, *, session_id, owner
                     ) -> ActivationResult
```

Explicit absolute no-symlink POSIX paths only. Ancestors and leaf directories
are pinned with descriptors and names rechecked. Session identity comes from
the caller, never a guessed data root, directory name, digest or cwd.

- `fence(owner)` owns/quiesces the **actual host writer** throughout the call,
  including ACK. An unrelated advisory lock is insufficient when another writer
  can still append to an old descriptor. The helper does not install a fence.
- `admit() -> bool` returns exactly `True` for the current writer, source,
  owner/recovery/unknown-operation holds and existing native transcript guard.
  It also attests to published durable asset directories and trusted candidate
  provenance. Rechecked at entry and replace/ACK checkpoints. The host composes
  its own guards; path strings and saved hash receipts are not permission.
- `asset_reader(ref)` is the trusted owner's published-asset inspection context
  manager returning a seekable regular binary file at offset zero. The helper
  verifies digest/length and fsyncs the descriptor before primary replacement.
  The publication owner already fsynced new asset-directory entries; their
  identity cannot be discovered from this handle. Typical F1 binding is
  `store.resolve_ref(ref, committed=False)` under explicit host admission.
- `commit_media(receipt)` is the synchronous trusted bound
  `SessionMediaStore.acknowledge_checkpoint`, not an event/report. It returns
  `{state: "committed", durability: "legacy-checkpoint-backed", ref_count: N}`
  matching the fully enumerated closure, including duplicate ref occurrences.
- `acknowledge` defaults to the real
  `acknowledge_candidate_checkpoint(operation_dir, session_dir, *, session_id,
  asset_reader)`. Injection is for owned integration/fault tests, but still must
  return the actual sealed local `LegacyCheckpointReceipt` for this exact
  primary/session/hash/byte count/record count. Dict, None, coroutine and lazy
  iterator are not ACKs. It performs **no second history save**.
- `cancelled() -> bool` is synchronous current host cancellation. No success
  or subsequent primary replace is claimed from a cancellation.

Source plan owner/session/path must match the explicit caller and exact
`session_dir/transcript.jsonl`. Prefix/fork/import candidates refuse. The source
and all explicitly previewed backups must retain full identity and SHA. Pending
legacy append and v2 commit companions refuse; caller admission covers further
native companions/recovery holds known only to that host.

## Assets-first replacement and ACK

1. Reconcile exact converter candidate/manifest/plan/all original-copy hashes.
   Fully exhaust bounded record/ref/slot/asset verification. Integrity alone is
   not owner admission or consent. Converter's exact source-owned ref enumeration
   is a dependency gate, not just a SQL/file hash check.
2. Journal preparation/stage provenance in the private operation directory.
   Copy candidate to a new private `0600` same-session stage in bounded 64 KiB
   chunks; preserve source mtime before stage fsync, fsync directory, verify
   staged bytes and complete format/asset closure. Primary remains present.
3. Recheck current admission, cancellation, pinned directories, source, backups
   and pending companions. **Durably journal replace-intent before** the sole
   `os.replace(stage, transcript.jsonl)`, recheck before calling it, then fsync
   the session directory. No original or backup is removed/overwritten.
4. Verify actual installed bytes/records/slots/closure. Journal ack-intent, obtain
   actual local checkpoint after write, run trusted media-store ACK, and reverify
   receipt/actual primary/closure/backups. Only then atomically write/fsync the
   small acknowledged journal.
5. Return `ActivationResult(state="acknowledged", sha256, bytes, records,
   checkpoint=<local receipt>)`. The proof is process-local and source-bound;
   journals cannot reconstruct it or transfer writer authority.

An exactly byte-identical source/candidate skips replacement but obtains fresh
checkpoint + media ACK. Primary inode and mtime remain unchanged. A rewrite
preserves original source mtime (not ctime/inode); it must not manufacture user
activity or renew screenshot TTL. Metadata/title/status/retention stay untouched.

Memory is bounded by parser chunks, one bounded reference, fixed SQLite cache
and small operation metadata, not file/record/string/ref-count size. No
readline/json.loads over a message, all-history serialization, SessionStore.save,
second active backup, provider request or tool execution occurs. Verification
is O(history), with several streaming passes, not constant-time.

## Reconcile and resume

Reconciliation is read-only: no fence acquisition, fsync, replacement, callback,
cleanup or fresh ACK. Returned state and facts are not permission:

| State | Meaning |
| --- | --- |
| not-started | No activation journal, exact original primary present. |
| ready-to-resume | Preparing/ready journal, exact original primary. Fresh admitted resume may create a new stage; partial stages remain. |
| replace-unconfirmed-source-present | Replacement intent exists, exact original still named. **Never retry replacement**, even after a known synthetic before-call failure. Owner resolves separately. |
| installed-awaiting-ack | Replacement/ACK intent and exact candidate primary. Fresh admitted resume may fsync/readback/ACK, never replace/save again. |
| acknowledged-on-disk-reverify-required | Prior completion journal and candidate match; current process still needs real checkpoint/media ACK before use. |
| conflict | Neither expected original nor phase-consistent candidate. Preserve recovery, refuse mutation. |

Apply refuses any existing activation journal; it is not a replay API. Resume
prepares only before replace intent. After intent it can only re-ACK exact
installed candidate, never call primary os.replace again. Lost replace/final
journal/caller replies never replay tools, publication or transcript writes.
Local ACK is repeatable source verification, not a new observation/expiry.

Cancellation before replace returns cancelled-preserved; after write returns
installed-awaiting-ack. Denied admission, source/closure conflicts, invalid
metadata and filesystem/fsync/readback/ACK failures raise without success.
Exceptions may leave installed candidate: inspect durable journal and actual
bytes before recovery. Resume reacquires current host admission/Stop/holds.
Admission/cancellation is rechecked immediately after the final streaming hash
scans, before replacement, and again after final ACK verification. Oversized
serialized bindings refuse before any activation write; every created journal
fits the same 16 KiB bound used by recovery reads.

Originals, old backup names, converter journals, assets, candidates, failed
stage files, per-stage provenance notes and journal temporaries are retained.
No cleanup/rollback, registration clearing, deletion or mtime reclamation. Later
cleanup needs independent owner preview/admission/accounting; conversion is
not reclaimed bytes. Stage provenance notes enumerate per-attempt leftovers.

## Qualification boundary

`tests/test_media_activation.py` pairs actual converter, checkpoint factory and
F1 store on private synthetic roots: real ACK/readback/resolve, originals and
metadata preservation, unknown before/after replace with no replay, copy/fsync/
readback/checkpoint/media/final-journal faults, source/backup/pending/layout/
candidate/manifest/original conflicts, current admission, cancellation, fake/
lazy ACK refusal, no-op and 16 MiB individual non-image string with traced
helper peak below 3 MiB.

POSIX/Linux synthetic **candidate-graph only** until dependency and changed-risk
reviews are accepted. No Windows/native parity, actual CLI/Unified ownership,
power-loss/installed/DTU qualification, live conversion, T2 or release/adoption
is inferred. Converter8d60678's reviewed SQL-enumeration flaw requires the
1021e1d same-canonical-pass repair and aa04874 schema restriction (or an accepted
descendant); the old verifier is not a qualified activation dependency.
