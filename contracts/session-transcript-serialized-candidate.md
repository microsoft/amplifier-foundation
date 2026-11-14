# Serialized candidate input to the existing transcript writer

Opt-in POSIX input boundary, not another transcript format, publication engine, ownership protocol or media acknowledgement. Existing append/rewrite intents, retained generations, descriptor CAS and reconciliation remain the physical write mechanism. No application or Core dependency.

## Preparation and exact input

`prepare_serialized_candidate(candidate_stream, *, candidate_prefix: CommittedPrefix, candidate_check)` is a context manager yielding a local `PreparedTranscriptCandidate`. Its read-only properties `candidate_prefix`, `canonical_sha256`, `canonical_bytes`, and `message_count` are available **before writing**, so the actual host retains the canonical digest with its settled operation for unknown-acknowledgement recovery.

Input is an owned open regular binary file, with pending buffered writes flushed. Multiple hard links are refused; anonymous temporary files are supported. Physical EOF must equal prefix.committed_bytes; trailing unselected bytes are not ignored. Prefix is an integrity selector, not source/session provenance or adoption authority. The v2 body header binds generation, not session ID: actual converter/host ownership must bind the candidate to its selected session.

The boundary duplicates the FD and uses bounded pread, preserving caller cursor/read-ahead. It copies and hashes exact input into an anonymous snapshot, validates strict integer-version header/generation, all complete role-bearing message dictionaries, nested duplicate keys, Unicode/primitive/schema bounds, count and SHA256. The reviewed incremental transducer creates a private canonical row spool without decoding a giant string or all-history list. No new parser engine.

Required candidate_check raises on actual candidate namespace/identity/ownership loss. Checks span copy, transduction, admission, pre-CAS and exit. It must remain valid after legitimate target CAS: checking that the *old target descriptor* remains current is not an appropriate candidate guard. Candidate ownership and actual target all-writer exclusion are distinct obligations; a no-op callback proves neither.

Prepared objects are context-issued, process-local and nontransferable through construction/copy/serialization/fork. Exit or observed guard error revokes them irreversibly. They are not typed media receipts.

## Admission over the same bytes

`with prepared.open_verified() as source:` exposes a checked read-only bounded seekable view of the exact raw header-bearing snapshot. Supports read(size<=65536), seek and tell; no public mutable FD or whole-row decoding. The media-schema owner supplies strict bounded canonical ref enumeration over these same bytes; it must be exhausted including final checks. A DB ref query, prefix dictionary or caller-supplied duck iterator is not a substitute. This format input boundary alone makes no media publication/ref-closure claim.

## Existing publication and reconciliation

`writer.rewrite_prepared(prepared, *, expected: CommittedPrefix, operation_id, admit)` consumes exactly the issued canonical spool through the existing kind=rewrite engine. Target session must match the selector. Complete candidate verification precedes admission; admission must validate/publish/fsync every newly reachable ref from the same prepared snapshot under actual host/store ownership before canonical mutation. Writer generates a new destination header/generation, retains old body/tail unchanged and follows unchanged intent, predecessor CAS, body fsync, pointer replace and directory fsync. No candidate-to-active rename or second journal/backup.

`writer.rewrite_serialized(stream, *, candidate_prefix, expected, operation_id, admit, candidate_check)` wraps preparation plus rewrite. Integrations needing fingerprints and strict media admission should own preparation explicitly inside their transaction boundary.

Raw candidate SHA256 covers original header plus rows. `prepared.canonical_sha256` instead hashes normalized canonical message JSONL, including LF but excluding header. Persist that digest, byte/message count, predecessor and stable operation ID before writing. On uncertainty use existing `writer.reconcile(operation_id=..., expected=original_predecessor, suffix_sha256=canonical_sha256, kind="rewrite")`. Equivalent ordinary/prepared inputs have the same canonical intent; different input under the same ID refuses.

Exception after pointer replacement or late target/candidate exit means uncertain acknowledgement, never permission for new ID, tool replay, tail deletion or guessed clean reinitialization. Resolve the original saved intent. CommitAck remains an unsealed selector. Actual sealed versioned checkpoint/store acceptance follows the existing owner integration under real ownership; report only after successful typed ACK context **and preparation context** exit. A binding accepting an externally prepared object cannot claim enclosing preparation-exit success before that caller context ends. Preferred host binding owns preparation through admission, rewrite and typed ACK and clears pending state only after all contexts exit.

## Qualification limits

Synthetic checks exercise exact input/schema/EOF refusal before admission, cursor/FD checks, source changes, cancellation, copy/fork refusal, CAS conflict, late fence exit, canonical equivalence, reconciliation, old body/tail preservation and 2MiB/8MiB individual records. Memory measurement is additional Python allocation, not RSS or ordinary resume memory. Shared publication fault tests remain separately attributed and are rerun for changed engine inputs. No live activation, migration, configuration, recovery deletion or new resource is authorized by this contract.
