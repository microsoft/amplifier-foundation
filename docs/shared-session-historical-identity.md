# Retaining a canonical shared-session address

An application can remove its own temporary working directory while retaining
canonical session history. Recreating that directory merely to open a historical
session changes application state unnecessarily. `SharedSessionStore.identity()`
exports a JSON descriptor, and `SharedSessionStore.from_identity()` reopens that
same coordination address without creating or requiring the workspace directory.

```python
store = SharedSessionStore(existing_workspace, session_id, root=state_root)
identity = store.identity()  # version, canonical workspace, sessionId
# The application persists this in its own authenticated ownership record.
# Later, after authorizing a historical operation:
historical = SharedSessionStore.from_identity(identity, root=state_root)
held = historical.acquire(app="history-export")
try:
    checkpoint = held.read()
finally:
    held.release()
```

The descriptor is an address, **not authorization** to execute, modify, dispose,
restore, or transfer a session. Foundation cannot authenticate the application's
ownership records. The caller must validate their provenance, operation policy,
and original state-root/native-home configuration before opening a descriptor.
The normal constructor still resolves an existing directory; `list_ids()` also
continues to require an existing workspace. Applications should retain the
identity before removing their own directory, not synthesize it from untrusted
paths or recreate a missing directory to satisfy the normal constructor.

The opener rejects malformed/extra fields, unsupported versions, invalid session
IDs, noncanonical/traversing/relative paths, and surviving symlink substitutions.
It does not claim that a newly recreated directory at the same address is the
original directory; inode/allocation ownership remains the application's policy.
No workspace content, filesystem directory, checkpoint, or native marker is
created by either the descriptor export or opener.

All store entrypoints retain their original behavior: `checkpoint_path`, `stamp`,
`read`, `acquire`, `acquire_transfer`, and `confirm_transfer_commit` address the
same state and native transfer marker. Both entrypoints use the identical OS lock
and private-directory validation. Held-handle checks, process death, checkpoint
validation, and transfer fences are unchanged. Ordinary acquisition still refuses a
native transfer fence after the workspace disappears; explicit transfer recovery
still needs the exact existing staged identity and application authorization.
