# Shared session metadata and scoped settings

Native `metadata.json` owns the session name and description. UI projections and
legacy naming sidecars are not competing authorities. Paths are supplied by the
host; this mechanism does not require `~/.amplifier` or a particular application.

## Naming and metadata updates

```python
from amplifier_foundation.session.metadata import SessionMetadataStore

metadata = SessionMetadataStore(session_dir)
metadata.set_name("Investigate the build")  # explicit user rename
metadata.update({"custom_host_field": {"preserve": True}})
snapshot = metadata.read()                 # does not write or repair files

# After an asynchronous naming request:
metadata.set_name(
    "Build investigation",
    source="generated",
    expected_revision=snapshot.get("name_revision", 0),
)
```

Names contain 1–200 characters after stripping surrounding whitespace.
`name_source` is `manual`, `generated`, or `fallback`. A legacy name without
provenance is treated as intentional. Generated results cannot replace manual
or unclassified legacy names. Fallback names can be replaced by generation.
`name_revision` advances when a name/source changes; delayed results may supply
their observed revision. Reads never call a model.

`set_name(..., only_if_missing=True)` can adopt a legacy name without replacing
an existing native choice. Hosts own migration policy and preserve conflicting
legacy evidence. `create=True` on the store is an explicit opt-in for creation;
ordinary updates cannot recreate a deleted session.

Each update rereads and patches the latest metadata under a short file lock.
The stable lock is a sibling of the session directory,
`.<session-directory-name>.metadata.lock`, so deleting the session does not
replace the lock inode. Destructive deletion must acquire this lock as well as
the host's execution ownership lock. Never unlink the lock file.

Session execution still requires the separate Foundation ownership mechanism.
Metadata-only edits may use the short metadata lock while an updated host owns
execution. This does not grant permission to change transcripts, run tools,
take over a session, or bypass the host's authentication.

Runtime checkpoints must preserve newer canonical names:

```python
from amplifier_foundation.session.history import SessionHistoryStore

# While holding execution ownership:
SessionHistoryStore(session_dir).save(
    current_messages, runtime_metadata, merge_metadata=True
)
```

`merge_metadata=True` preserves unknown existing metadata and current naming
fields while applying the incoming runtime fields. Explicit renames use
`SessionMetadataStore.set_name`, not a cached checkpoint dictionary. The
existing default replacement behavior of `save`/`save_metadata` remains
available for intentional complete replacements.

All cooperating metadata writers use the same short lock. An old process that
ignores it, or an external editor, is outside this concurrency guarantee.
Upgrade both hosts before relying on simultaneous metadata edits. Backups and
corrupt-primary handling reuse the native history implementation. No transcript,
event, tool effect, provider continuation, or session ID is rewritten by naming.

## Scoped settings mechanism

```python
from amplifier_foundation.settings import read_settings, update_settings

settings = read_settings([global_path, project_path, local_path, session_path])
update_settings(session_path, lambda value: {**value, "voice": voice_preferences})
```

Hosts choose the paths and their order. Dictionaries merge recursively; scalars
and ordinary lists replace. `config.providers` merges by instance `id`, falling
back to `module`. Distinct instances and unknown keys survive; an empty provider
list does not remove inherited instances. Runtime bundle composition has its own
merge rules and is not changed by this API.

An empty settings file is intentional. Malformed YAML or a non-mapping root is
an error. Reads never rewrite files. Writers lock `<settings-file>.lock`, reread,
apply only the requested mutation, and atomically replace the file. Existing
permission bits are preserved where supported and new files use mode 0600 on
POSIX. Windows uses the containing directory's ACL policy. Credential values must not
be expanded into exported or portable session settings.

This moves shared file mechanisms into Foundation. It does not make host-specific
runtime controls, pending operations, or canvas layouts universally executable.
Those require explicit host adapters and capability contracts.
