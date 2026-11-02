# Shared native session history

`SessionHistoryStore` gives applications a common interface to the files already
used by Amplifier CLI. It does not migrate sessions or change their storage
ownership. Older CLI versions can still read transcripts saved through this API.

| File | Authority |
| --- | --- |
| `transcript.jsonl` | Saved conversation, including ordering, provider continuation fields, and persisted reminders |
| `metadata.json` | Existing application metadata: name, lineage, bundle, workspace, and unknown fields |
| `context-intelligence/events.jsonl` | Optional activity observations, retained by their configured logger |

The combined view exists in memory only. No merged history file, database,
checkpoint, new logger, or event copy is created. The Context Intelligence bundle
requires no changes. Existing `config.md` files remain host-managed.

## Read and continue a session

```python
from pathlib import Path
from amplifier_foundation.session import SessionHistoryStore

store = SessionHistoryStore(Path(session_dir))

# Resume does not need to open or load an activity log.
history = store.load(include_events=False)
await context.set_messages(history.messages)

# Later, while holding the application's session ownership lock:
messages = await context.get_messages()
store.save(messages, history.metadata, merge_metadata=True)
```

The default save policy excludes `system` and `developer` messages, matching CLI.
Use `preserve_system=True` if a host needs otherwise. All other JSON-serializable
fields are preserved, including provider-specific continuation state. A host
that already sanitizes messages can pass `sanitizer=its_sanitize_message`.
Redaction and choosing which data to persist are application policy.

`save` defaults to replacing metadata. Runtime checkpoints should opt into
`merge_metadata=True` to preserve current naming and unknown fields.
`save_messages` and `save_metadata` support incremental persistence; explicit
renames use the field-oriented metadata API below.

```python
from amplifier_foundation.session.metadata import SessionMetadataStore
SessionMetadataStore(session_dir).set_name("Updated name")
```

Each write uses the existing CLI atomic-replace and `.backup` convention. Both
new payloads and the readability of existing files are checked before a paired
save starts. A damaged primary never replaces its readable backup. Each file is
atomic individually; this is not a multi-file transaction or a power-loss
persistence guarantee. Transcript writers must hold execution ownership. Metadata writes also use a
short cooperating lock; see [shared metadata and settings](SESSION_METADATA.md)
for field updates and checkpoint merging that preserve newer names.

Applications already using `SharedSessionStore` can retain its acquire/check/
release ownership mechanism. They should read and write native files through
`SessionHistoryStore` while holding that lock, without calling `HeldSession.write`
to produce an additional checkpoint. Older applications that do not participate
in the ownership protocol must not write the same session concurrently.

## Bounded native Windows history

The existing API selects a private Windows adapter on Windows. The scope is
ordinary `transcript.jsonl`, `metadata.json`, their `.backup` files and the
transcript `.append-pending` intent, not shared checkpoints or transfer fences.
The layout and public API do not change. POSIX persistence is unchanged.

The adapter requires an absolute local fixed NTFS path (relative inputs are
anchored without resolving away unsafe components). It pins and validates each
ancestor before opening or creating a descendant. Newly created directories and
files receive a protected current-user DACL **at creation, before payload**.
Existing history directories and files must already satisfy the private ACL
contract. Reparse points, hardlinked files, unsafe ACLs and namespace aliases
are refused without automatic ACL repair or migration. Windows security failures
are not transcript corruption and do not authorize overwriting the object.
Reads, including missing-root reads, never create directories or locks.
Optional activity reads use the same validated private handles; their logger
must provision private files separately, or the host can omit events.

Read-only streams retain a validated file object, not a namespace lease. They
hold traversal pins through the private file handle's open and validation, then
release those pins before yielding the stream. Read, seek and peek use that
same non-inheritable handle without reopening its pathname. Each future path
open independently validates its ancestors and file again. This lets an ordinary
atomic settings writer publish into an ancestor while a history reader remains
open; it does not bypass a separately held ownership or write-operation lease.
Ownership, publication, append/intent, metadata-lock and fork-reservation pins
retain their existing lifetimes and sharing restrictions.

Replacement writes complete and `FlushFileBuffers` the new private temporary
before a same-directory native `NtSetInformationFile(FileRenameInformationEx)`
replacement with `REPLACE_IF_EXISTS | POSIX_SEMANTICS`. The native request uses
only the destination leaf name with a null root handle; the source handle's
directory must match the pinned destination directory. Directory pins keep
`FILE_LIST_DIRECTORY` access and deny write/delete sharing throughout publication.
Readers share deletion:
an already-open reader retains the complete old file while later readers see the
new file. Unsupported native rename/disposition operations fail; there is no
copy/delete, delete-first or less-safe sharing fallback. Native execution on
the intended Windows version/filesystem is required before claiming qualification.

`save(..., incremental=True)` retains the append optimization. A warm append
copies only the previous tail into the backup, not the full transcript prefix.
The complete previous transcript is flushed before the private pending intent
is published. The primary append is flushed before that intent is removed.
A failed primary append retains the intent and complete backup, even if failure
lands between whole JSONL rows. Intent-aware reads recover the backup and do not
acknowledge a partial batch. Recovery replacement does not rotate a damaged
primary over a good backup.

These are individual-file visibility and file-content flush guarantees, **not**
a file-pair transaction or a power-loss guarantee for directory/ancestor
publication. Transcript writers must still hold execution ownership. Windows
checkpoint/fence mutations and cooperative handoff remain unsupported; ordinary
history saves do not remove those gates.

On Windows, `session.store.write_transcript` also uses this private boundary and
preserves system/developer rows, as that lower-level writer did before. As the
explicit repair/rewind writer, it can replace a corrupt primary after validating
the destination's security, including any existing append intent before changing
the primary or backup. It retains the validated intent handle through replacement
and removes that intent only after successful publication. A corrupt primary
need not parse to perform this explicit repair. It retains existing backups
rather than rotating corrupt data over them; it does not create a new backup
automatically. The file-based `fork_session` reads the canonical source through `SessionHistoryStore`
and saves its transcript/metadata through the same boundary. Windows forks first
reserve a new private directory with atomic create-only semantics, then keep it
and its ancestors pinned throughout transcript, metadata and optional legacy
event publication. Every existing destination is refused with `FileExistsError`,
including empty private directories, self-forks and same-ID contenders. There is
no exists-then-create check or adoption of another fork's reservation. If a newly
reserved fork fails, its directory and any partial files remain, with an explicit
incomplete-publication exception note; no rollback deletes data. This is not a
file-pair transaction. The operation does not create inherited-ACL history or
resolve away reparse points. Unsafe source objects remain refusals, not migrations.
The generic `read_jsonl`/`write_jsonl` utilities, format-only
`load_transcript`/`load_metadata` readers and legacy root-event slicing remain
separate low-level file helpers, not qualified private-history APIs.

## Recovery and revision checks

- A missing transcript or metadata file falls back to its `.backup`, if present.
- If both are absent, the result is an empty list or dictionary respectively.
- A corrupt primary falls back to a **complete valid** backup and records a
  `recovered_backup` diagnostic. Reading never rewrites the primary.
- Existing unreadable or corrupt files without a usable backup raise
  `SessionHistoryError`. Canonical message rows are never silently dropped.
- A valid empty primary is authoritative over an older nonempty backup.

`exists()` checks canonical files and backups. Diagnostics contain only a stable
code, source label, optional one-based line number, and severity. They never
include conversation text or tool arguments. Standalone `load_messages`,
`load_metadata`, and `iter_events` replace `store.diagnostics` for that operation;
`load` collects all diagnostics into the returned view.

`SessionHistory.revision` contains pre-read `FileStamp` values for the transcript,
metadata, and their backups, plus the events file when requested. If any file
changes during a combined read, a `changed_during_read` diagnostic is returned.
Hosts requiring a stable resume view should retry under their session lock.

## Enrich a chat with activity

```python
history = store.load()  # include_events=True
for association in history.associations:
    event = history.events[association.event_index]
    if association.message_indices:
        show_activity(event, association.message_indices, association.turn_index)
    else:
        show_unassociated_activity(event, auxiliary=association.auxiliary)
```

Events never replace, add, or reorder transcript messages. Normalized events
retain the original `data` and extra envelope fields, and expose `event`,
`timestamp`, `session_id`, and one-based source `line`. A nested CI session ID is
recognized; conflicting identities are diagnosed, and explicitly different
sessions are filtered out. Unscoped events remain visible with a diagnostic but
are not associated to canonical messages.

Associations use exact message or tool-call IDs. Parallel tool completion order
does not change transcript order. Reused IDs remain ambiguous. Exact prompt text
can associate uniquely; repeated prompts associate only when the complete prompt
sequences agree. Auxiliary naming/summarization observations are marked and not
assigned to conversation messages. Timestamps alone are never enough to attach
an event to a turn. All message and turn indices are zero-based. Each associated
turn also exposes `turn_message_index`, its exact human-message anchor in the
transcript, so hosts do not need to recreate reminder/tool-result filtering.

For relocated Context Intelligence logs, pass the actual configured path and,
when necessary, a session identity different from the directory name:

```python
store = SessionHistoryStore(session_dir, events_path=configured_log_path,
                            session_id=actual_session_id)
```

There is no fallback to the legacy root `events.jsonl`; it may belong to an
entirely different logger. Missing CI logs are normal and do not prevent resume.
Malformed, truncated, or unreadable activity records are diagnosed separately
from transcript corruption. `iter_events()` streams large logs without eagerly
building the combined view. Hosts can use `iter_events(max_lines=5000)` to bound
physical rows scanned, including malformed and other-session records. If more
input remains, `scan_limit` is reported. An additional `max_bytes=16 * 1024 * 1024`
bounds total raw input, including a single oversized provider payload. A row
crossing that budget is not parsed or yielded; at most one extra byte establishes
that input remains. Both limits default to unlimited.
`load(include_events=False)` does not read or stat
the events file at all.

## Fork, edit, and repair

The existing pure message operations remain the mechanism for these operations:
`fork_session_in_memory`, `slice_to_turn`, `diagnose_transcript`,
`repair_transcript`, and `rewind_transcript`. Hosts select the intended boundary,
repair policy, new session identity, and lineage, then persist the resulting
messages using `SessionHistoryStore`.

Do not copy or rewrite Context Intelligence events when forking a conversation.
The parent log describes work that actually happened in the parent session. A
child's logger records its future activity; lineage metadata points to the
parent. The older file-based `fork_session` and `events` helpers operate the
legacy root events file and are not CI-log migration mechanisms.

This separation permits future event reconstruction experiments without changing
which source currently owns resumed conversation state. It also permits either
host to adopt the shared interface before any event-only migration is considered.

## Transcript repair policy

`diagnose_transcript` indexes top-level compatibility tool calls and canonical
`tool_call` content blocks. Equivalent mirrors in one message count once.
`repair_transcript` gathers an unambiguous affected parallel batch immediately
after its declaring call. It preserves each original result message and its full
payload, metadata, signatures, cost, and extra fields (except internal `line_num`).
Non-result messages keep their relative order, but **placement can change
chronology**. This is not a chronology-preserving reconstruction.

Only actually missing results receive placeholders: no result was recorded, and
the execution outcome is unknown. Derived assistant repair notices do not assert
interruption or execution failure. Reused/conflicting call IDs, duplicate results,
unbound or premature results, unsafe cross-batch associations, and multi-result
messages that cannot be placed whole remain explicitly broken and unresolved;
their original evidence is preserved, not split, relabeled, or replaced.
Association diagnostics stay in the returned index/diagnosis, not new message
metadata; existing sequence metadata is not rewritten. Hosts must inspect the
post-repair diagnosis. Repair is not a universal dispatch guard.
