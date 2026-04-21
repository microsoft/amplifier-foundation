# Hooks: Code-Decided Lifecycle Observers

## What It Is

A hook is a lifecycle observer that fires automatically on specific events in the
agent execution cycle. Unlike tools (which the LLM chooses to call), hooks are
code-decided -- they fire on events like session start, tool calls, LLM responses,
etc. Hooks can observe, block operations, modify data, inject context into the
agent's conversation, and request user approval.

## How It Works

### The Hook Protocol

```python
class HookHandler(Protocol):
    async def __call__(self, event: str, data: dict) -> HookResult: ...
```

Any async callable matching `(str, dict) -> HookResult` satisfies it.

### The 5 Actions

| Action | Power | Blocking? |
|--------|-------|-----------|
| `continue` | Observe only, proceed normally | No |
| `deny` | Block the operation entirely | Yes, short-circuits |
| `ask_user` | Approval gate -- prompt user | Yes |
| `inject_context` | Add text to agent's conversation | No, merged |
| `modify` | Transform event data | No, chained |

Action precedence: `deny` always wins. Multiple `inject_context` results merge.

### HookResult Fields

Beyond the core `action`, `data`, `reason`, HookResult has:
- **Context injection**: `context_injection`, `context_injection_role` (system/user),
  `ephemeral` (temporary, current turn only)
- **Approval gates**: `approval_prompt`, `approval_options`, `approval_timeout`
- **Output control**: `suppress_output`, `user_message`, `user_message_level`

### Lifecycle Events (33 total)

Key events for hook authors:
- `session:start`, `session:end`, `session:fork`
- `prompt:submit`, `prompt:complete`
- `tool:pre`, `tool:post`, `tool:error`
- `provider:request`, `provider:response`
- `context:pre_compact`, `context:post_compact`
- `execution:start`, `execution:end`

### Real Hook Implementations

| Hook | Events | Actions | Purpose |
|------|--------|---------|---------|
| hooks-process-guard | tool:pre | deny, inject | Block bash when too many processes |
| hooks-progress-monitor | tool:post | inject | Detect analysis paralysis |
| hooks-session-naming | prompt:complete | continue | Auto-generate session names |
| hooks-todo-display | tool:pre/post | continue | Render visual todo progress |
| hooks-deprecation | session:start | inject | Warn about deprecated bundles |
| hooks-status-context | provider:request | inject | Inject git/environment status |
| hooks-mode (modes bundle) | provider:request, tool:pre | deny, inject | Enforce mode tool policies |

### Composing in Bundles

```yaml
hooks:
  - module: hooks-progress-monitor
    source: git+https://...
    config:
      read_threshold: 30
      same_file_threshold: 3
```

Hooks are composed via behavior YAML files, just like tools.

### Key Implementation Files

- `amplifier-core/python/amplifier_core/interfaces.py:215-229` -- HookHandler protocol
- `amplifier-core/python/amplifier_core/models.py:103-298` -- HookResult (15 fields)
- `amplifier-core/python/amplifier_core/events.py` -- All 33 event constants
- `amplifier-core/docs/HOOKS_API.md` -- Complete API documentation
- `amplifier-foundation/modules/hooks-progress-monitor/` -- Real implementation

## Relevance to System Design Bundle

Hooks offer powerful automated feedback mechanisms for system design work:

- **Design completeness monitor**: Like `hooks-progress-monitor` detects analysis
  paralysis, a design hook could monitor whether the agent is exploring all
  dimensions of a design problem. If the agent produces an architecture without
  addressing failure modes, the hook could inject a reminder: "Design appears
  incomplete: no failure scenarios addressed."

- **Constraint violation detector**: A hook on `tool:pre` (before file writes)
  could check if a proposed implementation violates established design constraints.
  For example, if the design specifies "stateless services" and the agent writes
  code with session state, the hook could warn or block.

- **Design phase enforcement**: The modes bundle already does this -- hooks enforce
  tool policies during different phases. A system design bundle could use the same
  pattern: block implementation tools during design exploration, block design tools
  during implementation.

- **Architectural drift detection**: A hook on `tool:post` after file edits could
  compare changes against the system's architectural model and flag drift. "This
  change introduces a dependency between modules X and Y that the architecture
  shows as independent."

- **Context injection for design prompts**: Hooks can inject context before every
  LLM call. A design hook could inject the current system model, active constraints,
  or design checklist items as ephemeral context, ensuring the agent always has the
  design context available without permanently consuming context window.

The key advantage of hooks over context files is that hooks are **dynamic** -- they
can respond to what's happening in real-time and inject different context based on
the current state. The disadvantage is implementation complexity (Python module +
protocol compliance).

For initial experiments, the modes system (which is built on hooks) gives us most
of the design-phase enforcement we need without writing custom hooks. Custom hooks
would be valuable later for automated design quality feedback.

## Context Window Impact

Hooks interact with the context window through the `inject_context` action, which
has two distinct modes with very different token economics.

### Ephemeral Injections (recommended default)

When a hook returns `inject_context` with `ephemeral=True`:

1. The orchestrator calls `get_messages_for_request()` (compaction runs here)
2. The ephemeral content is appended to the returned message list **after** compaction
3. The LLM sees it in the current request
4. The content is never passed to `context.add_message()` -- it evaporates after the call
5. Next turn, the hook fires again and re-creates the injection fresh

| Factor | Detail |
|--------|--------|
| Storage | None -- not in message history |
| Compactable | No -- bypasses compaction entirely |
| Persistence | Single LLM call only |
| Cost model | Fixed per turn while the hook fires |
| Accumulation | Never compounds -- re-created fresh each time |

This is the pattern used by `hooks-mode` (mode body), `hooks-skills-visibility`
(skill list), and `hooks-status-context` (git/environment status).

### Persistent Injections (use sparingly)

When a hook returns `inject_context` with `ephemeral=False` (or calls
`context.add_message()` directly):

| Factor | Detail |
|--------|--------|
| Storage | Message history (permanent) |
| Compactable | Yes -- treated like any other message |
| Persistence | Until compacted away |
| Cost model | Accumulates over time |
| Risk | Can grow unboundedly if hook fires frequently |

Persistent injections are appropriate when the information must be visible across
multiple turns and the hook doesn't fire often. They're dangerous for hooks that
fire on every `provider:request` because each injection adds to the message
history.

### The `tool:post` Timing

For hooks on `tool:post` events, ephemeral injections are queued in
`_pending_ephemeral_injections` and applied at the **start of the next loop
iteration** (not the current one). This creates a one-iteration delay:

```
Tool executes -> tool:post hook fires -> injection queued
-> Next iteration starts -> queued injection applied -> LLM sees it
```

The `append_to_last_tool_result=True` option merges the injection into the
previous tool result message body rather than creating a new message. This avoids
the structural oddity of a bare system message appearing between tool results.

### Budget Enforcement

The coordinator tracks injection budgets per turn:

| Limit | Type | Behavior |
|-------|------|----------|
| `injection_size_limit` | Hard limit (characters) | Raises ValueError if exceeded |
| `injection_budget_per_turn` | Soft limit (tokens, chars/4) | Warning logged, injection proceeds |

Both ephemeral and non-ephemeral injections count against the per-turn budget.
Multiple hooks firing on the same event accumulate toward the limit.

### Token Cost of Real Hooks

| Hook | Event | Injection type | Approximate per-turn cost |
|------|-------|---------------|--------------------------|
| hooks-mode | provider:request | Ephemeral | Size of mode file (~500-2,000 tokens) |
| hooks-skills-visibility | provider:request | Ephemeral | ~1,000-1,500 tokens (scales with skill count) |
| hooks-status-context | provider:request | Ephemeral | ~200-400 tokens (git status + env info) |
| hooks-progress-monitor | tool:post | Ephemeral (queued) | ~100-200 tokens (only when triggered) |

### Design Guidelines for Hook Authors

1. **Default to `ephemeral=True`.** Unless the information must survive across
   turns, ephemeral injections avoid accumulation in message history.
2. **Be size-conscious on `provider:request` hooks.** These fire on every LLM
   call, including tool-loop iterations. A 500-token injection on a turn with 5
   tool calls costs 2,500 tokens.
3. **Use `append_to_last_tool_result` for `tool:post` feedback.** This avoids
   creating extra messages that fragment the conversation structure.
4. **Consider conditional injection.** Not every turn needs the same context.
   Check session state before injecting to skip turns where the information isn't
   relevant.
