# Amplifier Scheduling

## Purpose and public contract

This package owns reusable recurrence calculations and durable schedule/run
records. It follows Foundation's [mechanism, not policy](../../README.md#philosophy)
design and its [optional package boundary](../README.md): independent installation,
explicit caller-selected storage, and no eager base-package dependency. Amplifier
Unified is a consumer of this API, not its normative authority.

The public entry points are [`policy`](src/amplifier_scheduling/policy.py) and
[`ScheduleStore`](src/amplifier_scheduling/store.py). Callers supply schedule
inputs, time, command identities, storage paths and execution callbacks. The
library returns normalized recurrence/due decisions, scoped revisions, durable
claims and exact command/run receipts. Revision conflicts refuse mutation;
unknown outcomes remain evidence rather than permission to repeat work.
Authentication, admission, task execution, notifications, UI and retention policy
belong to callers. This package is not a daemon or a native-session writer.

## Contract acceptance

Run an explicit Python 3.11 or 3.13 interpreter from the Foundation checkout:
`python libraries/qualify.py scheduling --output /owned/qualification/path`.
The [installed-consumer harness](../qualify.py) builds wheel/sdist, installs the
wheel separately, checks runtime isolation and import state, and runs
[recurrence/lease/recovery](tests/test_policy_store.py),
[bounded queries](tests/test_bounded_queries.py) and
[exact command receipts](tests/test_command_receipts.py). Changes to public
behavior must keep these checks and declared consumer compatibility reviewable.
[Provenance](extraction.json), [landing](LANDING.json) and the earlier
[qualification receipt](QUALIFICATION.json) describe exact recorded revisions;
they do not prove current host, browser, account or deployed acceptance.

Independent Python library extracted from Amplifier Unified. Standard-library runtime; no client, model, Amplifier runtime or host application imports. See extraction.json for provenance.

Run `uv sync` and `uv run pytest`. Durable SQLite receipts preserve uncertainty and never replay work. Applications own authentication, admission, execution and user presentation.

`ScheduleStore.page`, `run_page`, `incoming`, and `due` are bounded indexed APIs. Default storage preserves all run receipts; opt-in `history_limit` retains the legacy terminal-pruning policy. Application code should avoid the compatibility `list`, `runs`, `execution_runs`, and global `projection` methods for UI/list paths. Explicit leases, compare-and-set revisions, deterministic due IDs and unknown recovery prevent accidental replay.

`ScheduleStore.command_receipt(session_id, command_id)` returns the original
committed schedule/run result for one exact command, with `status: committed`.
It does not call a builder, inspect current schedule state, acquire a scheduler
lease or start work. Results survive later edits and process restart. Unknown
IDs and IDs belonging to another conversation return `None`; absence is never
permission to replay an upstream command whose admission may still be pending.
Run receipts are readable by their source and recorded destination conversation.
The indexed single-row read rejects a receipt above 384 KiB while retaining it.

### Startup authority

All four tables (`schedules`, `schedule_runs`, `schedule_commands`, and
`scheduler_lease`) are retained authority, including command results, unknown
runs, due identities and lease ownership. Existing stores require their complete
known schema through a bounded WAL-aware read-only check before a writable open.
Missing or unsupported tables refuse startup; no empty command/run history is
recreated. Fresh initialization requires no main file or WAL/SHM/rollback sidecar,
including dangling symlinks. Refusal preserves the main and nonempty WAL;
SQLite may update derived SHM. Derived indexes, including the due uniqueness
index over original run rows, can be reconstructed. Concurrent store instances
continue to coordinate through existing SQLite transactions and `scheduler_lease`;
this adds no external process lease, recovery scan or callbacks. Callers own
storage lifecycle serialization. This schema check cannot detect row erasure or
external replacement with a complete valid schema.

Main and existing sidecars must be regular files without symlinks before SQLite
is called, so retained FIFOs cannot block startup. Callers serialize storage
lifecycle changes; this is not an adversarial VFS or path-swap guarantee.
