# Cooperative session handoff

Foundation provides local ownership and release-request mechanisms. Applications
choose their storage locations, when to yield, how to stop work, and their UX.
The existing `SharedSessionStore` lock remains the authority; a release receipt
does not grant ownership to its requester.

```python
from amplifier_foundation.session import (
    SharedSessionStore, ReadyToRelease, CannotRelease,
    register_release_handler, request_release, SessionBusyError,
)

store = SharedSessionStore(workspace, session_id, root=coordination_root)
held = store.acquire(app="example-host")

async def prepare_release(request):
    # These are host operations, not Foundation APIs.
    await host.close_admission()
    request.report_progress("draining")
    await host.settle_work()
    request.report_progress("persisting")
    await host.save()
    await host.cleanup_and_flush()
    host.invalidate_old_callbacks()
    return ReadyToRelease()

registration = await register_release_handler(
    held, prepare_release=prepare_release, runtime_dir=private_runtime_directory,
)
```

The callback must not release the handle itself. Returning `ReadyToRelease`
asserts that all accepted work is accounted for and no old writer remains.
Foundation releases last. Returning `CannotRelease(code, message)` or raising an
exception leaves the lock held. A host that partly stopped must stay paused or
restore admission safely. Do not reread disposed context into an empty final save.

On a busy acquisition, another application may deliberately call:

```python
result = await request_release(
    store, expected_owner=busy.owner, request_id=unique_request_id,
    requester_app="another-host", timeout=30,
)
# Regardless of the receipt, only a successful acquire grants ownership.
successor = store.acquire(app="another-host")
await host.load_and_validate_history(successor)
```

Handle a competing owner or invalid history before enabling input. Do not
automatically send a new request to a different owner that wins the race.

## Locations and compatibility

`root=` selects the coordination domain independently of history storage.
Existing defaults and lock-file paths remain unchanged. Apps using a hierarchy
such as `~/.amplifier-agent` may supply their own root and runtime directory.
Hosts sharing the same history must also share the same coordination address.
Separate roots do not protect concurrent writers to the same history files.

Each acquisition has a new `acquisition_id` and a canonical `coordination_root`.
`held.owner` returns a detached diagnostic snapshot. Ready registrations add
versioned `handoff` endpoint metadata. Old lock-aware apps still exclude new
ones; without a registration they return `unsupported` for automatic release.
This protocol neither creates checkpoints nor changes history formats.

Version 1 uses private Unix sockets and verifies the OS user on both ends. The
app label is display attribution, not authenticated application identity.
Remote clients go through their host's authenticated service. This is local
POSIX coordination, not distributed locking or protection from arbitrary writers.

## Lifecycle and failures

- Registration must stay alive while serving requests. `await registration.close()`
  withdraws admission and waits for any already-started release callback.
- Normal `held.release()` invalidates its registration before unlocking. Hosts
  must serialize their normal shutdown, parking, and requested shutdown paths.
- Request IDs are remembered for the acquisition, including after registration
  replacement. An identical retry shares the result; a changed target or source
  is rejected. The bounded registry rejects new IDs instead of forgetting old ones.
- Progress is `received`, then optional `draining` and `persisting`; terminal
  results include `released`, `cannot_release`, `owner_changed`, `unsupported`,
  `handoff_in_progress`, `unauthorized`, `protocol_error`, `unreachable`, and
  `timed_out`. Applications decide how to display them.
- A request timeout bounds caller waiting (up to 300 seconds). It never cancels
  an already-started save or forces an unlock. An expired request cannot start
  shutdown. Retries retain the first admission deadline, even if their caller
  wait budget differs.
- An owner crash releases the OS lock automatically. Stale metadata and sockets
  are not ownership. Resume the last valid saved state and reconcile uncertain
  tool outcomes; never replay operations merely because a result is absent.
- A hung process can still hold the lock. A failed health check is not an unlock.
- Forked children close inherited listener descriptors without running the
  parent's server/selector cleanup or unlinking its endpoint. Their inherited
  registrations and ownership handles cannot authorize work. Verify a complete
  post-fork exchange, not merely that the endpoint file still exists.

Use task-captured activation guards around asynchronous writes. A callback from
an old acquisition must not borrow a newer handle. Foundation cannot intercept
raw host/tool file writes; graceful preparation remains the host's responsibility.
