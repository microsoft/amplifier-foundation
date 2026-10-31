# Shared session state participant guide

`amplifier_foundation.session.shared_state` is a small same-host coordination
mechanism. It lets cooperating local applications share one authoritative
checkpoint for one canonical workspace and one exact root session ID.

## Supported boundary

- Checkpoint writers require the same host, the same OS user, POSIX, and a local
  filesystem with the native locking primitive. Windows has only the bounded
  ownership/read slice described below, not authoritative persistence.
- All participants must use the existing workspace path (it is resolved
  canonically), the exact root UUID, and the same explicit `root` or
  `AMPLIFIER_SESSION_STATE_HOME`. The default root is
  `${XDG_STATE_HOME:-~/.local/state}/amplifier/sessions`.
- This is an atomic authority checkpoint, not a replacement for a host's native
  session projection. A host may retain its own history files, but the shared
  checkpoint is authoritative between cooperating participants.
- Checkpoints contain complete provider context: structured messages, tool
  call/result pairs, and IDs. Do not substitute UI bubbles or event-log copies.
  Use a portable bundle reference and credential-safe JSON metadata only; never
  put secrets, tokens, passwords, or environment values in metadata.

No live attach protocol, TUI/web multiwriter protocol, or remote protocol is
provided. There is no force unlock, TTL, lease, takeover, or shared lock-file
deletion API.

Applications implementing host migration can use the separate
[durable transfer fence](SESSION_TRANSFER_FENCE.md) to keep ordinary execution
blocked after a saved writer releases its lock. This persists admission state
beside native history and does not implement remote transfer or authentication.

## Windows first slice

The Windows backend uses Python 3.11+ standard-library `ctypes`, with explicit
pointer-sized Win32 signatures. It introduces no new dependency, state root,
layout, application lock, lease or takeover protocol.

| Operation | Windows boundary |
| --- | --- |
| `acquire()`, `HeldSession.check()`, `release()` | Exclusive nonblocking `LockFileEx` on the stable `session.lock`; the handle and all pinned coordination ancestors deny delete sharing and are non-inheritable. Closing/crashing releases the OS lock, not by deleting its target. |
| `read()`, `stamp()`, `list_ids()` | Read-only access through validated handles and pinned ancestors on a local fixed NTFS volume. No missing directories are created. A stamp uses file identity, size, write time and creation time; it is not a durability acknowledgment. |
| `transfer_fence()` and ordinary admission | Inspect the existing native marker safely without creating native directories. Existing valid fences block execution; malformed, foreign-owned or unsafe markers fail closed. Acquisition checks before publishing owner diagnostics. |
| `owner.json` | Protected, complete temporary-file writes, file-buffer flush and atomic replacement for advisory display only. Only explicit advisory owner readers share deletion, so a diagnostic reader does not block replacement. Lock and directory pins still deny deletion. Release closes ownership even if advisory publication fails. No directory/ancestor power-loss persistence is claimed. |
| Authority mutation and transfer recovery | Checkpoint `write()`/`delete_checkpoint()`, fence save/commit/clear, `acquire_transfer()` and `confirm_transfer_commit()` explicitly raise `SharedStateError` before authority mutation. Namespace persistence is not yet qualified. |

All traversed components reject reparse points (including junctions), alternate
namespace/short-name aliases and unsafe path forms. Regular state files must
have one hardlink only. State-owned directories and files are created with an
explicit current-user owner and protected private DACL **at creation**, not
repaired afterward with `chmod`. Existing private objects must have the same
owner, a protected non-null DACL and no ordinary-other-user allow ACEs, including
read access or inherit-only grants. SYSTEM, built-in Administrators and the
specific TrustedInstaller service SID retain normal OS authority; OWNER RIGHTS
refers only to the verified owner. These exceptions do not include Users,
Authenticated Users, arbitrary services or localized account names.
Unsupported ACE types fail closed. Creation still grants only the current user;
validation never silently tightens or repairs an existing DACL.

Before opening or creating a descendant, **every** directory from the drive root
is pinned and checked for trusted ownership and untrusted mutation rights.
Nonprivate ancestors/native marker directories may be owned by the current user
or those explicit OS authorities. They may allow ordinary reads. Above the
configured state/native root, an effective create-subdirectory-only grant is
also permitted: it does not authorize reparse conversion or deletion of a pinned
parent. `FILE_ADD_FILE`/`FILE_WRITE_DATA`, `FILE_WRITE_ATTRIBUTES`, delete-child,
ACL/owner changes and other mutation grants are not exempt. Inherit-only ACEs
grant no access to a nonprivate parent; each existing child is checked separately,
and new children receive protected DACLs.

Win32 sharing does **not** exclude attribute-only access. The ACL checks, not
the share mode, prevent untrusted reparse conversion before absolute-path
creation. [MS-FSA FSCTL_SET_REPARSE_POINT](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-fsa/4aeefef8-92c3-4abc-af7a-a610caf8a165)
requires `FILE_WRITE_DATA` **or** `FILE_WRITE_ATTRIBUTES`; both are rejected for
untrusted effective grants. Default ACLs vary by Windows version and deployment.
A shared temp or system-drive ancestor that grants ordinary users these rights
is refused before descendant creation, even when the configured state root
would be private. The error identifies the unsafe traversal directory; access
denied and unsupported ACEs are never treated as successful validation.

Exact Windows session IDs must be lowercase and not DOS device names
(`con`, `nul`, `com1`, etc.). IDs are rejected rather than silently lowercased.
Differently-cased existing owned directory/file names also fail closed. Keep
the canonical workspace and the same explicit coordination root/native home
across participants. Do not use UNC, mapped network, removable, non-NTFS or
reparse-backed paths. All path chains are bounded to 128 components.
Retained identities require the exact resolved Windows path string, not
case-insensitive `Path` equality: a case alias or case-only rename fails closed
rather than creating a second string-hashed lock address. An exact missing
workspace remains retainable without being recreated when surviving ancestors
still resolve to the same spelling.

The OS user and code running as that user remain trusted. A handle is
process-bound and non-copyable; applications must still capture the capability
for each task and settle runtime work before release. No UID is fabricated.
Cooperative release-request handoff is still POSIX-only; there is no Windows
named-pipe or TCP substitute in this slice. This is not complete Windows host
or runtime support, and tests skipped on POSIX do not qualify native behavior.

## Minimal participant pattern (POSIX checkpoint writer)

```python
from pathlib import Path

from amplifier_foundation.session import SessionBusyError, SharedSessionStore

workspace = Path("/existing/workspace")
root_session_id = "018c1234-5678-7abc-8def-0123456789ab"  # exact root UUID
store = SharedSessionStore(workspace, root_session_id, root="/same/state/root")

try:
    held = store.acquire(app="terminal-host", pid=__import__("os").getpid())
except SessionBusyError as error:
    # `error.owner` is advisory untrusted display data, not authority.
    show_busy_owner(error.owner)
else:
    try:
        checkpoint = held.read()
        context = restore_or_initialize_context(checkpoint)
        result = execute(context, "work placeholder")
        held.write(
            context.messages,
            bundle="portable-bundle-reference",
            metadata={"host": "terminal"},
        )
    finally:
        held.release()
```

Acquire before reading authority for writable work. `HeldSession` is
process-bound and cannot be copied. A released or wrong-process handle fails.
The host must capture the handle/token belonging to each task or callback:
never let an old callback look up a mutable global "current writer" and thereby
borrow a new acquisition. The library cannot enforce that application policy.
In the example, `execute` represents a finite operation that settles its owned
jobs, approvals, and checkpoint work before returning or raising. Arrange normal
runtime shutdown under the lock before `release`; releasing a handle alone does
not stop an engine or its tools.
If acquisition is busy, retain the user's draft locally and display diagnostics
as untrusted advisory information.

## TUI and warm reuse

A TUI may hold the lock throughout an active prompt and release it on close.
Alternatively, it may explicitly auto-park only after known work settles, save
under the lock, then release. Exiting a view does not inherently release an
executor; the host decides that lifecycle boundary.

Warm reuse is optional. After each save, retain a `FileStamp` captured while
holding the lock. On the next acquisition, compare the new stamp under the lock:
unchanged state can be reused, while a changed, missing, or unknown stamp
requires a fresh read/rebuild according to the host's policy. File metadata
freshness is not proof that another runtime is quiescent.
Compare relevant configuration inputs as well as the authoritative checkpoint.
Keep a parked executor unable to run tools, change context, or persist state
until a new acquisition succeeds. An unchanged stamp only permits reuse of a
runtime that is still valid and was safely parked. On missing previously known
authority, report the problem rather than resurrecting a stale native projection.

For the first TUI integration, holding ownership until the TUI session closes is
the simpler policy. Automatic parking can be added after its lifecycle and
delayed-callback tests pass.

## Storage interoperability

Prefer this Python API, including from a small local adapter when the TUI uses
another language. Independent implementations must share the same lock
primitive and serialization semantics, not merely agree on filenames.

Under the configured root, version 1 addresses a session as:

```text
v1/<sha256(UTF-8 canonical workspace path)>/<exact root ID>/
    session.lock     # stable flock (POSIX) / LockFileEx (Windows) target; never unlink
    owner.json       # advisory display information, not a lease
    checkpoint.json  # atomically replaced authority
```

On POSIX, the checkpoint has exactly these keys: `version` (1), `workspace`, `session_id`,
`bundle`, `messages`, and `metadata`. State-owned directories are private to the
OS user (0700); files are private (0600). Windows uses the private DACL boundary
above instead of POSIX modes. Acquisition does not rewrite the
checkpoint, so taking and releasing a lock alone does not invalidate its stamp.
Read-only history may inspect an atomic checkpoint without becoming a writer;
restoring a context for execution requires acquiring first and reading again.
Do not synchronize or replay event logs, pending approvals, or interrupted tools.

## Safe checkpoint deletion (POSIX only)

After a host's own human confirmation, a live holder can remove only the
authoritative checkpoint:

```python
held.delete_checkpoint()
```

The call is serialized with writes and release, is idempotent if the checkpoint
is already absent, and fsyncs the containing directory where supported. It does
not remove or replace `session.lock` or the session directory. The same live
holder may later call `write()` to recreate the checkpoint. Never delete the
stable lock file or directory to try to take over a session.

## Three-host acceptance matrix (POSIX checkpoint participants)

| Situation | Expected result |
| --- | --- |
| Current owner is active; terminal, web, and TUI contenders attempt acquisition | Owner continues; both other participants receive `SessionBusyError`. |
| Terminal, web, and TUI run one after another with the same full context | Each acquires, reads/writes, and releases in turn using the canonical workspace, exact root UUID, and same state root. |
| A released holder, old task, or callback tries to write/delete after a new holder acquires | It fails; it cannot use the new holder's capability. |
| Owner crashes, forks, or execs | Native lock lifetime/recovery applies; a child cannot retain the parent capability, and close-on-exec releases the descriptor. |
| Relevant configuration changes while a session is warm | The host invalidates warm reuse and rebuilds from authoritative state. |

An installed Digital Twin Universe exercise, if needed, is requested from the
manager. This guide's example is illustrative and does not claim that such an
exercise was run.
