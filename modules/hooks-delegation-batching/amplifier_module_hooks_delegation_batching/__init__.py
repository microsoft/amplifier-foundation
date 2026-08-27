"""Delegation Batching Hook Module

Detects that a root has just completed its Nth consecutive delegation wave
with no intervening non-delegate work, and injects one ephemeral reminder
before the next wave is planned.

This is Stage 2 ("Option C") of the plan-then-dispatch parallel-delegation
work. Stage 1 (prose guidance in the delegate tool description and the
agent-facing context docs) addresses the same defect at the instruction
layer. This hook reinforces it at the exact decision point: after wave N's
results land, before wave N+1 is planned -- the moment a static context file
is furthest from the model's attention.

Mechanism (proven in-repo, not hypothetical):
- ``modules/hooks-progress-monitor`` returns
  ``HookResult(action="inject_context", ..., ephemeral=True,
  append_to_last_tool_result=True)`` from a ``tool:post`` handler.
- The orchestrator (``amplifier-module-loop-streaming``, a separate repo)
  consumes exactly that shape from ``tool:post`` and applies it on the next
  iteration -- i.e. after the current wave's results land, before the next
  wave is planned. That is precisely the decision point this hook targets.

Detection:
- Each assistant turn that emits one or more ``delegate`` calls shares one
  ``parallel_group_id``. A new ``parallel_group_id`` observed on ``tool:post``
  counts as one delegation wave.
- Non-delegate ``tool:post`` events mark the session as "interrupted": real
  work happened between waves, so the next wave genuinely could not have
  been planned earlier, and the wave chain resets.
- A wave is only nudged once (``nudged_groups`` dedupe), and at most
  ``max_nudges`` times per session, and only once ``wave_count >=
  min_wave_index``.
- When ``narrow_wave_only`` is set (the default), only narrow waves (fewer
  than 2 observed members so far) are nudged -- a wide wave already batched.

Ships opt-in and default-off: this module is not composed into
``behaviors/agents.yaml``. See ``behaviors/delegation-batching.yaml`` and
this module's README for how to enable it.
"""

from dataclasses import dataclass, field
from typing import Any

from amplifier_core import HookResult

NUDGE = """<system-reminder source="hooks-delegation-batching">
**Delegation wave {wave_count} just completed.**

This is your {wave_count}th consecutive wave of delegations with no other work in
between. Before you dispatch the next one, check: could these delegations have gone
out together?

Delegations emitted in ONE turn run concurrently. Split across turns, they run
sequentially and you block on each. If the next delegation does not consume a result
you just received, it was independent - and it should have gone out with the last wave.

Emit every remaining independent delegation NOW, in this one turn.
</system-reminder>"""


@dataclass
class DelegationBatchingConfig:
    """Configuration for delegation wave batching detection.

    All fields optional; defaults match the spec.
    """

    enabled: bool = True  # master switch
    min_wave_index: int = 2  # first wave index that may be nudged (1-based)
    max_nudges: int = (
        3  # per-session cap; prevents nagging legitimately dependent waves
    )
    narrow_wave_only: bool = True  # only nudge when the completed wave had < 2 members


@dataclass
class DelegationBatchingState:
    """Per-mounted-instance state.

    Not keyed by session id. Modules mount per session, so the instance
    already *is* the session scope; ``tool:post`` does not carry
    ``session_id``, and replicating a session_id-keyed dict with a
    ``"default"`` fallback would be a misleading pattern that in practice
    always yields exactly one bucket.
    """

    wave_count: int = 0  # completed delegate waves this session
    nudges_issued: int = 0
    seen_groups: set[str] = field(default_factory=set)  # parallel_group_ids observed
    group_sizes: dict[str, int] = field(
        default_factory=dict
    )  # group_id -> member count (lower bound)
    nudged_groups: set[str] = field(default_factory=set)  # dedupe: one nudge per wave
    last_group_id: str | None = None
    interrupted: bool = False  # a non-delegate tool ran since last wave


class DelegationBatchingHooks:
    """Hook handler that detects sequential delegation waves and nudges batching."""

    def __init__(self, config: DelegationBatchingConfig, hooks: Any = None):
        self.config = config
        self.state = DelegationBatchingState()
        # Optional hook-emitter used for the `delegate:batching_nudge`
        # telemetry event. May be None in unit tests that construct the
        # hook directly without an emitter.
        self._hooks = hooks

    async def handle_tool_post(self, _event: str, data: dict[str, Any]) -> HookResult:
        """Handle a `tool:post` event.

        A nudge hook must never be able to fail a tool call -- any internal
        error is caught and swallowed as a plain `continue`.
        """
        try:
            return await self._process(data)
        except Exception:
            return HookResult(action="continue")

    async def _process(self, data: dict[str, Any]) -> HookResult:
        """Run the wave-detection algorithm for one `tool:post` payload."""
        if not self.config.enabled:
            return HookResult(action="continue")

        state = self.state
        tool_name = data.get("tool_name", "")

        if tool_name != "delegate":
            # Real work between waves means the next wave genuinely could
            # not have been planned earlier -- reset the chain.
            state.interrupted = True
            return HookResult(action="continue")

        group_id = data.get("parallel_group_id")
        if not group_id:
            # Cannot attribute this delegate call to a wave.
            return HookResult(action="continue")

        state.group_sizes[group_id] = state.group_sizes.get(group_id, 0) + 1

        if group_id not in state.seen_groups:
            state.seen_groups.add(group_id)
            state.wave_count += 1
            if state.interrupted:
                # A wave that follows real work starts a fresh chain.
                state.wave_count = 1
                state.interrupted = False
            state.last_group_id = group_id

        if group_id in state.nudged_groups:
            return HookResult(action="continue")
        if state.wave_count < self.config.min_wave_index:
            return HookResult(action="continue")
        if state.nudges_issued >= self.config.max_nudges:
            return HookResult(action="continue")
        if self.config.narrow_wave_only and state.group_sizes[group_id] >= 2:
            return HookResult(action="continue")

        state.nudged_groups.add(group_id)
        state.nudges_issued += 1

        await self._emit_nudge_event(group_id, state)

        return HookResult(
            action="inject_context",
            context_injection=NUDGE.format(wave_count=state.wave_count),
            context_injection_role="user",
            ephemeral=True,
            append_to_last_tool_result=True,
        )

    async def _emit_nudge_event(
        self, group_id: str, state: DelegationBatchingState
    ) -> None:
        """Emit the `delegate:batching_nudge` telemetry event, if wired."""
        if self._hooks is None:
            return
        await self._hooks.emit(
            "delegate:batching_nudge",
            {
                "wave_count": state.wave_count,
                "parallel_group_id": group_id,
                "wave_size": state.group_sizes[group_id],
                "nudges_issued": state.nudges_issued,
                "metadata": None,
            },
        )


async def mount(
    coordinator: Any, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Mount the delegation batching hooks module.

    Config options:
        enabled: bool (default: True) - master switch
        min_wave_index: int (default: 2) - first wave index that may be nudged
        max_nudges: int (default: 3) - per-session cap on nudges
        narrow_wave_only: bool (default: True) - only nudge narrow (< 2 member) waves
    """
    config = config or {}

    batching_config = DelegationBatchingConfig(
        enabled=config.get("enabled", True),
        min_wave_index=config.get("min_wave_index", 2),
        max_nudges=config.get("max_nudges", 3),
        narrow_wave_only=config.get("narrow_wave_only", True),
    )

    handler = DelegationBatchingHooks(batching_config, hooks=coordinator.hooks)

    # Declare the event this module emits so the session capture hooks
    # auto-discover and record it via the observability.events contribution
    # channel. See: core:docs/specs/CONTRIBUTION_CHANNELS.md; template:
    # tool-delegate/__init__.py.
    coordinator.register_contributor(
        "observability.events",
        "hooks-delegation-batching",
        lambda: ["delegate:batching_nudge"],
    )

    # Register hook - runs after tool execution to detect wave patterns.
    coordinator.hooks.register(
        "tool:post",
        handler.handle_tool_post,
        priority=50,
        name="hooks-delegation-batching",
    )

    return {
        "name": "hooks-delegation-batching",
        "version": "0.1.0",
        "description": "Detects sequential delegation waves and nudges toward batching",
        "config": {
            "enabled": batching_config.enabled,
            "min_wave_index": batching_config.min_wave_index,
            "max_nudges": batching_config.max_nudges,
            "narrow_wave_only": batching_config.narrow_wave_only,
        },
    }
