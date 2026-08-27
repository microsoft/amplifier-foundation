"""Same-batch read coalescer.

Deduplicates exact-key repeats of `read_file` (and, for free, `load_skill`)
within a single parallel tool batch of a single session.

Measured basis (5 gpt-5.6 runs, see the implementation spec): 1 196 same-batch
duplicate `read_file` calls -- 37% of all reads, 87% of duplicate tokens --
with byte-identical content in 100% of cases. Deduplicating just this class
removed 16.4% of all input tokens across the measured runs, with zero
information loss, because the duplicate's content is always delivered
adjacent to the marker in the same tool-result turn.

Mechanism (see `loop-streaming` parallel path, `_execute_tool_only`): a
`tool:post` hook can replace the content the LLM sees by returning
`HookResult(action="modify", data={..., "result": <new str>})`. Detection of
"did a hook modify the result" is by object identity (`is not result_data`),
so this hook MUST return a *new* dict with a *new* str under "result" --
mutating `data` in place and returning it is a silent no-op. Returning a
`str` (not a dict/list) avoids `json.dumps` wrapping the marker in JSON noise.

The sequential tool-call path emits the same `tool:post` payload minus
`parallel_group_id` (it is `None` there), which is exactly the discriminator
this hook needs: no batch id => no batch => no dedupe. Sequential calls are
therefore never deduped, by construction, not by a separate check.

Scope, by design:
    - Per-session, in-memory only. One dict on this hook instance,
      `parallel_group_id -> key -> _Entry`, bounded by `max_batches` as an
      insertion-ordered LRU. A hook instance is mounted once per session, so
      this state dies with the session: no tree id, no cross-session state,
      no cleanup path needed, no leak possible.
    - Detection is exact-content-match, not a staleness heuristic: on a hit,
      the newly observed content's sha256 is compared against the sha256
      recorded for the first occurrence. A mismatch means the file changed
      *within this batch* -- the second content is delivered untouched and
      counted as `dedupe:divergent` (the invalidation story is "compare the
      actual bytes in hand", not mtime or any other guess).
    - Skill-load dedupe (`load_skill`) is not a separate mechanism. It is the
      exact same hook with `load_skill` added to the `tools` allowlist by
      default -- zero incremental code.

Explicitly out of scope (see the spec's Sections 6.1/6.2 for the rejected
alternatives and why): cross-session/cross-agent memos, cross-turn dedupe
within one session, tree-scoped state, and mtime/staleness heuristics. None
of those are implemented here, and none of this hook's state survives a
turn boundary in a way that could support them.

Config surface (all keys optional; ships default-OFF):

    hooks:
      - module: hooks-dedupe
        config:
          enabled: false                       # Stage 1 ships default-OFF
          tools: ["read_file", "load_skill"]
          min_bytes: 1000
          max_batches: 8

Telemetry: `dedupe:coalesced` (one per marker emitted) and `dedupe:divergent`
(one per same-key content mismatch within a batch -- should be ~0; a
non-zero rate means a file is being mutated mid-batch). Both are declared via
`register_contributor("observability.events", ...)` so they are
auto-captured by the existing session logging hooks -- no new instrumentation
is required to measure this feature.

Note on the "re-read after marker" guardrail metric from the spec's DTU
measurement plan (Section 9): that metric is computed *externally*, by a
post-hoc scan of `events.jsonl` comparing `dedupe:coalesced` events in turn N
against ordinary `read_file` `tool:post` events (already emitted by
loop-streaming, independent of this hook) for the same key in turn N+1. It is
NOT tracked as live state inside this hook, because doing so would require
remembering marked keys across a turn boundary -- exactly the cross-turn
state this module's scope explicitly excludes (see above). No code in this
module computes it; `dedupe:coalesced`'s existing `target` field is
sufficient for the external analysis to join against.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from amplifier_core import HookResult

logger = logging.getLogger(__name__)

__all__ = ["DedupeHook", "mount"]

#: Default tool allowlist. Adding "load_skill" here *is* the entirety of the
#: skill-load dedupe mechanism -- no separate code path exists for it.
DEFAULT_TOOLS: tuple[str, ...] = ("read_file", "load_skill")

#: Below this many characters, the marker (~45 tokens) is not clearly
#: cheaper than the payload it would replace.
DEFAULT_MIN_BYTES = 1000

#: Insertion-ordered LRU bound on retained batches, so a session with many
#: parallel tool batches cannot grow this hook's state unboundedly.
DEFAULT_MAX_BATCHES = 8

#: read_file's own parameter defaults (amplifier-module-tool-filesystem
#: read.py), normalised here so a bare `read(path)` and an explicit
#: `read(path, offset=1, limit=2000)` collide onto the same dedupe key.
READ_FILE_DEFAULT_OFFSET = 1
READ_FILE_DEFAULT_LIMIT = 2000

#: (tool_name, canonical_input_str)
_Key = tuple[str, str]


@dataclass
class _Entry:
    """The content-holding call for one dedupe key within one batch."""

    tool_call_id: str
    sha256_hex: str
    content: str
    n_lines: int


def _build_key_and_target(tool_name: str, tool_input: Any) -> tuple[_Key, str] | None:
    """Parse `tool_input` into a dedupe key plus a human-readable target.

    `tool_input` may arrive as a dict (the common case) or a JSON string;
    parsed defensively, returning None (=> pass through) on any shape this
    hook doesn't understand rather than raising.

    The key's second element folds in offset/limit for read_file (so
    differing windows over the same file are distinct keys); `target` is
    just the file path or skill name, used for telemetry and the marker
    text, and is deliberately narrower than the key.
    """
    parsed = tool_input
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
    if not isinstance(parsed, dict):
        return None

    if tool_name == "read_file":
        path = parsed.get("file_path")
        if not isinstance(path, str) or not path:
            return None
        offset = parsed.get("offset")
        limit = parsed.get("limit")
        if offset is None:
            offset = READ_FILE_DEFAULT_OFFSET
        if limit is None:
            limit = READ_FILE_DEFAULT_LIMIT
        return (("read_file", f"{path}|{offset}|{limit}"), path)

    if tool_name == "load_skill":
        skill_name = parsed.get("skill_name")
        if not isinstance(skill_name, str) or not skill_name:
            return None
        return (("load_skill", skill_name), skill_name)

    return None


class DedupeHook:
    """Coalesces byte-identical repeated tool results within one parallel
    tool batch of one session. See module docstring for the full contract.
    """

    def __init__(
        self,
        hooks: Any,
        enabled: bool = False,
        tools: list[str] | tuple[str, ...] = DEFAULT_TOOLS,
        min_bytes: int = DEFAULT_MIN_BYTES,
        max_batches: int = DEFAULT_MAX_BATCHES,
    ) -> None:
        self.hooks = hooks
        self.enabled = enabled
        self.tools = set(tools)
        self.min_bytes = min_bytes
        self.max_batches = max_batches
        # parallel_group_id -> key -> holder entry. Insertion-ordered so the
        # oldest batch is evicted first once max_batches is exceeded.
        self._batches: OrderedDict[str, dict[_Key, _Entry]] = OrderedDict()

    async def handle_tool_post(self, event: str, data: dict[str, Any]) -> HookResult:
        """`tool:post` handler. Returns `continue` on the first failing
        guard; the guard order matches the spec (Section 4.4) exactly.
        """
        if not self.enabled:
            return HookResult(action="continue")

        pgid = data.get("parallel_group_id")
        if not pgid:
            # Sequential calls have no parallel_group_id -- never deduped.
            # This single guard is the entire losslessness argument.
            return HookResult(action="continue")

        tool_name = data.get("tool_name")
        if tool_name not in self.tools:
            return HookResult(action="continue")

        result = data.get("result")
        if not isinstance(result, dict) or result.get("success") is not True:
            return HookResult(action="continue")

        output = result.get("output")
        if not isinstance(output, dict):
            return HookResult(action="continue")

        content = output.get("content")
        if not isinstance(content, str):
            return HookResult(action="continue")

        if len(content) < self.min_bytes:
            return HookResult(action="continue")

        parsed = _build_key_and_target(tool_name, data.get("tool_input"))
        if parsed is None:
            return HookResult(action="continue")
        key, target = parsed

        # --- Critical section: no `await` between lookup and store. The
        # batch executes under asyncio.gather, so several handlers for one
        # batch are interleaved on one event loop; this section must be
        # atomic under that cooperative scheduling (spec Section 4.4). ---
        batch = self._batches.get(pgid)
        if batch is None:
            batch = {}
            self._batches[pgid] = batch
            self._evict_oldest_batches()

        digest = hashlib.sha256(
            content.encode("utf-8", errors="surrogatepass")
        ).hexdigest()
        entry = batch.get(key)

        if entry is None:
            lines_read = output.get("lines_read")
            n_lines = (
                lines_read if isinstance(lines_read, int) else content.count("\n") + 1
            )
            batch[key] = _Entry(
                tool_call_id=str(data.get("tool_call_id")),
                sha256_hex=digest,
                content=content,
                n_lines=n_lines,
            )
            return HookResult(action="continue")
        # --- End critical section. Everything below only reads `entry` and
        # never mutates batch state, so it is safe across an `await`. ---

        if digest != entry.sha256_hex:
            await self.hooks.emit(
                "dedupe:divergent",
                {"tool_name": tool_name, "parallel_group_id": pgid, "target": target},
            )
            return HookResult(action="continue")

        digest12 = digest[:12]
        marker = (
            f"[deduped] Identical to tool call {entry.tool_call_id} in this same "
            f"parallel batch:\n{tool_name} {target} ({entry.n_lines} lines, "
            f"sha256:{digest12}). That result is present in this turn \u2014 read it "
            "there. Re-issue this call only if you need different lines "
            "(offset/limit)."
        )

        await self.hooks.emit(
            "dedupe:coalesced",
            {
                "tool_name": tool_name,
                "parallel_group_id": pgid,
                "holder_tool_call_id": entry.tool_call_id,
                "duplicate_tool_call_id": data.get("tool_call_id"),
                "target": target,
                "bytes_saved": len(content),
                "sha256": digest12,
            },
        )

        # New dict, new str under "result": the object-identity check in
        # loop-streaming (`returned_result is not result_data`) requires
        # this. Mutating `data` in place would be a silent no-op.
        modified_data = {**data, "result": marker, "full_output": result}
        return HookResult(action="modify", data=modified_data)

    def _evict_oldest_batches(self) -> None:
        while len(self._batches) > self.max_batches:
            self._batches.popitem(last=False)


async def mount(
    coordinator: Any, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Mount the same-batch read/skill coalescer hook.

    Ships default-OFF: `enabled` defaults to False, so merely composing this
    module into a bundle (e.g. via the `agents` behavior, which every
    sub-session in a delegation tree inherits) has zero runtime effect until
    a caller explicitly opts in via config.

    Args:
        coordinator: The Amplifier coordinator instance.
        config: Optional module configuration -- see module docstring for
            the full `enabled` / `tools` / `min_bytes` / `max_batches`
            config surface.

    Returns:
        A module descriptor dict (name/version/description/config), per the
        convention used by other in-tree foundation hook modules.
    """
    config = config or {}

    hook = DedupeHook(
        hooks=coordinator.hooks,
        enabled=bool(config.get("enabled", False)),
        tools=config.get("tools", list(DEFAULT_TOOLS)),
        min_bytes=int(config.get("min_bytes", DEFAULT_MIN_BYTES)),
        max_batches=int(config.get("max_batches", DEFAULT_MAX_BATCHES)),
    )

    # priority=5 is load-bearing: it must run *before* hooks-tool-truncation
    # (priority=10, in amplifier-bundle-attractor), so truncation sees an
    # already-short marker and no-ops, rather than dedupe hashing an
    # already-truncated string.
    coordinator.hooks.register(
        "tool:post", hook.handle_tool_post, priority=5, name="hooks-dedupe"
    )

    # Declare the events we emit so the session capture hooks (hooks-logging
    # + hook-context-intelligence) auto-discover and record them via the
    # observability.events contribution channel. Module-owned declaration --
    # see core:docs/specs/CONTRIBUTION_CHANNELS.md; template: tool-delegate,
    # hooks-deprecation.
    coordinator.register_contributor(
        "observability.events",
        "hooks-dedupe",
        lambda: ["dedupe:coalesced", "dedupe:divergent"],
    )

    return {
        "name": "hooks-dedupe",
        "version": "0.1.0",
        "description": "Coalesces byte-identical repeated tool results within one parallel tool batch",
        "config": {
            "enabled": hook.enabled,
            "tools": sorted(hook.tools),
            "min_bytes": hook.min_bytes,
            "max_batches": hook.max_batches,
        },
    }
