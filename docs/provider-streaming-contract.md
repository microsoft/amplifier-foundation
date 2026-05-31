# Provider Streaming Contract

The conformance spec every Amplifier provider must satisfy to support "proper" token
streaming, derived from the reference implementation in
`amplifier-module-provider-anthropic`. Implement each provider to this contract
exactly — the renderer (hooks-streaming-ui) and any other consumer depend on these
event names and payload shapes being identical across providers.

## The four events

All emitted during `complete()` via `self.coordinator.hooks.emit(name, payload)`.
All events for a single call share ONE `request_id` (a `str(uuid.uuid4())` generated
once at the top of the streaming path).

There is **one delta event for all block content** (`llm:stream_block_delta`). It is
agnostic to block type — text and thinking/reasoning fragments use the SAME event,
distinguished by `block_type` (carried on every block event). There is NO separate
`thinking_delta` event. The renderer (and any consumer) routes on `block_type`.

### `llm:stream_block_start`
Emitted once per content block, before any of its deltas.
```python
{
  "request_id": str,     # uuid4, constant for the whole call
  "block_index": int,    # 0-based, order the block appears in the response
  "block_type": str,     # "text" | "thinking" | "tool_use"
  "name": str,           # CONDITIONAL: only when block_type == "tool_use" and a tool name exists
}
```

### `llm:stream_block_delta`  (ALL block content — text AND thinking)
Emitted for each non-empty content fragment of ANY block. The same event carries text
and reasoning fragments alike; `block_type` tells the consumer which it is.
```python
{
  "request_id": str,
  "block_index": int,    # same index as the block_start
  "block_type": str,     # "text" | "thinking" — the type of THIS block (matches its block_start)
  "sequence": int,       # 0-based, per-block counter (see note)
  "text": str,           # non-empty fragment (text or reasoning)
}
```

### `llm:stream_block_end`
Emitted when a content block completes.
```python
{
  "request_id": str,
  "block_index": int,
  "block_type": str,     # the type of the block that ended
}
```

### `llm:stream_aborted`
Emitted ONLY if a mid-stream exception occurs AND at least one delta was already
emitted (a partial stream). Emitted just before re-raising.
```python
{
  "request_id": str,
  "error": { "type": str, "msg": str },   # exception class name + message
}
```

## Semantics that must not drift

- **One delta event for all content.** Text and thinking fragments both use
  `llm:stream_block_delta`; there is NO `thinking_delta`. Consumers route on
  `block_type` (carried on every block event), never on the event name.
- **`sequence` is per-block and 0-based.** One counter per `block_index` (not a
  global counter); it just counts the deltas within that one block.
- **`block_index` is a single shared index space** across all block types in the
  response (thinking, text, tool_use all draw from the same 0-based sequence).
- **Empty fragments are never emitted.** Guard every delta with `if text:`.
- **tool_use blocks:** emit `block_start` (with `name`) and `block_end`; do NOT emit
  per-fragment deltas for tool-input JSON. Assemble tool calls into the final
  `ChatResponse` as usual.
- **Signature/seal fragments** (Anthropic `signature_delta`, Gemini
  `thought_signature`, etc.) are consumed silently — no event. They only affect the
  final assembled `ChatResponse`.

## Emit wiring (already present in every provider)

Every provider already receives `coordinator` and stores `self.coordinator`. Reuse it.

```python
# guard every emit:
if self.coordinator and hasattr(self.coordinator, "hooks"):
    await self.coordinator.hooks.emit(event_name, payload)

# hot-loop optimization (evaluate the guard once before the loop):
hooks_available = self.coordinator and hasattr(self.coordinator, "hooks")
```

There is no other bus, callback, or request-field emitter. `coordinator.hooks.emit`
is the entire surface.

## SDK consumption pattern

```python
request_id = str(uuid.uuid4())
seq: dict[int, int] = {}          # block_index -> next sequence number
block_types: dict[int, str] = {}  # block_index -> type (for block_end lookup)
partial_emitted = False

try:
    async with <sdk native stream>(**params) as stream:   # provider-specific
        async for event in stream:
            # map provider SDK events -> the 4 contract events.
            # text fragments AND reasoning/thinking fragments BOTH emit
            # llm:stream_block_delta (with block_type set from the open block).
            ...
    response = <assemble final ChatResponse>               # provider-specific
except Exception as e:
    if partial_emitted and hooks_available:
        await self.coordinator.hooks.emit("llm:stream_aborted",
            {"request_id": request_id, "error": {"type": type(e).__name__, "msg": str(e)}})
    raise
```

For SDKs that expose no explicit block boundaries (Chat Completions, Gemini, Ollama),
**synthesize** `block_start`/`block_end` by detecting block-type transitions
(thinking→text, etc.) and emitting `block_end` for every started block when the
stream ends.

## Per-request stream override

```python
self.use_streaming = self.config.get("use_streaming", True)   # class default: True

# per-call override, used by background tasks (e.g. session-namer) that must NOT stream:
_meta = getattr(request, "metadata", None)
_use_streaming = self.use_streaming
if isinstance(_meta, dict) and _meta.get("stream") is False:   # identity check, not truthiness
    _use_streaming = False
```

- The override is a **local** decision; it must NOT mutate `self.use_streaming`.
- Keep a **non-streaming fallback path**. It emits NO `llm:stream_*` events but still
  emits `llm:request` / `llm:response` exactly as today.

## Conformance checklist (per provider)

- [ ] `request_id` = one uuid4 for the whole call; present on all four events.
- [ ] ONE delta event (`llm:stream_block_delta`) for text AND thinking; NO
      `thinking_delta`. `block_type` on every block event carries the distinction.
- [ ] `block_index` shared 0-based space; `sequence` per-block.
- [ ] Empty fragments never emitted.
- [ ] `block_start` for every block (with `name` for tool_use); `block_end` for every
      block that started (including synthesized ends for chunk-based SDKs).
- [ ] `llm:stream_aborted` only after a partial emit.
- [ ] `use_streaming` config default True; `request.metadata["stream"] is False` override.
- [ ] Non-streaming fallback preserved; `llm:request`/`llm:response` unchanged.
- [ ] Unit tests assert the exact event names + payload keys above.

The `mock` provider is the canonical deterministic fixture for this contract: it emits
a synthetic stream from a canned response and is the reference shape new providers and
the renderer test against.

## Event dispositions (forward-looking)

Some events are emitted for live consumers (the renderer) but are not worth
persisting. Whether an event is worth persisting is a property of the EVENT, not of
each logger — so it is declared once, here, and every persistence sink honors it
identically.

**The model (target).** An event may carry an ADDITIVE, ADVISORY set of disposition
tags:

- *additive* — tags are orthogonal; an event can be both `transient` and `sensitive`.
  It is a set, never a single enum.
- *open* — a new disposition is a new string; unknown tags are ignored, never an error.
  Adding one touches zero existing producers or consumers.
- *advisory* — a missed honor is suboptimal, never harmful. The kernel stays a dumb
  pipe: it neither defines nor enforces dispositions. Producers declare; consumers honor.

Disposition vocabulary:

- `transient` — high-frequency, live-consumed, not worth persisting. The only member
  today is the streaming delta.

**Load-bearing dispositions do NOT ride this mechanism.** A disposition whose violation
is harmful (e.g. a must-redact secret) requires an enforcement choke point — a redaction
stage before any sink sees the data — not an honor-system tag. This is why
data-sensitivity stays in `strip_raw`, separate from dispositions.

**Current realization (stopgap).** Until producers declare dispositions directly,
persistence sinks default to excluding the transient streaming deltas by the fnmatch
pattern `llm:stream_*delta`. The structural streaming events (`llm:stream_block_start`,
`llm:stream_block_end`, `llm:stream_aborted`) are low-volume and kept. The two sinks
today — the session logger (`amplifier-module-hooks-logging`) and the
context-intelligence hook (`hook-context-intelligence`) — each carry this same default
independently, aligned by THIS convention, not by shared code.

**Capturing transient events while debugging.** Set the sink's exclude list to empty to
log/dispatch everything, including the deltas:

```yaml
config:
  exclude_events: []     # exclude nothing -> capture transient deltas
```

The value replaces (not merges with) the default. Scope it to a workspace
`.amplifier/settings.yaml` or a throwaway profile so it reverts when you leave, and flip
only the sink you need — e.g. capture deltas in the session log's `events.jsonl` without
also dispatching them to the context-intelligence graph server.

**Graduate** from the `llm:stream_*delta` pattern to producer-declared tags (via the
existing advisory contribution channel — never a kernel change) when a second
disposition axis, a per-instance need, or real cross-sink drift appears.
