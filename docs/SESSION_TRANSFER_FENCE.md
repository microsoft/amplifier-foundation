# Durable session transfer fences

The shared-session lock prevents concurrent local writers. A transfer fence adds
durable admission state for an application that moves a saved session to another
host. It remains effective after process exit or restart. It is not a transport,
distributed lock, remote authentication mechanism, or transfer implementation.

All execution participants must use a Foundation version with this API and call
`SharedSessionStore.acquire()` before execution and `HeldSession.check()` before
persistence. Older versions and writers that bypass the API do not honor the
fence. Hosts must use the same native history root, canonical workspace and exact
session ID. Altering the native root or manually deleting the marker is outside
this contract; the OS user controlling these private files remains trusted.

## Source handoff

The application blocks new work, cooperatively stops its runtime and dependent
work, checkpoints native history, and obtains the shared lock. It then fences
the saved writer before releasing the handle:

```python
held = store.acquire(app="transfer-adapter")
try:
    held.fence_transfer(transfer_id, destination_host, role="source")
finally:
    held.release()
```

`fence_transfer` does not stop work or save history. The host must establish that
boundary first. It writes a staged marker atomically, fsyncs its directory where
supported, and makes that handle fail execution checks and checkpoint writes.
An identical call is idempotent; a different existing marker is rejected.
If native history does not yet exist, creation is bounded to 128 missing
ancestors at a time. Each new directory is private and its containing directory
is fsynced before descendants or the marker are published, where supported.
Existing native directory modes are unchanged.

The application exports saved data without replaying inputs or uncertain effects.
After authenticating destination readiness, it permanently commits the source:

```python
recovery = store.acquire_transfer(transfer_id, app="transfer-adapter")
try:
    committed = recovery.commit_transfer()
finally:
    recovery.release()
```

A committed source cannot be acquired normally, recovered with
`acquire_transfer`, or cleared by this API. A later return transfer therefore
needs a new destination workspace/native history location. Preserve the source
marker when archiving inactive history. A failed or unknown application-level
transfer must retain its marker and expose its evidence for reconciliation.

## Destination staging and explicit resolution

Before installing transferred native history, an application acquires its new
destination session and calls `fence_transfer(..., role="destination")`. This
works before transcript or metadata files exist. Stage data without copying
process locks, advisory owner records, credentials or live runtime state.

After verifying the authenticated source release certificate and its exact
transfer identity, destination activation is explicit:

```python
recovery = destination.acquire_transfer(transfer_id, app="transfer-adapter")
try:
    recovery.clear_transfer()
finally:
    recovery.release()
```

The same `clear_transfer()` operation permits explicit cancellation of a staged
source before committing its release. The application owns the evidence and
authorization policy; matching an ID alone is not proof of a remote result.
`HeldTransfer` cannot authorize execution, read a checkpoint as a writer, write,
delete checkpoints, or replace a fence. Even after clearing, release it and
acquire a new ordinary handle before accepting later deliberate work. Clearing
does not start a runtime, submit input, or replay an operation.

## Native storage and failure behavior

`store.transfer_fence_path` identifies the marker without creating files:

```text
${AMPLIFIER_HOME:-~/.amplifier}/projects/<canonical-workspace-slug>/sessions/<id>/transfer-fence.json
```

The slug uses the native convention: replace `/` and `\\` with `-`, remove `:`,
and ensure a leading `-`. The private marker contains only `version` (1),
`session_id`, `transfer_id`, `destination_host`, `role` and `phase`. It is separate
from transcript/metadata and does not introduce a second history. Native history
remains readable without acquiring execution ownership.

`store.transfer_fence()` is a read-only inspection API. Malformed markers,
unsupported phases, wrong session IDs, symlinks and unsafe marker permissions
fail closed. Acquisition checks the marker after obtaining the local OS lock
and before replacing advisory owner metadata. A rejected acquisition releases
the temporary lock and does not change history, marker or owner metadata.
Ordinary `acquire()` raises `SessionTransferFencedError`, whose `fence` attribute
is a detached record. `acquire_transfer()` requires the exact staged transfer;
passing a transfer ID among ordinary acquisition diagnostics never bypasses it.

The marker is addressed through native history rather than the configurable
coordination root, so choosing another coordination root does not erase this
admission check. Applications must still share the coordination root to exclude
concurrent writers before a marker exists. Cross-host single-owner safety comes
from the application's authenticated staged-transfer protocol; these local
primitives do not provide that protocol or validate its receipts.
