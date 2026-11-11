# Native transcript conflict detection

`amplifier_foundation.session.NativeTranscriptGuard` checks metadata for the native
`transcript.jsonl` and its `transcript.jsonl.backup` companion. It reads no history
content and writes no files. `NativeTranscriptConflict` reports an observed change
or nonregular file without replacing either history.

Construct the guard before reading native history. Call `check()` after that read
and before each ordinary or recovery write. Call `saved()` only after the caller's
own successful save to accept its new file identities. A missing file is a valid
baseline; an externally created, deleted, appended or replaced file is a conflict.
Symlinks are not followed. Unrelated filesystem errors propagate unchanged.

The guard complements the existing shared writer lease and task activation; it
does not grant ownership, load or repair sessions, or lock out a non-cooperating
writer between a check and a save. Keep checkpoint scheduling, cancellation,
recovery and error handling with their existing owners.

Consumers that prepare dependencies separately from execution should import this
API at the execution boundary. Dependency preparation must still be able to run
with an earlier installed Foundation before installing a compatible generation.
