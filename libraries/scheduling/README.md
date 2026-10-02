# Amplifier Scheduling

Independent Python library extracted from Amplifier Unified. Standard-library runtime; no client, model, Amplifier runtime or host application imports. See extraction.json for provenance.

Run `uv sync` and `uv run pytest`. Durable SQLite receipts preserve uncertainty and never replay work. Applications own authentication, admission, execution and user presentation.

`ScheduleStore.page`, `run_page`, `incoming`, and `due` are bounded indexed APIs. Default storage preserves all run receipts; opt-in `history_limit` retains the legacy terminal-pruning policy. Application code should avoid the compatibility `list`, `runs`, `execution_runs`, and global `projection` methods for UI/list paths. Explicit leases, compare-and-set revisions, deterministic due IDs and unknown recovery prevent accidental replay.
