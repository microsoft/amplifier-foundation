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
- **Attributable**: Every `llm:*` event a naming call emits carries `data.purpose = "session-naming"`, so analyzers can exclude it from the session's own work (see [Event Attribution](#event-attribution))

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

**Naming never calls a provider this session did not select.** Every path below
ends on either the session's own conversation provider or a *same-vendor* sibling
of it. There is no arbitrary fallback.

Resolution order:

1. **`model_role`** — Resolved against the `model_role_resolver` capability
   (registered by whichever routing bundle is active — typically the
   matrix-based one shipped in `amplifier-bundle-routing-matrix`). Defaults to
   `"fast"`. A resolved candidate is **accepted only if** it is mounted in this
   session **and** its `get_info().id` matches the vendor of the session's own
   provider. Anything else is refused with a WARNING (once per session).

2. **The session's own conversation provider** — the `conversation.provider_pin`
   pin when one is set, otherwise the same priority ordering the streaming
   orchestrator uses to pick the conversation provider (`provider.priority`,
   then `provider.config["priority"]`, default 100, ties broken by mount order).
   No model override is applied on this path.

If the conversation is pinned to a provider that is no longer mounted, naming is
**skipped** for that turn rather than run on some other provider.

### Why the vendor check exists

Session naming used to resolve `model_role` through the routing matrix (whose
default matrix is openai) and, when the resolved name matched no mount, fall
through to `next(iter(providers.values()))` — an order-dependent, silent borrow
of whichever provider instance happened to be first in the mount dict. In an
Anthropic-pinned evaluation cell that emitted openai calls into the session's
event stream (321 foreign responses across 12 capture roots; see
`model_performance-egh`). Same-vendor siblings (`anthropic-sonnet` →
`anthropic-haiku`) remain allowed: that is the intended cheap-model routing.

## Event Attribution

A provider emits `llm:request` / `llm:response` through the coordinator it was
mounted with — the session's own — and the kernel stamps `session_id` and
`parent_id` defaults onto every event
(`amplifier_core/session.py`: `set_default_fields(...)`). A background naming
call therefore lands in the session's `events.jsonl` with `parent_id: null` and,
before this module stamped them, nothing at all to distinguish it from the root
agent's own turns.

Every event a naming call emits now carries:

```json
{"purpose": "session-naming", "origin_module": "hooks-session-naming"}
```

Excluding session naming from an analysis is then one predicate:

```jq
select(.data.purpose != "session-naming")
```

The stamp is applied to a naming-only *view* of the provider (a shallow copy
carrying a wrapping coordinator), built once per provider per session. The
shared provider instance is never mutated, so the foreground conversation's own
events are unaffected. If a provider's events cannot be stamped, the naming call
is **skipped** with a WARNING rather than emitted unattributably.

Note: the provider call has a 10 s hard timeout. A timed-out call can leave a
stamped `llm:request` with no matching `llm:response` — the stamp is what makes
that orphan identifiable rather than mysterious.

### Optional Dependency: hooks-routing

`hooks-routing` is an **optional runtime dependency**. The module degrades gracefully:

- If `hooks-routing` is not installed, the module falls back to the session's own
  conversation provider (debug-logged). Falling back is the expected behaviour when
  the routing module is absent.
- To disable routing explicitly and always use the session's own provider, set
  `model_role: null`.

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
