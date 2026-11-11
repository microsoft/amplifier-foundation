# Asynchronous native writer acquisition

`SharedSessionStore.acquire_async(app=..., **diagnostics)` acquires the same
canonical `HeldSession` as `acquire()`, off the event loop. Use it when an async
caller may be cancelled while acquiring ownership. Calling `asyncio.to_thread`
directly can discard a late successful result while its OS lock remains held.

```python
held = await store.acquire_async(app="native-adapter", version="1")
try:
    # The caller now owns the handle and its ordinary release policy.
    ...
finally:
    held.release()
```

Acquisition runs once, with the caller's context variables and diagnostics.
Success returns the original handle. Ordinary busy, transfer-fence and unsafe-path
errors retain their existing types and details. The synchronous acquisition,
explicit transfer API and on-disk ownership format are unchanged.

If the caller cancels before receiving the handle, this operation waits for the
in-flight acquisition. It releases any resulting handle through `HeldSession.release`
and waits for that release before re-raising the original `CancelledError`.
Repeated caller cancellation cannot abandon this settlement. An acquisition or
release error during settlement is available as the cancellation's chained cause.
The caller must not infer successful cleanup when that cause reports a failure.

Cancellation can therefore wait for blocking filesystem work already in progress;
the operation cannot forcibly stop a Python thread. This guarantee covers caller
cancellation while the event loop continues running, not process termination or
external cancellation of private settlement tasks. Application stop policy,
checkpointing, native history and cooperative handoff remain with their existing
owners. Once the handle has been returned, its recipient owns release.
