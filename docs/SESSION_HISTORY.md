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
store.save(messages, history.metadata)
```

The default save policy excludes `system` and `developer` messages, matching CLI.
Use `preserve_system=True` if a host needs otherwise. All other JSON-serializable
fields are preserved, including provider-specific continuation state. A host
that already sanitizes messages can pass `sanitizer=its_sanitize_message`.
Redaction and choosing which data to persist are application policy.

`save` **replaces** metadata. Merge any host updates with the loaded dictionary to
preserve fields written by another host. `save_messages` and `save_metadata` are
available for incremental conversation persistence and session renaming.

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
