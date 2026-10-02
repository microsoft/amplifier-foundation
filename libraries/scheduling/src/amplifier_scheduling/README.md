# Reusable scheduling policy and run store

This standard-library package owns recurrence calculation and durable admission
receipts. It has no Amplifier, provider, app, or tool dependency. The host supplies
explicit authorization, task/stop/question gates, input submission, and observed
run outcomes. It never runs model work or grants permission itself.

`normalize`, `next_after`, `preview`, and `due_occurrence` are pure functions over
explicit timestamps and IANA timezones. Once, absolute UTC intervals (minimum
one minute), daily local times, and selected weekdays are supported. Nonexistent
local times are skipped; folds use the first occurrence once. Due identity is
UTC. Preview and execution use the same policy. Missed policy `latest` selects
only the most recent occurrence; `skip` skips occurrences over one minute late.
No catch-up flood is produced after a long shutdown.

`ScheduleStore` owns a separate private SQLite database. Schedule edits use exact
revision CAS and stable command receipts. A database owner lease admits unique
`(schedule_id,due_utc)` runs, with stable IDs and no overlap. Run claims persist
before any external call. Hosts that create a destination first reserve its identity in `creating`. Hosts mark `submitting` before crossing an input
boundary, and must never retry ambiguous handoff. A new owner converts previous
creating/claimed/submitting/accepted/running runs to `unknown` and schedules to visible
`needs_review`, retaining the original input identity and evidence. Unknown runs
require deliberate reconciliation; they cannot release an overlapping new run.

Run history retains the most recent 100 terminal records per schedule by default;
active and unknown rows are never pruned. Idempotency receipts remain durable
rather than being evicted into permission to repeat an old mutation. Methods
perform no network operations; SQLite transactions never enclose provider or
runtime waits. Bounded history means old direct run reads can become unavailable,
not silently replaced by fabricated output.
