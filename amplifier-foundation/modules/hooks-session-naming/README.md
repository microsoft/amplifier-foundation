# hooks-session-naming

Automatic session naming and description generation for Amplifier sessions.

## Overview

This hook module observes conversation progress and automatically generates human-readable session names and descriptions using the configured LLM provider. Names and descriptions are stored in the session's `metadata.json` for display in CLI, log-viewer, and other UIs.

The module is entirely non-blocking: all LLM calls run as background asyncio tasks and never delay the main conversation. A session-end drain ensures in-flight naming tasks complete before teardown.

## Features

- **Non-blocking**: All LLM calls run as background asyncio tasks via `asyncio.create_task`, never blocking the main conversation
- **Session-consistent**: Tasks are tracked in `_pending_tasks` so Python 3.12+ cannot garbage-collect them before completion
- **Model-selectable**: Supports `model_role` and `provider_preferences` for precise control over which provider and model handles naming
- **Automatic naming**: Generates a human-readable session name after a configurable number of turns (default: 2)
- **Description updates**: Periodically updates the session description as the conversation evolves, only when scope meaningfully expands
- **Smart context extraction**: Uses a bookend+sampling strategy for long conversations (first 3 turns, sampled middle, last 5 turns)
- **Graceful deferral**: If the LLM signals insufficient context, retries on subsequent turns up to `max_retries` times

## Configuration

```yaml
hooks:
  - module: hooks-session-naming
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/hooks-session-naming
    config:
      initial_trigger_turn: 2        # Generate name after this turn (default: 2)
      update_interval_turns: 5       # Update description every N turns (default: 5)
      max_name_length: 50            # Maximum name length (default: 50)
      max_description_length: 200    # Maximum description length (default: 200)
      max_retries: 3                 # Max retries on defer (default: 3)
      # model_role defaults to "fast" — no config needed for cheap/fast naming.
      # Set to null to use your priority provider explicitly.
      model_role: fast
```

## Provider Selection

`model_role` (default: `"fast"`) routes naming to a cheap/fast model via the routing
matrix — the same mechanism used by the `delegate` tool and recipe agent steps.
Session naming is a simple classification task; it does not need the priority model.

Resolution order:

1. **`model_role`** — Resolved against `coordinator.session_state["routing_matrix"]`
   via `resolve_model_role` from `hooks-routing`. Defaults to `"fast"`.

2. **Fallback** — `next(iter(providers.values()))` — the first/priority provider.
   Used when `model_role` is `None`, or when resolution fails.

### Optional Dependency: hooks-routing

`hooks-routing` is an **optional runtime dependency**. The module degrades gracefully:

- If `hooks-routing` is not installed, the module silently falls back to the priority
  provider. No warning is emitted — falling back is the expected behaviour when the
  routing module is absent.
- To disable routing explicitly and always use the priority provider, set `model_role: null`.

## Async Behavior

Session naming is designed to be entirely non-blocking. Here is how the async machinery works:

1. **`asyncio.create_task`**: Each naming or description-update request is wrapped in `asyncio.create_task(self._generate_name(...))` and scheduled on the running event loop without blocking the hook handler.

2. **`_pending_tasks` reference holder**: The returned `Task` object is added to `self._pending_tasks` (a `set`). This prevents Python 3.12+ from garbage-collecting the task before it finishes, which would silently cancel it.

3. **`done_callback`**: `task.add_done_callback(self._pending_tasks.discard)` is registered on each task so it removes itself from the set upon completion, keeping the set lean.

4. **`session:end` drain (15s timeout)**: The `on_session_end` handler iterates `_pending_tasks` and calls `asyncio.wait_for(asyncio.shield(task), timeout=15.0)` for each. This gives in-flight naming tasks up to 15 seconds to complete before session teardown. If a task times out or is cancelled, the error is logged at `DEBUG` level and teardown continues — naming is best-effort.

5. **Internal 10s provider timeout**: Inside `_generate_name`, the LLM provider call is wrapped in `asyncio.wait_for(self._call_provider(prompt), timeout=10.0)`. This caps stalled or slow providers and ensures the naming task itself finishes well within the `session:end` 15-second drain window.

## How It Works

1. **Turn completion** — The `on_orchestrator_complete` hook fires after every `prompt:complete` event. It reads `turn_count` from `metadata.json` and adds 1 to get the current turn number.

2. **Initial naming** — Once `current_turn >= initial_trigger_turn` and no name exists, a background task calls the LLM with an `INITIAL_NAMING_PROMPT` that asks for a 2–6 word action-oriented name and a 1–2 sentence description.

3. **Graceful deferral** — If the LLM responds with `{"action": "defer"}`, the defer count for the session is incremented. The hook retries on subsequent turns until `max_retries` is exhausted.

4. **Description updates** — Once a name exists, the hook fires a background `DESCRIPTION_UPDATE_PROMPT` every `update_interval_turns` turns. The LLM responds with `{"action": "set", ...}` (update) or `{"action": "keep"}` (no change needed).

5. **Atomic metadata write** — Results are written to `metadata.json` via a temp-file-and-replace pattern to prevent partial writes from corrupting the file.

## Metadata Fields

The hook adds these fields to `metadata.json`:

```json
{
  "name": "Auth bug investigation",
  "description": "Debugging OAuth2 token refresh race conditions",
  "name_generated_at": "2024-01-07T12:05:00Z",
  "description_updated_at": "2024-01-07T12:30:00Z"
}
```
