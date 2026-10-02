# Amplifier Operations

Independent Python library extracted from Amplifier Unified. Standard-library runtime; no client, model, Amplifier runtime or host application imports. See extraction.json for provenance.

Run `uv sync` and `uv run pytest`. Durable SQLite receipts preserve uncertainty and never replay work. Applications own authentication, admission, execution and user presentation.

`OperationJournal.list/page` use indexed descending keyset pages; `read` bounds output bytes and explicitly reports source/retention gaps. `OperationRequests` (requests submodule) preserves admitted/unknown input receipts across restart, with actor/argument identity binding. This library never starts or controls a process itself. Default output retention remains10MB per operation with explicit truncation metadata; schedule/native history is not deleted by this library.
