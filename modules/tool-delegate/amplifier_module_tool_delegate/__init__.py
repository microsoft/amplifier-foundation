"""
Agent delegation tool module.

Enables AI agents to spawn sub-sessions for complex subtasks via capability-based architecture.
This module implements the Delegate tool with enhanced context control and session management.

Key Design Points:
- Two-parameter context system: depth (how much) and scope (which content)
- Short session ID resolution (minimum 6 characters)
- Fixed tool inheritance: agent's explicit declarations always honored
- Dynamic description with agent list
- Configurable features via structured config

Config Options:
- features.self_delegation.enabled: Allow agent="self" (default: True)
- features.session_resume.enabled: Allow session resumption (default: True)
- features.context_inheritance.enabled: Allow context passing (default: True)
- features.context_inheritance.max_turns: Maximum turns for "recent" mode (default: 10)
- features.provider_selection.enabled: Allow provider preferences (default: True)
- settings.exclude_tools: Tools spawned agents should NOT inherit (default: ["tool-delegate"])
- settings.exclude_hooks: Hooks spawned agents should NOT inherit (default: [])
- settings.timeout: Maximum child-session execution time in seconds (default: 14400,
  i.e. 4 hours); set explicitly to None/null to disable. This is a Layer 3
  wall-clock BACKSTOP -- orchestrator-independent and intentionally generous
  (~12x the measured healthy upper bound). It exists for the cases a per-leg
  LLM-call budget (settings.max_llm_calls) cannot cover: an orchestrator with no
  budget support, or a single hung call. If a real orchestrator with a call
  budget makes this timeout fire in practice, that is a signal the budget itself
  needs attention, not that this default is too generous. Timeouts return the
  child session ID, but callers must wait for app-layer cancellation cleanup and
  persistence before attempting to resume it.
- settings.strict_model_role: When True, a model_role that resolves to no
  candidates raises ModelRoleUnresolvedError instead of silently falling
  back to the session default model (default: False). Regardless of this
  setting, the no-candidates case always emits a
  "delegate:model_role_unresolved" event so the silent substitution is
  observable.

Tool Parameters:
- agent: Agent to delegate to (e.g., 'foundation:explorer', 'self')
- instruction: Clear instruction for the agent
- session_id: Resume existing session (use full session_id from previous delegate call)
- context_depth: "none" | "recent" | "all" - HOW MUCH context (default: "recent")
- context_turns: Number of turns for "recent" mode (default: 5)
- context_scope: "conversation" | "agents" | "full" - WHICH content (default: "conversation")
- provider_preferences: Ordered list of provider/model preferences
"""

# Amplifier module metadata
__amplifier_module_type__ = "tool"

import asyncio
import inspect
import json
import logging
import math
import re
from collections.abc import Coroutine
from typing import Any

from amplifier_core import ModuleCoordinator, ToolResult

from amplifier_foundation import ProviderPreference
from amplifier_foundation.tracing import generate_sub_session_id

logger = logging.getLogger(__name__)


class ModelRoleUnresolvedError(RuntimeError):
    """Raised when ``model_role`` resolves to no candidates under strict mode.

    Only raised when ``settings.strict_model_role`` is ``True``. Signals
    that the requested ``model_role`` could not be resolved to any
    provider/model candidate against the installed providers, and the
    caller has opted out of the default silent-fallback-to-session-default
    behavior. See ``delegate:model_role_unresolved`` for the always-emitted
    observability event that accompanies both the strict and default paths.
    """


# ---------------------------------------------------------------------------
# Structured delegation return contract (flag-gated; see features.return_contract
# in this module's config, and _parse_return_contract() below for the parser).
#
# A sub-agent may append a fenced ```json block, shaped per RETURN_CONTRACT_SCHEMA,
# to the end of its normal prose response. Only "findings" is required, and only
# "claim" within each finding -- everything else defaults on parse. The parser
# never rejects a partially-good return; see _parse_return_contract's docstring.
#
# RETURN_CONTRACT_SCHEMA and RETURN_CONTRACT_INSTRUCTION are kept as pure
# literals (no computed expressions) even though neither lives inside a tool
# class -- foundation's static token-cost estimator
# (amplifier_foundation.bundle_docs.tool_schema) locates the FIRST `return {`
# after `def input_schema` inside this file's one class and ast.literal_eval's
# it (see the docstring warning on DelegateTool.input_schema below). These two
# module-level constants sit above the class entirely so they cannot interfere
# with that scan, but keeping them literal keeps them inspectable by any future
# tooling that walks this module the same way.
RETURN_CONTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["claim"],
                "properties": {
                    "claim": {"type": "string"},
                    "evidence": {"type": "string"},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                },
            },
        },
        "not_covered": {"type": "array", "items": {"type": "string"}},
        "artifacts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string"},
                    "description": {"type": "string"},
                },
            },
        },
    },
}

RETURN_CONTRACT_INSTRUCTION = """[STRUCTURED RETURN]
After your normal prose answer, append ONE fenced json block as the LAST thing
in your response:

```json
{
  "summary": "at most 3 sentences -- the answer in brief",
  "findings": [
    {"claim": "one assertion, stated so it can be carried forward verbatim",
     "evidence": "file:line, command run, or URL -- empty string if genuinely none",
     "confidence": "high | medium | low"}
  ],
  "not_covered": ["a thing in scope you did NOT examine"],
  "artifacts": [{"path": "file written or modified", "description": "what changed"}]
}
```

Only "findings" is required, and only "claim" within each finding. If your task
produced no investigative findings, return "findings": [] and describe the work
done in "summary". Write your normal prose answer first, then this block."""


def _return_contract_event_fields(contract: dict[str, Any]) -> dict[str, Any]:
    """Five additive fields for the ``delegate:agent_completed`` event.

    Shared by both the spawn and resume completion paths so the two emit
    sites can never drift from each other.

    ``contract_conformant`` and the four counts are all ``None`` together
    when the return-contract feature is disabled -- this distinguishes
    "feature off" from "feature on but the agent returned nothing usable"
    (``False`` / ``0``), which a bare ``0`` would hide.
    """
    conformant = contract.get("conformant")
    if conformant is None:
        return {
            "contract_conformant": None,
            "findings_count": None,
            "evidence_backed_count": None,
            "not_covered_count": None,
            "artifacts_count": None,
        }

    findings = contract.get("findings") or []
    return {
        "contract_conformant": conformant,
        "findings_count": len(findings),
        "evidence_backed_count": sum(
            1 for f in findings if isinstance(f, dict) and f.get("evidence")
        ),
        "not_covered_count": len(contract.get("not_covered") or []),
        "artifacts_count": len(contract.get("artifacts") or []),
    }


#: Routing kwargs the resume path threads to the app layer's
#: ``session.resume`` capability. Both are OPTIONAL on that capability --
#: see :func:`_supported_resume_routing_kwargs`.
_RESUME_ROUTING_KWARGS = ("provider_preferences", "model_role")


def _supported_resume_routing_kwargs(resume_fn: Any) -> set[str]:
    """Which of ``_RESUME_ROUTING_KWARGS`` ``resume_fn`` can actually accept.

    ``session.resume`` is an app-layer capability, so its signature is not
    ours to guarantee. The original contract was
    ``(sub_session_id, instruction)``; routing kwargs were added later
    (amplifier-app-cli #292). Sending a kwarg an older app layer does not
    declare would raise ``TypeError`` and break resume outright, so this
    reports exactly what the callee declares and the caller sends only that
    -- and logs a warning naming anything it had to hold back, because a
    silent drop is the very defect this threading exists to fix.

    A callable that cannot be introspected (some C-implemented or exotically
    wrapped callables) is treated as accepting NOTHING: preserving today's
    working call shape beats crashing a resume on a guess. That case is
    warned about at the call site too, so it is never silent either.
    """
    try:
        params = inspect.signature(resume_fn).parameters
    except (TypeError, ValueError):
        logger.warning(
            "Could not introspect the session.resume capability's signature; "
            "resuming without threading routing kwargs (%s)",
            ", ".join(_RESUME_ROUTING_KWARGS),
        )
        return set()

    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return set(_RESUME_ROUTING_KWARGS)

    return {name for name in _RESUME_ROUTING_KWARGS if name in params}


def _matrix_provenance(resolver: Any) -> dict[str, Any] | None:
    """Read matrix identity off a ``model_role_resolver`` capability.

    WHY THIS EXISTS. ``delegate:agent_spawned`` records the
    ``provider_preferences`` a delegation resolved to, but not WHICH
    routing-matrix file produced them. A user file in ``~/.amplifier/routing/``
    silently outranks the bundle's own same-named matrix, so a surprising
    resolution in the event stream is indistinguishable from a shadowed
    matrix, a shipped-matrix change, or no matrix at all. Two prior
    investigations read the shipped file, reasoned about a matrix that was
    not in effect, and reached confidently wrong mechanisms.

    CONSUMED, NOT RE-DERIVED. ``matrix_path`` / ``matrix_source`` /
    ``shadowed_paths`` are published by the routing bundle on the capability
    object this tool already holds (see hooks-routing's ``resolver_class``
    docstring, which names "a spawn-time telemetry payload" as the intended
    consumer). Nothing here re-implements matrix precedence; a second
    implementation of that precedence is exactly the drift this reads
    published state to avoid.

    OPTIONAL BY CONTRACT. Every attribute is optional: the capability is
    duck-typed and an alternate strategy (cost-aware, latency-aware) may
    register under the same key without any notion of a "matrix file", as
    may an older routing bundle predating these attributes. Absent is NOT
    "no shadowing" -- it is "this strategy does not report a source", so
    this returns ``None`` rather than a dict of nulls, and the caller omits
    the key entirely. Values are type-guarded rather than trusted.

    Returns:
        A dict with ``matrix_name`` / ``matrix_path`` / ``matrix_source`` /
        ``shadowed_paths``, or ``None`` when the resolver reports no source
        at all (absent attributes, all-``None`` values, or a resolver that
        is itself ``None``).
    """
    if resolver is None:
        return None

    def _str_or_none(value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    name = _str_or_none(getattr(resolver, "name", None))
    path = _str_or_none(getattr(resolver, "matrix_path", None))
    source = _str_or_none(getattr(resolver, "matrix_source", None))

    raw_shadowed = getattr(resolver, "shadowed_paths", None)
    shadowed: list[str] = []
    # str is itself a sequence -- iterating one yields characters, which
    # would silently produce a list of single letters instead of failing.
    if isinstance(raw_shadowed, (list, tuple)):
        shadowed = [p for p in (_str_or_none(p) for p in raw_shadowed) if p]

    # A resolver that reports no file identity at all contributes nothing a
    # forensic reader can act on. Emitting {"matrix_path": None, ...} would
    # look like a positive statement ("we checked, there is no shadowing");
    # returning None keeps the key off the payload entirely, which reads
    # correctly as "unknown".
    if path is None and source is None and not shadowed:
        return None

    return {
        "matrix_name": name,
        "matrix_path": path,
        "matrix_source": source,
        "shadowed_paths": shadowed,
    }


# Matches a fenced ```json ... ``` block, tolerant of ```JSON, surrounding
# indentation, and trailing whitespace on the fence lines. The closing fence
# must be alone on its own line so short "```" substrings inside the JSON
# body's string values don't terminate the match early.
_JSON_FENCE_PATTERN = re.compile(
    r"^[ \t]*```[ \t]*json[ \t]*\r?\n(?P<body>.*?)\r?\n[ \t]*```[ \t]*$",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)


def _check_call_budget_type(value: int) -> None:
    """Raise if ``value`` is not a valid LLM-call budget integer.

    Rejects ``bool`` (a ``bool`` is an ``int`` subclass in Python;
    ``True``/``False`` must never silently become ``1``/``0`` here), any
    other non-``int``, and negative values. Mirrors the validation
    discipline established for ``settings.timeout`` in the #298 branch
    (``_validate_timeout``): fail loud at the point the value is supplied,
    never at spawn time.

    Deliberately does NOT collapse ``0`` -- callers that must distinguish
    "explicitly zero" from "not supplied" (e.g. the per-call precedence
    rank in ``_resolve_call_budget``) need the raw value. Callers for whom
    the two are equivalent should use ``_validate_call_budget`` instead.
    """
    if isinstance(value, bool):
        raise TypeError(f"max_llm_calls must be an integer, not a bool: {value!r}")
    if not isinstance(value, int):
        raise TypeError(
            "max_llm_calls must be an integer or None, got "
            f"{type(value).__name__}: {value!r}"
        )
    if value < 0:
        raise ValueError(f"max_llm_calls must be >= 0, got {value}")


def _validate_call_budget(value: Any) -> int | None:
    """Validate + collapse a Layer 1 LLM-call budget value.

    ``None`` and ``0`` both mean "no Layer 1 budget" -- collapsed to
    ``None`` so this caller only needs one falsy check. Use this for
    sources where "unset" and "explicitly zero" are equivalent (this
    module's own ``settings.max_llm_calls`` default). For the per-call
    override, where an explicit ``0`` must override a non-zero default
    rather than being indistinguishable from "not supplied", use
    ``_check_call_budget_type`` directly and let
    ``DelegateTool._resolve_call_budget`` do the collapse at the point it
    knows the value was explicitly given.
    """
    if value is None:
        return None
    _check_call_budget_type(value)
    return value or None  # 0 -> None (explicit opt-out)


class _DelegateTimeoutExpired(Exception):
    """Internal signal that the delegate-owned timeout expired."""


def _validate_timeout(timeout: object) -> int | float | None:
    """Return a timeout that asyncio's event loop can represent.

    ``asyncio.wait`` takes a float timeout. Validate that conversion at
    configuration time, before spawning a child coroutine, so an oversized
    integer cannot fail later after work has begun.
    """
    if timeout is None:
        return None
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise TypeError(
            "settings.timeout must be null or a positive finite, non-boolean "
            "number of seconds"
        )

    try:
        event_loop_timeout = float(timeout)
    except OverflowError as error:
        raise ValueError(
            "settings.timeout must be representable as a finite event-loop timeout"
        ) from error

    if timeout <= 0 or not math.isfinite(event_loop_timeout):
        raise ValueError(
            "settings.timeout must be null or a positive finite, non-boolean "
            "number of seconds"
        )
    return timeout


async def mount(coordinator: ModuleCoordinator, config: dict[str, Any] | None = None):
    """Mount the agent delegation tool.

    Args:
        coordinator: The module coordinator
        config: Optional configuration with features and settings

    Returns:
        None - No cleanup needed for this module
    """
    config = config or {}

    # Register observable lifecycle events via contribution channel.
    # Uses register_contributor (not register_capability) so events are
    # discoverable via collect_contributions("observability.events").
    # See: core:docs/specs/CONTRIBUTION_CHANNELS.md
    coordinator.register_contributor(
        "observability.events",
        "tool-delegate",
        lambda: [
            "delegate:agent_spawned",
            "delegate:agent_resumed",
            "delegate:agent_completed",
            "delegate:agent_cancelled",
            "delegate:error",
            "delegate:model_role_unresolved",
        ],
    )

    tool = DelegateTool(coordinator, config)
    await coordinator.mount("tools", tool, name=tool.name)
    logger.info("Mounted DelegateTool with observable events")
    return  # No cleanup needed


class DelegateTool:
    """Delegate tasks to specialized agents via sub-sessions.

    This tool provides fine-grained control over context inheritance
    and supports session resumption using full session IDs.

    Key improvements over task tool:
    - Two-parameter context: depth (how much) and scope (which content)
    - Fixed tool inheritance: agent declarations always honored
    - Session resume requires full session_id (returned by each delegate call)
    """

    name = "delegate"

    def __init__(self, coordinator: ModuleCoordinator, config: dict[str, Any]):
        """Initialize the delegate tool.

        Args:
            coordinator: Module coordinator for accessing capabilities
            config: Configuration with features and settings sections
        """
        self.coordinator = coordinator
        self.config = config

        # Parse structured config
        features = config.get("features", {})
        settings = config.get("settings", {})

        # Feature flags
        self_delegation_config = features.get("self_delegation", {})
        self.self_delegation_enabled = self_delegation_config.get("enabled", True)
        self.max_self_delegation_depth = self_delegation_config.get("max_depth", 3)

        self.session_resume_enabled = features.get("session_resume", {}).get(
            "enabled", True
        )
        self.context_inheritance_enabled = features.get("context_inheritance", {}).get(
            "enabled", True
        )
        self.max_context_turns = features.get("context_inheritance", {}).get(
            "max_turns", 10
        )
        self.provider_selection_enabled = features.get("provider_selection", {}).get(
            "enabled", True
        )

        # Structured delegation return contract (flag-gated; default OFF).
        # See RETURN_CONTRACT_INSTRUCTION / RETURN_CONTRACT_SCHEMA above and
        # _parse_return_contract() below for the full mechanism. `return_contract_reask`
        # is parsed now so the config surface is stable across stages, but it is
        # inert in Stage 1 -- the re-ask loop is a Stage 2 deferral (see spec §4.4).
        return_contract_config = features.get("return_contract", {})
        self.return_contract_enabled = return_contract_config.get("enabled", False)
        self.return_contract_strip_block = return_contract_config.get(
            "strip_block", True
        )
        self.return_contract_reask = return_contract_config.get(
            "reask_on_nonconformance", False
        )

        # Settings
        self.exclude_tools: list[str] = settings.get("exclude_tools", ["tool-delegate"])
        self.exclude_hooks: list[str] = settings.get("exclude_hooks", [])
        self.timeout = _validate_timeout(settings.get("timeout", 14400))
        self._detached_child_tasks: set[asyncio.Task[Any]] = set()
        # When True, model_role resolving to no candidates raises
        # ModelRoleUnresolvedError instead of silently falling back to the
        # session default model. Default False preserves existing behavior
        # for every caller that currently relies on the quiet fallback; the
        # "delegate:model_role_unresolved" event is emitted either way.
        self.strict_model_role: bool = settings.get("strict_model_role", False)

        # Layer 1 (per-leg LLM-call budget, spec: 298-replacement) settings.
        # Ships DARK at S0: default None means "inject no budget at all" --
        # today's behavior (whatever max_iterations the parent's own
        # orchestrator config already carries, typically unlimited -- see
        # _spawn_new_session's orchestrator_config build) is completely
        # unchanged until settings.max_llm_calls is explicitly set to a
        # positive integer. See _resolve_call_budget for the precedence
        # chain, and the module README's "Known gaps" section for why a
        # per-agent frontmatter override (spec §6.1) is NOT implemented.
        self.max_llm_calls: int | None = _validate_call_budget(
            settings.get("max_llm_calls")
        )
        self.budget_warn_ratio: float = float(settings.get("budget_warn_ratio", 0.8))

        # Build feature registry for dynamic description composition
        self._feature_registry = self._build_feature_registry()

        # Maps sub_session_id -> the raw (un-sanitized) agent_name recorded at
        # spawn time. This is the authoritative source for agent identity on
        # resume: it is the exact same string emitted in delegate:agent_spawned,
        # so counters that pair spawned/resumed/completed events by "agent"
        # stay correct. Populated in _spawn_new_session(); consulted in
        # _resume_existing_session() via _resolve_agent_for_session().
        #
        # Scope: this cache lives on the DelegateTool instance, which is
        # constructed once per mounted session (see mount()) and persists for
        # the lifetime of the parent session/coordinator. It does NOT survive
        # across process restarts or a resume issued from a *different*
        # parent session than the one that spawned the sub-session -- that
        # cold-cache case falls back to parsing the agent name out of the
        # session_id suffix (see _resolve_agent_for_session), which is lossy
        # (sanitized: lowercased, non-alphanumeric chars collapsed to hyphens)
        # but always available since
        # amplifier_foundation.tracing.generate_sub_session_id guarantees the
        # "{parent_span}-{child_span}_{sanitized_agent_name}" shape for every
        # sub-session id this tool creates.
        self._session_agents: dict[str, str] = {}

        # Maps sub_session_id -> the routing this tool resolved at spawn time:
        # {"model_role": str | None, "provider_preferences": list | None}.
        #
        # Why this exists: a caller pins routing ONCE, on the spawn call
        # (delegate(agent=..., model_role="reasoning")), and then resumes
        # with (session_id, instruction) -- the shape every existing caller
        # uses. Without this record the resume leg has nothing to thread and
        # the child silently falls back to settings priority, which is the
        # measured "resume wipes the role" defect. An explicit model_role /
        # provider_preferences on the resume call still wins over it.
        #
        # Same scope and same cold-cache caveat as _session_agents above:
        # per-DelegateTool-instance, so it does not survive a process
        # restart or a resume issued from a different parent session. That
        # case is not a silent drop either -- the app layer recovers the
        # preferences from the persisted session (agent overlay, then mount
        # plan); see amplifier-app-cli's resume_sub_session.
        self._session_routing: dict[str, dict[str, Any]] = {}

    def _build_feature_registry(self) -> list[dict[str, Any]]:
        """Build registry of features with their descriptions.

        Each feature has:
        - name: Feature identifier
        - enabled: Whether the feature is enabled
        - description: Text to include in tool description when enabled
        - disabled_note: Optional text when feature is disabled

        Returns:
            List of feature definitions
        """
        return [
            {
                "name": "self_delegation",
                "enabled": self.self_delegation_enabled,
                "description": '- agent="self": Spawn yourself as a sub-agent (maximum token conservation)',
                "disabled_note": None,
            },
            {
                "name": "session_resume",
                "enabled": self.session_resume_enabled,
                "description": "- Use session_id to resume an existing agent session (must be full session_id from previous delegate call)",
                "disabled_note": "- Session resumption is disabled",
            },
            {
                "name": "context_inheritance",
                "enabled": self.context_inheritance_enabled,
                "description": """Context control (two independent parameters):
- context_depth: HOW MUCH context - "none" (clean slate), "recent" (last N turns), "all" (full history)
- context_scope: WHICH content - "conversation" (text only), "agents" (+ agent results), "full" (+ all tools)""",
                "disabled_note": "- Context inheritance is disabled (agents always start fresh)",
            },
            {
                "name": "provider_selection",
                "enabled": self.provider_selection_enabled,
                "description": "- Use provider_preferences to specify model/provider for the agent",
                "disabled_note": None,
            },
            {
                "name": "return_contract",
                "enabled": self.return_contract_enabled,
                "description": (
                    "When a delegate result carries contract.findings, walk every "
                    "finding before you write your answer, and carry each surviving "
                    "claim into your response text with its evidence. A finding you "
                    "do not mention is a finding you have decided to discard -- "
                    "decide that deliberately, not by running out of attention.\n"
                    "contract.not_covered is your resume decision list. Read it the "
                    "moment the result arrives: resuming that session now is cheap; "
                    "discovering the gap three turns later is not.\n"
                    "contract.conformant: false means the agent returned "
                    "unstructured prose. Its coverage is unknown -- do not treat "
                    "its silence as completeness."
                ),
                "disabled_note": None,
            },
        ]

    async def _await_child_with_deadline(
        self, child_coro: Coroutine[Any, Any, Any]
    ) -> Any:
        """Await a child while releasing the parent at the configured deadline.

        Unlike ``asyncio.timeout`` and ``asyncio.wait_for``, this does not wait
        for a child that catches ``CancelledError`` or performs slow cancellation
        cleanup. The child is cancelled, detached, and its terminal result is
        consumed by a callback. A cancellation of this parent task follows the
        same cleanup path but is re-raised unchanged.
        """
        if self.timeout is None:
            return await child_coro

        child_task = asyncio.create_task(child_coro)
        try:
            done, _ = await asyncio.wait(
                (child_task,),
                timeout=float(self.timeout),
                return_when=asyncio.ALL_COMPLETED,
            )
        except asyncio.CancelledError:
            self._cancel_and_detach_child(child_task)
            raise

        if child_task in done:
            return child_task.result()

        self._cancel_and_detach_child(child_task)
        raise _DelegateTimeoutExpired

    def _cancel_and_detach_child(self, child_task: asyncio.Task[Any]) -> None:
        """Cancel a child while retaining it strongly until terminal cleanup."""
        if not child_task.done():
            child_task.cancel()
        if child_task.done():
            self._consume_detached_child_result(child_task)
            return

        self._detached_child_tasks.add(child_task)
        child_task.add_done_callback(self._consume_detached_child_result)

    def _consume_detached_child_result(self, child_task: asyncio.Task[Any]) -> None:
        """Consume a detached child result and release its strong reference."""
        try:
            child_task.result()
        except asyncio.CancelledError:
            pass
        except BaseException:
            logger.debug(
                "Detached delegate child finished with an exception after cancellation",
                exc_info=True,
            )
        finally:
            self._detached_child_tasks.discard(child_task)

    def _compose_feature_descriptions(self) -> str:
        """Compose feature descriptions based on enabled state.

        Returns:
            Composed feature description text
        """
        lines = []
        for feature in self._feature_registry:
            if feature["enabled"]:
                lines.append(feature["description"])
            elif feature.get("disabled_note"):
                lines.append(feature["disabled_note"])
        return "\n".join(lines)

    @property
    def description(self) -> str:
        """Generate dynamic description with available agents and enabled features.

        Composes description based on:
        1. Enabled features from config
        2. Available agents from registry
        """
        agents_list = self._get_agent_list()
        feature_desc = self._compose_feature_descriptions()

        base_description = """Spawn a specialized agent to handle tasks autonomously.

Why delegate: Every tool call YOU make consumes YOUR context window permanently.
Agents absorb that cost and return only summaries (~500 tokens vs ~20,000 tokens).
Delegation = longer, more effective sessions.

Special agent values:
- agent="namespace:path/to/bundle": Delegate to any bundle directly as an agent"""

        # Add self-delegation if enabled
        if self.self_delegation_enabled:
            base_description += '\n- agent="self": Spawn yourself as a sub-agent (maximum token conservation)'

        # Add feature-based sections
        base_description += f"\n\n{feature_desc}"

        # Add usage notes
        base_description += """

Agent usage notes:
- Launch multiple agents concurrently when tasks are independent
- When an agent completes, it returns a single message back to you
- Each agent invocation is stateless - provide complete context in your instruction"""

        if agents_list:
            agent_desc = "\n".join(
                f"  - {a['name']}: {a.get('description', 'No description')}"
                for a in agents_list
            )
            return f"{base_description}\n\nAvailable agents:\n{agent_desc}"

        return f'{base_description}\n\nNo agents currently registered. Use agent="self" or a bundle path.'

    @property
    def input_schema(self) -> dict:
        """Input schema for agent delegation.

        Supports both spawn (agent + instruction) and resume (session_id + instruction).

        Returns:
            JSON schema for the tool input with structured parameters

        Implementation note:
            The schema body is kept as a pure literal in ``_static_input_schema()``
            so foundation's static token-cost estimator
            (``amplifier_foundation.bundle_docs.tool_schema._extract_input_schema``)
            can extract it via ``ast.literal_eval``. The dynamic note about
            whether self-delegation is enabled in this session is appended
            here, post-construction, in-place on the agent description.

            (Take care not to write the substring r-e-t-u-r-n followed by
            an open brace anywhere in this file's docstrings — that text
            pattern is what the static estimator's regex looks for, and
            non-literal text after such a match will trip ast.literal_eval.)
        """
        schema = self._static_input_schema()
        # Reflect the runtime self-delegation state in the parameter description
        # so the model has a clear signal about whether `agent="self"` will be
        # accepted in this session — the bare example list alone has been
        # observed to encourage models to attempt `"self"` even when
        # `execute()` will hard-reject it.
        if self.self_delegation_enabled:
            note = " Self-delegation is enabled in this session."
        else:
            note = (
                " Self-delegation is DISABLED in this session —"
                " `agent='self'` will be rejected by execute()."
                " Use a registered agent name or bundle path instead."
            )
        schema["properties"]["agent"]["description"] += note

        # Shape `model_role` to what the session can actually honour. Left as
        # an open string, models have been observed to invent values -- notably
        # the literal "default" -- which resolve to no candidates, log a
        # warning, and let the spawn proceed on the agent's default model. The
        # role names belong in the parameter the model is filling, not only in
        # prose it may not weight.
        #
        # Three distinct states, and collapsing any two of them is a bug:
        #   None -> no resolver registered at all. execute() can do nothing but
        #           warn, so the parameter is inert: drop it.
        #   ()   -> a resolver is registered but cannot enumerate its roles
        #           (older routing bundle, or a strategy that has no fixed role
        #           set). Routing works; we just cannot constrain it. Keep the
        #           parameter as an open string -- dropping it here would break
        #           working routing.
        #   (..) -> constrain to those roles.
        known_roles = self._resolver_known_roles()
        if known_roles is None:
            schema["properties"].pop("model_role", None)
        elif known_roles:
            schema["properties"]["model_role"]["enum"] = list(known_roles)
        return schema

    def _resolver_known_roles(self) -> tuple[str, ...] | None:
        """Roles the active ``model_role_resolver`` can enumerate.

        Returns ``None`` when no resolver capability is registered, and an
        empty tuple when a resolver is registered but does not expose the
        optional ``known_roles`` member. Those two states are different and
        callers must treat them differently -- see ``input_schema``.

        The capability is the contract, and it is the same source ``execute()``
        resolves against. Note that ``known_roles`` is advisory: a listed role
        can still resolve to no candidates when no installed provider serves
        it, which is why the miss-path warning in ``execute()`` stays.
        """
        if not hasattr(self.coordinator, "get_capability"):
            return None
        resolver = self.coordinator.get_capability("model_role_resolver")
        if resolver is None:
            return None
        # Optional member of a duck-typed contract, so it may be absent, and a
        # third-party resolver may return something unusable. A bad value must
        # degrade to "cannot enumerate" -- never leak into the schema, and
        # never raise, since this runs on every request.
        roles = getattr(resolver, "known_roles", None)
        if not isinstance(roles, (list, tuple)):
            return ()
        if not all(isinstance(role, str) for role in roles):
            return ()
        return tuple(roles)

    def _static_input_schema(self) -> dict:
        """Return a fresh literal copy of the input schema dict.

        The dict body below is intentionally kept as a pure literal — no
        computed expressions, no string concatenation, no name references —
        so foundation's bundle-docs token-cost estimator can statically
        evaluate it via ``ast.literal_eval``. The dynamic self-delegation
        status note is applied by ``input_schema`` after this returns.
        """
        return {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent to delegate to (e.g., 'foundation:explorer', 'self', or bundle path).",
                },
                "instruction": {
                    "type": "string",
                    "description": "Clear instruction for the agent",
                },
                "session_id": {
                    "type": "string",
                    "description": "Resume existing agent session (use full session_id from previous delegate call)",
                },
                "context_depth": {
                    "type": "string",
                    "enum": ["none", "recent", "all"],
                    "description": "HOW MUCH context: 'none' (clean slate), 'recent' (last N turns), 'all' (full history)",
                },
                "context_turns": {
                    "type": "integer",
                    "description": "Number of turns when context_depth is 'recent' (default: 5)",
                },
                "context_scope": {
                    "type": "string",
                    "enum": ["conversation", "agents", "full"],
                    "description": "WHICH content: 'conversation' (user/assistant text), 'agents' (+ delegate results), 'full' (+ all tool results)",
                },
                "provider_preferences": {
                    "type": "array",
                    "description": "Ordered list of provider/model preferences with glob pattern support",
                    "items": {
                        "type": "object",
                        "properties": {
                            "provider": {
                                "type": "string",
                                "description": "Provider name (e.g., 'anthropic', 'openai')",
                            },
                            "model": {
                                "type": "string",
                                "description": "Model name or glob pattern (e.g., 'claude-haiku-*')",
                            },
                        },
                        "required": ["provider", "model"],
                    },
                },
                "model_role": {
                    "type": "string",
                    "description": (
                        "Override the agent's default model role for this delegation. "
                        "Use when the task requires a different capability than the agent's default "
                        "(e.g., 'vision' for image-related work, 'reasoning' for architecture). "
                        "Available roles are shown in the session context."
                    ),
                },
                "max_llm_calls": {
                    "type": "integer",
                    "description": (
                        "Override the LLM-call budget for this delegation (per session "
                        "leg). Raise for known-large tasks; 0 disables the budget for "
                        "this call (a wall-clock backstop still applies). Only takes "
                        "effect when this session's settings.max_llm_calls is "
                        "configured -- most deployments do not set one yet."
                    ),
                },
            },
            "required": ["instruction"],
        }

    def _get_agent_list(self) -> list[dict[str, Any]]:
        """Get list of available agents from mount plan.

        Returns:
            List of agent definitions with name and description
        """
        agents = self.coordinator.config.get("agents", {})
        sorted_agents = sorted(agents.items(), key=lambda item: item[0])
        return [
            {"name": name, "description": cfg.get("description", "No description")}
            for name, cfg in sorted_agents
        ]

    async def _get_parent_messages(self) -> list[dict[str, Any]] | None:
        """Get all messages from parent session.

        Returns:
            List of messages or None if not available
        """
        parent_context = self.coordinator.get("context")
        if not parent_context or not hasattr(parent_context, "get_messages"):
            logger.debug("No parent context available for inheritance")
            return None

        try:
            messages = await parent_context.get_messages()
            return messages if messages else None
        except Exception as e:
            logger.warning(f"Failed to get parent messages: {e}")
            return None

    def _extract_recent_turns(
        self, messages: list[dict[str, Any]], n_turns: int
    ) -> list[dict[str, Any]]:
        """Extract the last N user->assistant turns from messages.

        A "turn" starts with a user message and includes all subsequent messages
        until the next user message.

        Args:
            messages: Full message history
            n_turns: Number of recent turns to extract

        Returns:
            Messages from the last N turns
        """
        if not messages or n_turns <= 0:
            return []

        # Find indices where user messages start (turn boundaries)
        turn_starts = [i for i, m in enumerate(messages) if m.get("role") == "user"]

        if not turn_starts:
            return messages  # No user messages, return all

        if len(turn_starts) <= n_turns:
            return messages  # Fewer turns than requested, return all

        # Get messages from the nth-to-last turn onwards
        start_index = turn_starts[-n_turns]
        return messages[start_index:]

    def _sanitize_content(self, content: Any) -> str:
        """Extract text content from message content field.

        Handles both string and list formats (Anthropic/Amplifier).

        Args:
            content: Message content (string or list of content blocks)

        Returns:
            Extracted text content as string
        """
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            text_parts = []
            # Types to explicitly filter out
            filtered_types = {
                "tool_use",
                "tool_call",
                "tool_result",
                "thinking",
                "redacted_thinking",
            }

            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type", "")
                    if block_type == "text":
                        text = block.get("text", "")
                        if text:
                            text_parts.append(text)
                    elif block_type not in filtered_types:
                        logger.debug(f"Unknown content block type '{block_type}'")
                elif isinstance(block, str):
                    text_parts.append(block)

            if text_parts:
                return "\n".join(text_parts)

        return ""

    def _sanitize_conversation_only(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Sanitize messages to include only user/assistant conversation text.

        Strips ALL tool content - only human-readable conversation remains.

        Args:
            messages: Raw messages from parent

        Returns:
            Sanitized messages with only conversation content
        """
        sanitized = []
        for msg in messages:
            role = msg.get("role")

            # Skip tool messages entirely
            if role == "tool":
                continue
            if msg.get("tool_call_id"):
                continue

            # Only user and assistant
            if role in ("user", "assistant"):
                # Skip assistant messages that only contain tool calls
                if (
                    role == "assistant"
                    and msg.get("tool_calls")
                    and not msg.get("content")
                ):
                    continue

                content = msg.get("content", "")
                sanitized_content = self._sanitize_content(content)

                if sanitized_content:
                    sanitized.append({"role": role, "content": sanitized_content})

        return sanitized

    def _sanitize_with_agent_results(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Sanitize messages to include conversation plus delegate/task tool results.

        Includes results from agent delegation tools but not other tools.

        Args:
            messages: Raw messages from parent

        Returns:
            Sanitized messages with conversation and agent results
        """
        sanitized = []
        agent_tools = {"delegate", "task"}

        for msg in messages:
            role = msg.get("role")

            # Include tool results only from agent tools
            if role == "tool":
                tool_name = msg.get("name", "")
                if tool_name in agent_tools:
                    content = msg.get("content", "")
                    if content:
                        # Format as assistant message with agent context
                        sanitized.append(
                            {
                                "role": "assistant",
                                "content": f"[Agent Result from {tool_name}]: {content}",
                            }
                        )
                continue

            if msg.get("tool_call_id"):
                # Check if this is a result from an agent tool
                # Tool call ID doesn't tell us the tool name, so skip
                continue

            # User and assistant messages
            if role in ("user", "assistant"):
                if (
                    role == "assistant"
                    and msg.get("tool_calls")
                    and not msg.get("content")
                ):
                    continue

                content = msg.get("content", "")
                sanitized_content = self._sanitize_content(content)

                if sanitized_content:
                    sanitized.append({"role": role, "content": sanitized_content})

        return sanitized

    def _sanitize_all_content(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Sanitize messages to include all content including tool results.

        Preserves tool results but strips internal metadata.

        Args:
            messages: Raw messages from parent

        Returns:
            Sanitized messages with all content
        """
        sanitized = []

        for msg in messages:
            role = msg.get("role")

            # Include all tool results
            if role == "tool":
                tool_name = msg.get("name", "unknown")
                content = msg.get("content", "")
                if content:
                    # Truncate very long tool results
                    if len(content) > 4000:
                        content = content[:4000] + "... [truncated]"
                    sanitized.append(
                        {
                            "role": "assistant",
                            "content": f"[Tool Result from {tool_name}]: {content}",
                        }
                    )
                continue

            if msg.get("tool_call_id"):
                continue

            # User and assistant messages
            if role in ("user", "assistant"):
                if (
                    role == "assistant"
                    and msg.get("tool_calls")
                    and not msg.get("content")
                ):
                    continue

                content = msg.get("content", "")
                sanitized_content = self._sanitize_content(content)

                if sanitized_content:
                    sanitized.append({"role": role, "content": sanitized_content})

        return sanitized

    async def _build_inherited_context(
        self, depth: str, turns: int, scope: str
    ) -> list[dict[str, Any]] | None:
        """Build context based on depth (how much) and scope (which content).

        Args:
            depth: "none", "recent", or "all"
            turns: Number of turns for "recent" mode
            scope: "conversation", "agents", or "full"

        Returns:
            List of sanitized messages or None
        """
        if depth == "none":
            return None

        messages = await self._get_parent_messages()
        if not messages:
            return None

        # Step 1: Filter by DEPTH
        if depth == "recent":
            messages = self._extract_recent_turns(messages, turns)
        # else: "all" - keep all messages

        # Step 2: Filter by SCOPE
        if scope == "conversation":
            return self._sanitize_conversation_only(messages)
        elif scope == "agents":
            return self._sanitize_with_agent_results(messages)
        else:  # "full"
            return self._sanitize_all_content(messages)

    def _format_parent_context_for_instruction(
        self, messages: list[dict[str, Any]]
    ) -> str:
        """Format parent messages as text to prepend to the instruction.

        Args:
            messages: List of sanitized messages from parent session

        Returns:
            Formatted text block with parent conversation context
        """
        if not messages:
            return ""

        lines = ["[PARENT CONVERSATION CONTEXT]"]
        lines.append(
            "The following is recent conversation history from the parent session:"
        )
        lines.append("")

        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            role_label = role.upper()
            if role == "user":
                role_label = "USER"
            elif role == "assistant":
                role_label = "ASSISTANT"

            # Truncate very long messages
            max_content_len = 2000
            if len(content) > max_content_len:
                content = content[:max_content_len] + "... [truncated]"

            lines.append(f"{role_label}: {content}")
            lines.append("")

        lines.append("[END PARENT CONTEXT]")
        return "\n".join(lines)

    def _merge_tools(
        self,
        parent_tools: list[dict[str, Any]],
        agent_tools: list[dict[str, Any]],
        exclude: list[str],
    ) -> list[dict[str, Any]]:
        """Merge tools with correct inheritance semantics.

        Exclusions apply to INHERITANCE only.
        Explicit declarations from agent are ALWAYS honored.

        Args:
            parent_tools: Tools from parent session
            agent_tools: Tools explicitly declared by agent config
            exclude: Tools to exclude from inheritance

        Returns:
            Merged tool list
        """
        # Start with inherited, apply exclusions
        inherited = [t for t in parent_tools if t.get("module") not in exclude]

        # Agent's explicit declarations ALWAYS added (even if excluded from inheritance)
        inherited_modules = {t.get("module") for t in inherited}
        for tool in agent_tools:
            if tool.get("module") not in inherited_modules:
                inherited.append(tool)

        return inherited

    def _parse_return_contract(self, response: str) -> tuple[dict[str, Any], str]:
        """Parse an optional structured return contract from a sub-agent's response.

        Looks for the LAST fenced ```json block in *response* (see
        RETURN_CONTRACT_SCHEMA) and tolerantly normalizes it -- a partially-good
        return is kept, never discarded outright. This is the single place that
        decides whether a delegation "conformed" to the contract; both the spawn
        and resume completion paths call it once and reuse the result for both
        telemetry and the annotated ``ToolResult.output``.

        Args:
            response: The sub-agent's final text -- normal prose, optionally
                followed by a fenced json block.

        Returns:
            A ``(contract, cleaned_response)`` tuple.

            ``contract`` always has the shape::

                {
                    "conformant": bool | None,  # None only when the feature is disabled
                    "reason": str | None,       # populated when conformant is False
                    "summary": str | None,
                    "findings": list[dict],
                    "not_covered": list[str],
                    "artifacts": list[dict],
                }

            ``cleaned_response`` is *response* with the parsed block removed
            when parsing succeeded and ``return_contract_strip_block`` is
            enabled; otherwise it is byte-identical to *response*. This is
            the one non-purely-additive behavior in the contract (see the
            module docstring / README) and only ever fires when both the
            feature and strip_block are enabled and the block parsed cleanly.

        Invariants:
            - NEVER raises. Any unexpected failure degrades to
              ``conformant=False`` with ``reason=repr(exception)``.
            - NEVER mutates *response* when ``conformant`` is not ``True``.
            - Pure function of (response, self.return_contract_enabled,
              self.return_contract_strip_block). No I/O, no coordinator access.
        """
        empty_contract: dict[str, Any] = {
            "conformant": None,
            "reason": None,
            "summary": None,
            "findings": [],
            "not_covered": [],
            "artifacts": [],
        }

        if not self.return_contract_enabled:
            return dict(empty_contract), response

        try:
            match = None
            for match in _JSON_FENCE_PATTERN.finditer(response):
                pass  # keep iterating -- the LAST fenced json block wins

            if match is None:
                contract = dict(empty_contract)
                contract["conformant"] = False
                contract["reason"] = "no fenced json block found in agent response"
                return contract, response

            try:
                parsed = json.loads(match.group("body"))
            except (ValueError, TypeError) as e:
                contract = dict(empty_contract)
                contract["conformant"] = False
                contract["reason"] = f"json parse failed: {e}"
                return contract, response

            if not isinstance(parsed, dict):
                contract = dict(empty_contract)
                contract["conformant"] = False
                contract["reason"] = "contract block is not a JSON object"
                return contract, response

            raw_findings = parsed.get("findings")
            if not isinstance(raw_findings, list):
                contract = dict(empty_contract)
                contract["conformant"] = False
                contract["reason"] = "missing required 'findings' array"
                return contract, response

            # Normalize, never reject -- a partially-good return is kept.
            findings: list[dict[str, Any]] = []
            for item in raw_findings:
                if not isinstance(item, dict):
                    continue
                claim = item.get("claim")
                if not isinstance(claim, str) or not claim.strip():
                    continue
                evidence = item.get("evidence")
                if not isinstance(evidence, str):
                    evidence = ""
                confidence = item.get("confidence")
                if confidence not in ("high", "medium", "low"):
                    confidence = "unspecified"
                findings.append(
                    {"claim": claim, "evidence": evidence, "confidence": confidence}
                )

            raw_not_covered = parsed.get("not_covered")
            not_covered = (
                [item for item in raw_not_covered if isinstance(item, str)]
                if isinstance(raw_not_covered, list)
                else []
            )

            raw_artifacts = parsed.get("artifacts")
            artifacts: list[dict[str, Any]] = []
            if isinstance(raw_artifacts, list):
                for item in raw_artifacts:
                    if not isinstance(item, dict):
                        continue
                    path = item.get("path")
                    if not isinstance(path, str) or not path:
                        continue
                    description = item.get("description")
                    if not isinstance(description, str):
                        description = ""
                    artifacts.append({"path": path, "description": description})

            summary = parsed.get("summary")
            if not isinstance(summary, str):
                summary = None

            contract = {
                "conformant": True,
                "reason": None,
                "summary": summary,
                "findings": findings,
                "not_covered": not_covered,
                "artifacts": artifacts,
            }

            if self.return_contract_strip_block:
                cleaned = response[: match.start()] + response[match.end() :]
                # Collapse the blank lines left behind by removing the block.
                cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
            else:
                cleaned = response

            return contract, cleaned

        except Exception as e:  # defensive -- this method must never raise
            contract = dict(empty_contract)
            contract["conformant"] = False
            contract["reason"] = repr(e)
            return contract, response

    async def execute(self, input: dict) -> ToolResult:
        """Execute agent delegation with structured parameters.

        Routes to spawn (new agent session) or resume (existing agent session)
        based on input parameters.

        Args:
            input: Dict with 'instruction' (required) and either:
                   - 'agent' (for spawn) or
                   - 'session_id' (for resume)

        Returns:
            ToolResult with success status and output or error
        """
        # Extract parameters
        agent_name = input.get("agent", "").strip()
        instruction = input.get("instruction", "").strip()
        session_id = input.get("session_id", "").strip()

        # Framework context — read from coordinator dispatch context.
        #
        # Updated orchestrators (post loop-streaming fix) store context in a
        # task-keyed dict at coordinator._tool_dispatch_contexts so concurrent
        # delegates running via asyncio.gather don't race on a shared attribute.
        # Older orchestrators use the single-attribute coordinator._tool_dispatch_context.
        # We check the new task-keyed form first and fall back to the legacy
        # attribute so both patterns are supported.
        _dispatch_task = asyncio.current_task()
        dispatch_ctx = (
            getattr(self.coordinator, "_tool_dispatch_contexts", {}).get(_dispatch_task)
            or getattr(self.coordinator, "_tool_dispatch_context", {})
            or {}
        )
        tool_call_id = dispatch_ctx.get("tool_call_id", "")
        parallel_group_id = dispatch_ctx.get("parallel_group_id", None)

        # Context parameters (two-parameter system)
        context_depth = input.get("context_depth", "recent")
        context_turns = input.get("context_turns", 5)
        context_scope = input.get("context_scope", "conversation")

        # Validate context_turns against max
        if context_turns > self.max_context_turns:
            context_turns = self.max_context_turns

        # Provider preferences
        raw_provider_prefs = input.get("provider_preferences", [])
        provider_preferences = None
        if raw_provider_prefs and self.provider_selection_enabled:
            provider_preferences = [
                ProviderPreference.from_dict(p) for p in raw_provider_prefs
            ]

        # Model role resolution via the model_role_resolver capability (caller override)
        # provider_preferences wins when both are provided (explicit pin overrides resolver).
        #
        # The capability is generic — the routing-matrix bundle ships the
        # default matrix-based implementation, but other strategies (cost-aware,
        # latency-aware, availability-aware) may register an alternate resolver
        # under the same key. We duck-type against the contract:
        #     async def resolve(model_role) -> list[ProviderPreference]
        raw_model_role = input.get("model_role", "").strip()
        # Matrix provenance for the spawn telemetry record. Captured HERE,
        # at the one site that actually consults the resolver, rather than
        # re-fetched at the emit site: this records the identity of the
        # strategy that produced THIS delegation's preferences, and cannot
        # drift from it if the capability is swapped mid-session. Stays
        # None on every path where the matrix did not produce the
        # preferences (explicit provider_preferences pin, agent-level
        # defaults, no model_role at all) -- claiming a matrix produced
        # preferences it never saw would be worse than saying nothing.
        routing_matrix: dict[str, Any] | None = None
        if raw_model_role and provider_preferences is None:
            resolver = (
                self.coordinator.get_capability("model_role_resolver")
                if hasattr(self.coordinator, "get_capability")
                else None
            )
            routing_matrix = _matrix_provenance(resolver)
            if resolver is None:
                logger.warning(
                    "model_role '%s' specified but no model_role_resolver "
                    "capability is registered (install a routing bundle)",
                    raw_model_role,
                )
            else:
                resolved = await resolver.resolve(raw_model_role)
                if resolved:
                    # Resolver returns list[ProviderPreference] (foundation public type).
                    provider_preferences = list(resolved)
                else:
                    resolver_name = getattr(resolver, "name", type(resolver).__name__)
                    logger.warning(
                        "model_role '%s' resolved to no candidates against "
                        "installed providers (resolver=%s)",
                        raw_model_role,
                        resolver_name,
                    )

                    # Silent-substitution hazard: with provider_preferences
                    # left None, the delegation proceeds and quietly lands
                    # on the session's default model -- indistinguishable
                    # from a caller who never asked for routing at all. This
                    # can also occur after list_models retry-exhaustion
                    # (persistent provider outage) leaves the resolver with
                    # nothing to match. Always emit a structured event here,
                    # regardless of strict_model_role, so operators can
                    # detect the substitution even when they haven't opted
                    # into fail-loud mode.
                    unresolved_hooks = (
                        self.coordinator.get("hooks")
                        if hasattr(self.coordinator, "get")
                        else None
                    )
                    if unresolved_hooks:
                        await unresolved_hooks.emit(
                            "delegate:model_role_unresolved",
                            {
                                "model_role": raw_model_role,
                                "agent": agent_name,
                                "resolver": resolver_name,
                                "fallback_behavior": "session_default",
                                # Same additive/omitted-when-unknown contract
                                # as delegate:agent_spawned below. "Which
                                # matrix file failed to serve this role" is
                                # the first question asked of this event, and
                                # a shadowing user file is a leading cause.
                                **(
                                    {"routing_matrix": routing_matrix}
                                    if routing_matrix
                                    else {}
                                ),
                            },
                        )

                    # Default behavior (preserved for every existing caller):
                    # warn + emit the event above, then fall through with
                    # provider_preferences left None -- session default
                    # model is used. Only when the operator has explicitly
                    # opted into strict_model_role do we fail loud instead.
                    if self.strict_model_role:
                        raise ModelRoleUnresolvedError(
                            f"model_role '{raw_model_role}' resolved to no "
                            f"candidates against installed providers "
                            f"(resolver={resolver_name}). strict_model_role "
                            "is enabled, so this delegation is refused "
                            "instead of silently substituting the session "
                            "default model."
                        )

        # Layer 1 call-budget: per-call override (spec: 298-replacement,
        # highest-precedence rank). None means "no override supplied" --
        # falls through to this module's own settings.max_llm_calls default
        # in _resolve_call_budget. Validated eagerly here (not deferred to
        # spawn) so a bad value is reported against the call that supplied
        # it, matching the "Validate instruction" check just below.
        #
        # Deliberately NOT collapsed via _validate_call_budget: an explicit
        # 0 here must be distinguishable from "not supplied" (None), so
        # _resolve_call_budget can tell "opt out of the budget for this one
        # call" apart from "say nothing, use the module default" -- the
        # collapse (0 -> None) happens there, once that distinction has
        # already been used.
        raw_max_llm_calls = input.get("max_llm_calls")
        call_budget_override: int | None = None
        if raw_max_llm_calls is not None:
            try:
                _check_call_budget_type(raw_max_llm_calls)
            except (TypeError, ValueError) as e:
                return ToolResult(
                    success=False, error={"message": f"Invalid max_llm_calls: {e}"}
                )
            call_budget_override = raw_max_llm_calls

        # Validate instruction (always required)
        if not instruction:
            return ToolResult(
                success=False, error={"message": "Instruction cannot be empty"}
            )

        # Get hooks for event emission
        hooks = self.coordinator.get("hooks")

        # Route based on session_id presence
        if session_id:
            if not self.session_resume_enabled:
                return ToolResult(
                    success=False,
                    error={"message": "Session resumption is disabled"},
                )
            # provider_preferences / raw_model_role are threaded here for the
            # same reason the spawn branch below threads them: a caller that
            # pins a model for a delegation must get that model on EVERY leg
            # of it, not just the first. Both are already fully resolved
            # above (the model_role -> preferences resolution runs before
            # this branch), so the resume path receives exactly what the
            # spawn path would have.
            return await self._resume_existing_session(
                session_id,
                instruction,
                hooks,
                tool_call_id=tool_call_id,
                parallel_group_id=parallel_group_id,
                provider_preferences=provider_preferences,
                raw_model_role=raw_model_role,
            )

        # SPAWN MODE: Create new agent session (requires agent)
        if not agent_name:
            return ToolResult(
                success=False,
                error={
                    "message": "Agent name required for new delegation (or provide session_id to resume)"
                },
            )

        # Check agent exists in registry (with special handling for "self" and bundle paths)
        agents = self.coordinator.config.get("agents", {})

        # Handle special "self" value
        if agent_name == "self":
            if not self.self_delegation_enabled:
                return ToolResult(
                    success=False,
                    error={"message": "Self-delegation is disabled"},
                )

            # Check recursion depth limit
            current_depth = (
                self.coordinator.get_capability("self_delegation_depth") or 0
            )
            if current_depth >= self.max_self_delegation_depth:
                return ToolResult(
                    success=False,
                    error={
                        "message": f"Self-delegation depth limit ({self.max_self_delegation_depth}) exceeded. "
                        f"Current depth: {current_depth}. "
                        "Break the recursion by delegating to a named agent or completing the task.",
                        "code": "SELF_DELEGATION_DEPTH_EXCEEDED",
                    },
                )
            # Self-delegation uses parent's bundle - spawn capability handles it
            pass
        elif ":" in agent_name:
            # Bundle path format (e.g., "foundation:agents/explorer")
            # Skip registry validation - spawn capability handles bundle resolution
            pass
        elif agent_name not in agents:
            return ToolResult(
                success=False,
                error={
                    "message": f"Agent '{agent_name}' not found. Available: {list(agents.keys())}"
                },
            )

        # Apply agent-level default provider_preferences if caller didn't specify
        if provider_preferences is None and self.provider_selection_enabled:
            agent_cfg = agents.get(agent_name, {})
            agent_default_prefs = agent_cfg.get("provider_preferences", [])
            if agent_default_prefs:
                provider_preferences = [
                    ProviderPreference.from_dict(p) for p in agent_default_prefs
                ]

        return await self._spawn_new_session(
            agent_name=agent_name,
            instruction=instruction,
            context_depth=context_depth,
            context_scope=context_scope,
            context_turns=context_turns,
            provider_preferences=provider_preferences,
            hooks=hooks,
            tool_call_id=tool_call_id,
            parallel_group_id=parallel_group_id,
            raw_model_role=raw_model_role,
            agents=agents,
            call_budget_override=call_budget_override,
            routing_matrix=routing_matrix,
        )

    def _resolve_call_budget(
        self,
        agent_name: str,
        call_override: int | None,
    ) -> int | None:
        """Resolve the per-leg LLM-call budget for a delegation.

        ``None`` means "no Layer 1 budget for this delegation" -- Layer 3
        (the delegate's own wall-clock ``settings.timeout``) still applies
        regardless.

        Precedence (highest first):
          1. ``call_override`` -- the per-call ``max_llm_calls`` tool input,
             already validated by ``execute()``.
          2. ``self.max_llm_calls`` -- this module's ``settings.max_llm_calls``
             default (``None`` at S0 -- ships dark).

        NOT implemented: a per-agent frontmatter override
        (``agents[agent_name]["budget"]["max_llm_calls"]``, spec §6.1,
        precedence rank 2 of 4). Verified empirically that a top-level
        ``budget:`` block in an agent ``.md``'s frontmatter is dropped --
        ``amplifier_foundation.bundle._dataclass._load_agent_file_metadata``
        only forwards a fixed allowlist of top-level keys (``tools``,
        ``providers``, ``hooks``, ``session``, ``provider_preferences``,
        ``model_role``, ``agents``); ``budget`` is not among them. Wiring
        this rank now would silently no-op for every agent file. See
        ``tests/test_delegate_call_budget.py``'s
        ``test_agent_frontmatter_budget_key_is_dropped`` for the
        reproducing test, and the module README's "Known gaps" section.
        ``agent_name`` is accepted (and intentionally unused today) so this
        signature does not need to change again once that gap is closed.
        """
        del agent_name  # unused until per-agent frontmatter budget lands
        if call_override is not None:
            return call_override or None  # 0 -> None (explicit opt-out)
        return self.max_llm_calls

    async def _spawn_new_session(
        self,
        agent_name: str,
        instruction: str,
        context_depth: str,
        context_scope: str,
        context_turns: int,
        provider_preferences: list | None,
        hooks,
        *,
        tool_call_id: str = "",
        parallel_group_id: str | None = None,
        raw_model_role: str = "",
        agents: dict | None = None,
        call_budget_override: int | None = None,
        routing_matrix: dict[str, Any] | None = None,
    ) -> ToolResult:
        """Spawn a new agent sub-session.

        Core spawn logic extracted for testability. Called by execute() after
        input validation; may also be called directly in tests.

        Args:
            agent_name: Agent to delegate to
            instruction: Task instruction (may include inherited context)
            context_depth: HOW MUCH context to inherit
            context_scope: WHICH content to inherit
            context_turns: Number of recent turns (when context_depth="recent")
            provider_preferences: Resolved provider preferences list
            hooks: Hook coordinator for event emission
            call_budget_override: Per-call Layer 1 budget override (spec:
                298-replacement), already validated by execute(). None means
                "no override" -- falls through to settings.max_llm_calls.
            tool_call_id: Orchestrator tool call ID (enriches event payloads)
            parallel_group_id: Parallel group ID (enriches event payloads)
            raw_model_role: Raw model role string for routing tracking
            agents: Agent config dict (defaults to coordinator.config["agents"])
            routing_matrix: Matrix provenance captured by execute() from the
                ``model_role_resolver`` capability that produced
                ``provider_preferences`` (see :func:`_matrix_provenance`).
                ``None`` -- the default, and what every caller that does not
                supply it gets -- omits the field from the emitted event
                entirely, leaving the payload byte-identical to before.

        Returns:
            ToolResult with spawn outcome
        """
        parent_session_id = self.coordinator.session_id

        # Generate hierarchical sub-session ID (sanitized for filesystem safety)
        sub_session_id = generate_sub_session_id(
            agent_name=agent_name,
            parent_session_id=parent_session_id,
        )

        # Record the raw agent_name for this sub-session so a later resume
        # (via _resume_existing_session) can recover the exact identity used
        # here, instead of re-deriving a lossy, sanitized approximation from
        # the session_id suffix. See _resolve_agent_for_session().
        self._session_agents[sub_session_id] = agent_name

        # Record the routing this delegation resolved to, for the same
        # reason: a later resume of THIS sub-session must be able to route
        # the way the caller asked here, even when the resume call itself
        # says nothing about routing. See _session_routing's declaration and
        # _resolve_routing_for_session().
        self._session_routing[sub_session_id] = {
            "model_role": raw_model_role or None,
            "provider_preferences": (
                list(provider_preferences) if provider_preferences else None
            ),
        }

        # Resolve agents from coordinator config if not provided directly
        if agents is None:
            try:
                agent_configs_raw = self.coordinator.config.get("agents", {})
                agents = (
                    agent_configs_raw if isinstance(agent_configs_raw, dict) else {}
                )
            except Exception as e:
                logger.warning(
                    "Failed to resolve agents config, defaulting to empty: %s",
                    e,
                    exc_info=True,
                )
                agents = {}

        try:
            # Get spawn capability
            spawn_fn = self.coordinator.get_capability("session.spawn")
            if spawn_fn is None:
                return ToolResult(
                    success=False,
                    error={
                        "message": "Session spawning not available. App layer must register 'session.spawn' capability."
                    },
                )

            # Get parent session
            parent_session = self.coordinator.session

            # Emit delegate:agent_spawned event.
            #
            # `routing_matrix` is ADDITIVE and OMITTED when unknown -- see
            # _matrix_provenance. Two backward-compatibility properties
            # follow from that, both deliberate:
            #
            #   1. Consumers that ignore the field are unaffected: this is a
            #      dict payload, and an extra key changes nothing for a
            #      reader that does not look for it. No existing key's name,
            #      type, or value changes.
            #   2. Analyzers reading OLD captures still work: they must read
            #      it with .get("routing_matrix"), and absent means UNKNOWN
            #      (this capture predates the field, or no matrix strategy
            #      reported a source) -- NOT "no shadowing". Every capture on
            #      disk today is in that state, so an analyzer that treats
            #      absence as a negative assertion would silently mis-clear
            #      exactly the shadowed sessions this field exists to catch.
            if hooks:
                await hooks.emit(
                    "delegate:agent_spawned",
                    {
                        "agent": agent_name,
                        "sub_session_id": sub_session_id,
                        "parent_session_id": parent_session_id,
                        "context_depth": context_depth,
                        "context_scope": context_scope,
                        "tool_call_id": tool_call_id,
                        "parallel_group_id": parallel_group_id,
                        "model_role": raw_model_role or None,
                        "provider_preferences": (
                            [p.to_dict() for p in provider_preferences]
                            if provider_preferences
                            else None
                        ),
                        **(
                            {"routing_matrix": routing_matrix} if routing_matrix else {}
                        ),
                    },
                )

            # Build tool inheritance policy
            tool_inheritance = {}
            if self.exclude_tools:
                tool_inheritance["exclude_tools"] = self.exclude_tools

            # Build hook inheritance policy
            hook_inheritance = {}
            if self.exclude_hooks:
                hook_inheritance["exclude_hooks"] = self.exclude_hooks

            # Build inherited context using two-parameter system
            parent_messages = None
            if self.context_inheritance_enabled and context_depth != "none":
                parent_messages = await self._build_inherited_context(
                    context_depth, context_turns, context_scope
                )

            # Format parent context into instruction
            effective_instruction = instruction
            if parent_messages:
                logger.debug(
                    f"Built {len(parent_messages)} context messages (depth={context_depth}, scope={context_scope})"
                )
                context_text = self._format_parent_context_for_instruction(
                    parent_messages
                )
                effective_instruction = f"{context_text}\n\n[YOUR TASK]\n{instruction}"

            # Structured delegation return contract (flag-gated; additive).
            # Per-agent opt-out: an agent's meta.return_contract: false (forwarded
            # into agent config by _dataclass.py) suppresses injection even when
            # the feature is globally enabled. Defaults to inheriting the global
            # flag -- this is an opt-out, not a per-agent opt-in matrix (see spec §3.6).
            agent_cfg = (agents or {}).get(agent_name, {})
            if self.return_contract_enabled and agent_cfg.get("return_contract", True):
                effective_instruction = (
                    f"{effective_instruction}\n\n{RETURN_CONTRACT_INSTRUCTION}"
                )

            # Extract orchestrator config from parent session for inheritance.
            # Guard with isinstance to handle non-dict orchestrator values gracefully
            # (e.g. when orchestrator is a string like "loop-basic").
            orchestrator_config: dict[str, Any] = {}
            parent_config = parent_session.config or {}
            session_config = parent_config.get("session", {})
            orch_section = session_config.get("orchestrator", {})
            if isinstance(orch_section, dict):
                if orch_config := orch_section.get("config"):
                    # Copy: never mutate the parent's own config dict below.
                    orchestrator_config = dict(orch_config)
                    logger.debug(
                        f"Inheriting orchestrator config: {orchestrator_config}"
                    )

            # Layer 1: resolve the per-leg LLM-call budget for this child
            # (spec: 298-replacement). None means "no Layer 1 budget" --
            # the key is then left untouched, so whatever max_iterations
            # the parent's own inherited orchestrator config already
            # carries (rank 4 -- typically unlimited) is what the child
            # gets. Ships dark at S0: settings.max_llm_calls defaults to
            # None, so this is a no-op until a caller opts in.
            call_budget = self._resolve_call_budget(agent_name, call_budget_override)
            if call_budget is not None:
                orchestrator_config["max_iterations"] = call_budget
                orchestrator_config["budget_warn_ratio"] = self.budget_warn_ratio
            orchestrator_config_out: dict[str, Any] | None = orchestrator_config or None

            # Calculate self-delegation depth for child session
            # Named agents reset to 0, self-delegation increments
            if agent_name == "self":
                current_depth = (
                    self.coordinator.get_capability("self_delegation_depth") or 0
                )
                child_self_delegation_depth = current_depth + 1
            else:
                child_self_delegation_depth = 0  # Named agents start fresh chain

            # Build session metadata for child session.
            # agent_name is always included; tool_call_id and parallel_group_id
            # are only included when present so callers can test for key presence.
            session_metadata: dict[str, Any] = {"agent_name": agent_name}
            if tool_call_id:
                session_metadata["tool_call_id"] = tool_call_id
            if parallel_group_id:
                session_metadata["parallel_group_id"] = parallel_group_id

            # Spawn agent sub-session (with optional session-level timeout)
            #
            # The spawn function is an app-layer capability registered on the
            # coordinator. It receives ALL kwargs below, but not all are handled
            # by PreparedBundle.spawn() directly.
            #
            # Kwargs forwarded to PreparedBundle.spawn():
            #   instruction, parent_session, sub_session_id (as session_id),
            #   orchestrator_config, parent_messages, provider_preferences,
            #   self_delegation_depth
            #
            # Kwargs handled by the app-layer spawn capability:
            #   agent_name: Resolved to a Bundle by the app
            #   agent_configs: Used by the app to find agent configuration
            #   tool_inheritance: App-layer policy for tool filtering
            #   hook_inheritance: App-layer policy for hook filtering
            #
            # See session_spawner.py in amplifier-app-cli for the reference
            # app-layer implementation that handles all kwargs.
            # See examples/07_full_workflow.py for a minimal reference.
            spawn_coro = spawn_fn(
                agent_name=agent_name,
                instruction=effective_instruction,
                parent_session=parent_session,
                agent_configs=agents,
                sub_session_id=sub_session_id,
                tool_inheritance=tool_inheritance,
                hook_inheritance=hook_inheritance,
                orchestrator_config=orchestrator_config_out,
                provider_preferences=provider_preferences,
                self_delegation_depth=child_self_delegation_depth,
                session_metadata=session_metadata,
            )
            result = await self._await_child_with_deadline(spawn_coro)

            # Structured delegation return contract: parse the sub-agent's
            # response once (no-op when the feature is disabled -- see
            # _parse_return_contract). Reused below for both the completion
            # telemetry and the annotated output.
            contract, cleaned_response = self._parse_return_contract(
                result.get("output", "")
            )

            # Emit delegate:agent_completed event
            if hooks:
                await hooks.emit(
                    "delegate:agent_completed",
                    {
                        "agent": agent_name,
                        "sub_session_id": sub_session_id,
                        "parent_session_id": parent_session_id,
                        "success": True,
                        "tool_call_id": tool_call_id,
                        "parallel_group_id": parallel_group_id,
                        **_return_contract_event_fields(contract),
                    },
                )

            # Negotiated-feature seam (spec: 298-replacement §4.4). A budget
            # was requested (call_budget is not None) but the child's
            # orchestrator reported no llm_call_budget telemetry -- either
            # it doesn't implement max_iterations at all (e.g. a
            # third-party orchestrator, or loop-basic), or it silently
            # ignored the config key. Layer 1 bounding is NOT active for
            # this delegation in that case; only the wall-clock backstop
            # (settings.timeout) applies. Silence is the failure mode this
            # spec exists to eliminate, so make it loud rather than let the
            # caller believe a budget is enforced when it is not.
            result_metadata = result.get("metadata") or {}
            budget_enforced = True
            if call_budget is not None:
                budget_enforced = "llm_call_budget" in result_metadata
                if not budget_enforced:
                    logger.warning(
                        "Delegate requested an LLM-call budget of %s for agent "
                        "%r, but the child's orchestrator reported no budget "
                        "telemetry. Layer 1 bounding is NOT active for this "
                        "delegation; only the %ss wall-clock backstop applies.",
                        call_budget,
                        agent_name,
                        self.timeout,
                    )

            # Build provider routing summary (only when routing was requested)
            # Always include both keys for a stable dict shape — consumers
            # can safely read provider_routing["model_role"] without KeyError.
            provider_routing = None
            if raw_model_role or provider_preferences:
                provider_routing = {
                    "model_role": raw_model_role or None,
                    "resolved": (
                        [p.to_dict() for p in provider_preferences]
                        if provider_preferences
                        else None
                    ),
                }

            # Merge the budget_enforced flag into the metadata bag we
            # forward, without mutating the child's own returned dict.
            output_metadata = dict(result_metadata)
            if call_budget is not None:
                output_metadata["budget_enforced"] = budget_enforced

            # Return output with session_id for multi-turn capability.
            # "response" is `cleaned_response` -- byte-identical to
            # result["output"] whenever the feature is disabled, parsing
            # failed, or strip_block is off; the fenced json block is only
            # ever removed from it on a successful, flag-enabled parse (see
            # _parse_return_contract). "contract" is purely additive.
            session_id_result = result["session_id"]
            return ToolResult(
                success=True,
                output={
                    "response": cleaned_response,
                    "session_id": session_id_result,
                    "agent": agent_name,
                    "turn_count": result.get("turn_count", 1),
                    "status": result.get("status", "success"),
                    "metadata": output_metadata,
                    "contract": contract,
                    **(
                        {"provider_routing": provider_routing}
                        if provider_routing
                        else {}
                    ),
                },
            )

        except asyncio.CancelledError:
            # Parent session cancelled — emit diagnostic event then re-raise.
            # CancelledError inherits from BaseException, not Exception,
            # so existing handlers never catch it. This makes cancellation
            # visible to hooks/observers.
            if hooks:
                await hooks.emit(
                    "delegate:agent_cancelled",
                    {
                        "agent": agent_name,
                        "sub_session_id": sub_session_id,
                        "parent_session_id": parent_session_id,
                        "tool_call_id": tool_call_id,
                    },
                )
            raise

        except _DelegateTimeoutExpired:
            recovery_msg = (
                "Child cancellation cleanup is still in progress; do not resume "
                "this session until cleanup and persistence complete."
            )
            timeout_msg = (
                f"Agent '{agent_name}' timed out after {self.timeout}s "
                f"(delegate tool session-level timeout). {recovery_msg}"
            )
            logger.warning(timeout_msg)
            if hooks:
                await hooks.emit(
                    "delegate:error",
                    {
                        "agent": agent_name,
                        "sub_session_id": sub_session_id,
                        "parent_session_id": parent_session_id,
                        "error": timeout_msg,
                        "error_type": "delegate_timeout",
                        "status": "timed_out",
                        "timeout_seconds": self.timeout,
                        "resumable": False,
                        "resume_status": "pending_child_cleanup",
                        "tool_call_id": tool_call_id,
                        "parallel_group_id": parallel_group_id,
                    },
                )
            return ToolResult(
                success=False,
                output={
                    "session_id": sub_session_id,
                    "agent": agent_name,
                    "status": "timed_out",
                    "metadata": {
                        "timeout_seconds": self.timeout,
                        "resumable": False,
                        "resume_status": "pending_child_cleanup",
                        "recovery_message": recovery_msg,
                    },
                },
                error={"message": timeout_msg},
            )

        except Exception as e:
            # Emit delegate:error event — include the exception type so the
            # caller can distinguish provider errors, kernel errors, etc.
            error_type = type(e).__name__
            error_detail = str(e) or "(no detail)"
            error_msg = f"Agent delegation failed ({error_type}): {error_detail}"
            if hooks:
                await hooks.emit(
                    "delegate:error",
                    {
                        "agent": agent_name,
                        "sub_session_id": sub_session_id,
                        "parent_session_id": parent_session_id,
                        "error": error_msg,
                        "tool_call_id": tool_call_id,
                        "parallel_group_id": parallel_group_id,
                    },
                )
            return ToolResult(success=False, error={"message": error_msg})

    def _resolve_agent_for_session(self, session_id: str) -> str:
        """Resolve the agent identity for a (possibly resumed) sub-session.

        Source priority (most to least reliable):
        1. ``self._session_agents`` -- the raw agent_name recorded by THIS
           tool instance when it originally spawned ``session_id``. Exact
           match to the ``agent`` field emitted in ``delegate:agent_spawned``,
           so spawned/resumed/completed events pair correctly under the same
           agent identity.
        2. The session_id suffix -- ``generate_sub_session_id`` always
           produces IDs shaped ``{parent_span}-{child_span}_{sanitized_name}``
           (see amplifier_foundation.tracing). The sanitizer maps every
           non-alphanumeric character -- including "_" itself -- to "-", so
           the suffix after the LAST "_" is unambiguous and always present.
           This is a deterministic parse of a documented format, not a guess,
           but it is lossy: the sanitized name is lowercased and punctuation
           (e.g. the ":" in "foundation:explorer") is collapsed to hyphens.
           Used when the cache misses -- e.g. resuming a sub-session spawned
           by a different parent session/process than the one calling this
           method now.

        Args:
            session_id: Full sub-session ID to resolve.

        Returns:
            The agent name, or "unknown" if neither source yields one.
        """
        cached = self._session_agents.get(session_id)
        if cached:
            return cached

        if "_" in session_id:
            suffix = session_id.rsplit("_", 1)[-1]
            if suffix:
                return suffix

        return "unknown"

    def _resolve_routing_for_session(
        self, session_id: str
    ) -> tuple[str | None, list | None]:
        """Recover the routing recorded when ``session_id`` was spawned.

        Source priority mirrors :meth:`_resolve_agent_for_session`: this
        tool's own spawn-time record, else nothing. Unlike agent identity
        there is no lossy fallback to parse out of the session_id -- routing
        is not encoded there -- so a cold cache returns ``(None, None)`` and
        the app layer's own recovery (agent overlay, then persisted mount
        plan) takes over.

        Returns:
            ``(model_role, provider_preferences)``, either of which may be
            ``None``.
        """
        recorded = self._session_routing.get(session_id) or {}
        return recorded.get("model_role"), recorded.get("provider_preferences")

    async def _resume_existing_session(
        self,
        session_id: str,
        instruction: str,
        hooks,
        *,
        tool_call_id: str = "",
        parallel_group_id: str | None = None,
        provider_preferences: list | None = None,
        raw_model_role: str = "",
    ) -> ToolResult:
        """Resume existing agent session.

        Args:
            session_id: Full agent session ID to resume (from previous delegate call)
            instruction: Follow-up instruction
            hooks: Hook coordinator for event emission
            tool_call_id: Orchestrator tool call ID (enriches event payloads)
            parallel_group_id: Parallel group ID (enriches event payloads)
            provider_preferences: Preferences resolved by execute() for THIS
                resume call, if the caller pinned any. ``None`` falls back to
                whatever was recorded when the sub-session was spawned.
            raw_model_role: The raw model role string supplied on THIS resume
                call, if any. Same fallback as ``provider_preferences``.

        Returns:
            ToolResult with success status and output or error
        """
        parent_session_id = self.coordinator.session_id
        resume_agent = None
        if "_" in session_id:
            resume_agent = session_id.rsplit("_", 1)[-1] or None

        # Resolve agent identity BEFORE the try block (and before emitting
        # any events), from the most reliable in-repo source available
        # (spawn-time cache, or the session_id suffix convention as
        # fallback). This is what lets delegate:agent_resumed -- and every
        # delegate:error / delegate:agent_cancelled / delegate:agent_completed
        # event on this path -- carry a real "agent" value instead of
        # omitting the field entirely. Computed outside the try/except so it
        # is unconditionally bound in every except branch below (pyright:
        # a name only assigned inside a try body is "possibly unbound" in
        # its except clauses, since any earlier statement could have raised
        # first).
        agent_name = self._resolve_agent_for_session(session_id)

        # Resolve the routing this leg should run under. Precedence:
        #   1. what the caller stated on THIS resume call (already resolved
        #      by execute(): model_role -> provider_preferences), then
        #   2. what this tool recorded when it spawned the sub-session.
        # Only when the caller stated NEITHER do we fall back, so an
        # explicit resume-time pin is never quietly overruled by history.
        effective_model_role: str | None = raw_model_role or None
        effective_preferences: list | None = provider_preferences
        if effective_model_role is None and effective_preferences is None:
            effective_model_role, effective_preferences = (
                self._resolve_routing_for_session(session_id)
            )

        try:
            # Use session_id as-is (no short ID resolution - LLMs can handle full IDs)
            full_session_id = session_id

            # Emit delegate:agent_resumed event.
            # Payload shape is kept consistent with delegate:agent_spawned's
            # "agent" field (see _spawn_new_session) so downstream counters
            # can pair spawned/resumed/completed events by agent identity.
            if hooks:
                await hooks.emit(
                    "delegate:agent_resumed",
                    {
                        "agent": agent_name,
                        "session_id": full_session_id,
                        "parent_session_id": parent_session_id,
                        "tool_call_id": tool_call_id,
                        "parallel_group_id": parallel_group_id,
                        # Same two fields, same shape, as delegate:agent_spawned.
                        # Additive: a consumer that does not read them is
                        # unaffected, and absence in an OLD capture means
                        # UNKNOWN (predates this field), never "no routing".
                        # Their whole point is that the drop this fix closes
                        # was invisible in telemetry -- spawned carried a role,
                        # resumed carried nothing to compare it against.
                        "model_role": effective_model_role,
                        "provider_preferences": (
                            [p.to_dict() for p in effective_preferences]
                            if effective_preferences
                            else None
                        ),
                    },
                )

            # Get resume capability
            resume_fn = self.coordinator.get_capability("session.resume")
            if resume_fn is None:
                return ToolResult(
                    success=False,
                    error={
                        "message": "Session resumption not available. App layer must register 'session.resume' capability."
                    },
                )

            # Structured delegation return contract (flag-gated; additive).
            # Opt-out is resolved via `agent_name`, which is the same identity
            # `_resolve_agent_for_session` computed above (before the try block)
            # for event emission -- consistent with the spawn path's opt-out.
            effective_instruction = instruction
            if self.return_contract_enabled:
                agents_cfg = self.coordinator.config.get("agents", {})
                agent_cfg = (
                    agents_cfg.get(agent_name, {})
                    if isinstance(agents_cfg, dict)
                    else {}
                )
                if agent_cfg.get("return_contract", True):
                    effective_instruction = (
                        f"{instruction}\n\n{RETURN_CONTRACT_INSTRUCTION}"
                    )

            # Thread the caller's routing across the app-layer seam.
            #
            # This is the fix for the measured "resume wipes the model role"
            # defect: this call used to be (sub_session_id, instruction)
            # only, so provider_preferences and model_role never reached the
            # app layer and the resumed leg fell back to settings priority
            # -- a silent downgrade, invisible until it was caught on the
            # wire. The kwargs are OPTIONAL on the capability (see
            # _supported_resume_routing_kwargs), so an app layer that
            # predates them keeps working unchanged; what it cannot do is
            # drop them quietly.
            resume_routing: dict[str, Any] = {}
            if effective_preferences is not None:
                resume_routing["provider_preferences"] = effective_preferences
            if effective_model_role is not None:
                resume_routing["model_role"] = effective_model_role

            if resume_routing:
                supported = _supported_resume_routing_kwargs(resume_fn)
                unsupported = sorted(set(resume_routing) - supported)
                if unsupported:
                    logger.warning(
                        "session.resume capability does not accept %s -- resuming "
                        "session %s WITHOUT the caller's routing, which may resolve "
                        "to a different provider/model than the spawn leg. Update "
                        "the app layer's resume capability to accept %s.",
                        ", ".join(unsupported),
                        full_session_id,
                        ", ".join(_RESUME_ROUTING_KWARGS),
                    )
                resume_routing = {
                    k: v for k, v in resume_routing.items() if k in supported
                }

            # Resume agent session (with optional session-level timeout)
            resume_coro = resume_fn(
                sub_session_id=full_session_id,
                instruction=effective_instruction,
                **resume_routing,
            )
            result = await self._await_child_with_deadline(resume_coro)

            # Structured delegation return contract (see the spawn path for
            # the full explanation) -- computed once, reused for telemetry
            # and the annotated output below.
            contract, cleaned_response = self._parse_return_contract(
                result.get("output", "")
            )

            # Emit delegate:agent_completed event
            if hooks:
                await hooks.emit(
                    "delegate:agent_completed",
                    {
                        "agent": agent_name,
                        "sub_session_id": full_session_id,
                        "parent_session_id": parent_session_id,
                        "success": True,
                        "tool_call_id": tool_call_id,
                        "parallel_group_id": parallel_group_id,
                        **_return_contract_event_fields(contract),
                    },
                )

            # Return output with session info. "response" is `cleaned_response`
            # -- see the spawn path's comment for the exact byte-identity
            # guarantee this preserves in the disabled/non-conformant paths.
            # `agent_name` was already resolved above (before the try block)
            # via `_resolve_agent_for_session` -- no re-derivation here.
            session_id_result = result["session_id"]
            return ToolResult(
                success=True,
                output={
                    "response": cleaned_response,
                    "session_id": session_id_result,
                    "agent": agent_name,
                    "turn_count": result.get("turn_count", 1),
                    "status": result.get("status", "success"),
                    "metadata": result.get("metadata", {}),
                    "contract": contract,
                },
            )

        except ValueError as e:
            # Session ID resolution error
            if hooks:
                await hooks.emit(
                    "delegate:error",
                    {
                        "agent": agent_name,
                        "session_id": session_id,
                        "parent_session_id": parent_session_id,
                        "error": str(e),
                        "tool_call_id": tool_call_id,
                        "parallel_group_id": parallel_group_id,
                    },
                )
            return ToolResult(success=False, error={"message": str(e)})

        except FileNotFoundError as e:
            # Session not found
            if hooks:
                await hooks.emit(
                    "delegate:error",
                    {
                        "agent": agent_name,
                        "session_id": session_id,
                        "parent_session_id": parent_session_id,
                        "error": f"Session not found: {str(e)}",
                        "tool_call_id": tool_call_id,
                        "parallel_group_id": parallel_group_id,
                    },
                )
            return ToolResult(
                success=False,
                error={
                    "message": f"Agent session '{session_id}' not found. May have expired or never existed."
                },
            )

        except asyncio.CancelledError:
            # Parent session cancelled — emit diagnostic event then re-raise.
            # CancelledError inherits from BaseException, not Exception,
            # so existing handlers never catch it. This makes cancellation
            # visible to hooks/observers.
            if hooks:
                await hooks.emit(
                    "delegate:agent_cancelled",
                    {
                        "agent": self._resolve_agent_for_session(session_id),
                        "session_id": session_id,
                        "parent_session_id": parent_session_id,
                        "tool_call_id": tool_call_id,
                    },
                )
            raise

        except _DelegateTimeoutExpired:
            # Resolve agent name for the message the same way as everywhere
            # else on this path (cache first, session_id suffix fallback).
            resume_agent = self._resolve_agent_for_session(session_id)
            agent_label = resume_agent or "unknown"
            recovery_msg = (
                "Child cancellation cleanup is still in progress; do not resume "
                "this session until cleanup and persistence complete."
            )
            timeout_msg = (
                f"Resumed agent '{agent_label}' timed out after {self.timeout}s "
                f"(delegate tool session-level timeout). {recovery_msg}"
            )
            logger.warning(timeout_msg)
            if hooks:
                error_payload = {
                    "session_id": session_id,
                    "parent_session_id": parent_session_id,
                    "error": timeout_msg,
                    "error_type": "delegate_timeout",
                    "status": "timed_out",
                    "timeout_seconds": self.timeout,
                    "resumable": False,
                    "resume_status": "pending_child_cleanup",
                    "tool_call_id": tool_call_id,
                    "parallel_group_id": parallel_group_id,
                }
                if resume_agent is not None:
                    error_payload["agent"] = resume_agent
                await hooks.emit("delegate:error", error_payload)
            timeout_output = {
                "session_id": session_id,
                "status": "timed_out",
                "metadata": {
                    "timeout_seconds": self.timeout,
                    "resumable": False,
                    "resume_status": "pending_child_cleanup",
                    "recovery_message": recovery_msg,
                },
            }
            if resume_agent is not None:
                timeout_output["agent"] = resume_agent
            return ToolResult(
                success=False,
                output=timeout_output,
                error={"message": timeout_msg},
            )

        except Exception as e:
            # Other errors — include exception type for clear source attribution
            error_type = type(e).__name__
            error_detail = str(e) or "(no detail)"
            error_msg = f"Agent resume failed ({error_type}): {error_detail}"
            if hooks:
                await hooks.emit(
                    "delegate:error",
                    {
                        "agent": self._resolve_agent_for_session(session_id),
                        "session_id": session_id,
                        "parent_session_id": parent_session_id,
                        "error": error_msg,
                        "tool_call_id": tool_call_id,
                        "parallel_group_id": parallel_group_id,
                    },
                )
            return ToolResult(success=False, error={"message": error_msg})
