# Native session activation

`amplifier_foundation.session.activation.ActivationGate` is the shared task-local
fence used by native application runtimes. A session can release its writer and
later resume in the same process while an old task still has a reference to it.
That old task must not borrow the resumed session's authority.

The application creates one gate for its session owner. After acquiring the
native writer, `activate()` returns a new token and binds it for mount-time work.
Work captures `current()` when admitted, then uses `bind(captured)` and
`reset(binding)` in a `finally` block. Check `check_current()` at a mutation
boundary. When retiring that activation, `release(activation)` invalidates its
tasks. A stale release cannot invalidate a newer activation.

Token identity grants authority within that gate; its number is diagnostic.
Neither a matching number nor a copied token grants authority. A suspended task
retains its captured context through replacement and must fail its next check.
The gate is owned by one event loop, not a cross-thread synchronization primitive.

This mechanism does not acquire an OS lock, cancel tasks, close modules, read or
write history, or select a preparation policy. The application's lifecycle still
owns those operations, and Foundation's `SharedSessionStore` remains the
exclusive native writer mechanism. There is no new approval or history store.
