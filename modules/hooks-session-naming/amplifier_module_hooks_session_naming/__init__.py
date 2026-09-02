"""Session Naming Hook Module

Automatically generates human-readable session names and descriptions
using the configured LLM provider. Runs in background, never blocking
the main conversation.
"""

import asyncio
import copy
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from amplifier_core import HookResult

logger = logging.getLogger(__name__)

# Provenance stamped onto every event this module's own LLM call emits.
# The provider writes llm:request / llm:response into the SESSION'S event
# stream through the coordinator it was mounted with, and the kernel adds
# session_id / parent_id defaults -- so without a stamp a naming call is
# structurally indistinguishable from the root agent's own work, and every
# scorer reading events.jsonl counts it as a root response.
NAMING_PURPOSE = "session-naming"
NAMING_ORIGIN = "hooks-session-naming"


class _NamingHooks:
    """Hook-registry view that stamps naming provenance on every event.

    Wraps the real registry: ``emit``/``emit_and_collect`` add
    ``purpose``/``origin_module`` to the payload before it reaches the
    registry, so the fields land in ``data`` in events.jsonl (hooks-logging
    copies unknown payload keys straight through). Everything else is
    forwarded untouched.
    """

    def __init__(self, hooks: Any):
        self._hooks = hooks

    def __getattr__(self, name: str) -> Any:
        return getattr(self._hooks, name)

    @staticmethod
    def _stamp(data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        stamped = dict(data)
        stamped["purpose"] = NAMING_PURPOSE
        stamped["origin_module"] = NAMING_ORIGIN
        return stamped

    async def emit(self, event: str, data: Any = None) -> Any:
        return await self._hooks.emit(event, self._stamp(data))

    async def emit_and_collect(
        self, event: str, data: Any = None, timeout: float | None = None
    ) -> Any:
        return await self._hooks.emit_and_collect(event, self._stamp(data), timeout)


class _NamingCoordinator:
    """Coordinator view whose ``hooks`` stamp naming provenance.

    Handed to a provider *copy* (see ``SessionNamingHook._stamped_provider``)
    so the provider's own ``self.coordinator.hooks.emit`` calls are tagged.
    Every other coordinator attribute is forwarded to the real one.
    """

    def __init__(self, coordinator: Any):
        self._coordinator = coordinator
        self.hooks = _NamingHooks(coordinator.hooks)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._coordinator, name)


@dataclass
class SessionNamingConfig:
    """Configuration for session naming.

    model_role routes naming to a cheap/fast model via the routing matrix.
    Defaults to "fast" — session naming is a simple classification task that
    does not need the priority/expensive model. Set to None to use the
    session's own conversation provider explicitly.

    Whatever model_role resolves to, naming only ever calls the session's own
    conversation provider or a same-vendor sibling of it (see
    ``SessionNamingHook._call_provider``).
    """

    initial_trigger_turn: int = 2
    update_interval_turns: int = 5
    max_name_length: int = 50
    max_description_length: int = 200
    max_retries: int = 3
    model_role: str | None = "fast"


INITIAL_NAMING_PROMPT = """You generate names and descriptions for conversation sessions.

<task>
Analyze this conversation and either:
- Generate a name + description (action: "set")
- Signal insufficient context (action: "defer")
</task>

<guidelines>
NAME (2-6 words):
- Action-oriented: "Debugging X" > "X Discussion"
- Specific: Include key file/project/concept when identifiable
- Human-friendly: Like a chat app conversation title

DESCRIPTION (1-2 sentences max):
- Primary goal or topic
- Key technologies/concepts mentioned
- Must stay concise

DEFER when:
- No specific task identifiable yet
- Only vague questions asked so far
- Conversation is still in "what do you need?" phase
</guidelines>

<conversation>
{context}
</conversation>

Respond with JSON only (no markdown, no explanation):
{{"action": "set"|"defer", "name": "..."|null, "description": "..."|null}}"""


DESCRIPTION_UPDATE_PROMPT = """You check if a session description needs updating.

<current>
Name: {name}
Description: {description}
</current>

<stability_rule>
Only update if scope MEANINGFULLY expanded or shifted. Keep for:
- Refinements of existing topic
- Implementation details
- Minor tangents that returned to main topic

The description must remain concise (1-2 sentences) even as conversation grows.
If scope expanded, rewrite to cover the broader range concisely.
</stability_rule>

<conversation_excerpt>
{context}
</conversation_excerpt>

Respond with JSON only (no markdown, no explanation):
{{"action": "set"|"keep", "name": null, "description": "..."|null}}"""


class SessionNamingHook:
    """Hook handler for automatic session naming."""

    def __init__(self, coordinator: Any, config: SessionNamingConfig):
        self.coordinator = coordinator
        self.config = config
        self._defer_counts: dict[str, int] = {}
        self._pending_tasks: set[asyncio.Task] = set()
        # Tracks which sessions have already received the "model_role
        # resolved to no candidates, falling back" WARNING (see
        # _call_provider). Naming retries every few turns for the life of a
        # session, so without this a stable config gap would re-emit the
        # identical warning on every retry.
        self._role_fallback_warned: set[str] = set()
        # Same dedup, for the "model_role resolved to a provider this session
        # never selected — refusing to borrow it" WARNING.
        self._cross_provider_refused: set[str] = set()
        # Same dedup, for the "cannot stamp this provider's events" WARNING.
        self._unstampable_warned: set[str] = set()
        # id(real provider) -> (real provider, stamped copy). The copy is made
        # once per provider per session: providers create their SDK client
        # lazily, so a fresh copy on every naming call would build a fresh
        # client (and connection pool) every few turns. The real provider is
        # held alongside so its id() cannot be recycled under us.
        self._stamped_providers: dict[int, tuple[Any, Any]] = {}

    async def on_orchestrator_complete(
        self, event: str, data: dict[str, Any]
    ) -> HookResult:
        """Handle orchestrator completion - trigger naming if appropriate.

        Naming runs as a background asyncio task (non-blocking). The task is
        tracked in self._pending_tasks so it is not garbage-collected by Python
        3.12+ before it completes. The session:end handler drains any in-flight
        task before teardown.
        """
        session_id = data.get("session_id")
        if not session_id:
            return HookResult(action="continue")

        # Get session directory from coordinator's session store path
        session_dir = self._get_session_dir(session_id)
        if not session_dir or not session_dir.exists():
            return HookResult(action="continue")

        # Load current metadata
        metadata = self._load_metadata(session_dir)
        # Note: prompt:complete fires BEFORE metadata.json is updated with new turn_count
        # So we add 1 to get the actual current turn number
        stored_turn_count = metadata.get("turn_count", 0)
        current_turn = stored_turn_count + 1
        has_name = metadata.get("name") is not None

        # Initial naming: turn >= initial_trigger and no name yet
        if current_turn >= self.config.initial_trigger_turn and not has_name:
            defer_count = self._defer_counts.get(session_id, 0)
            if defer_count < self.config.max_retries:
                task = asyncio.create_task(
                    self._generate_name(session_id, session_dir, is_update=False)
                )
                self._pending_tasks.add(task)
                task.add_done_callback(self._pending_tasks.discard)

        # Description update: has name and at update interval
        elif (
            has_name
            and current_turn > 0
            and current_turn % self.config.update_interval_turns == 0
        ):
            task = asyncio.create_task(
                self._generate_name(session_id, session_dir, is_update=True)
            )
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)

        return HookResult(action="continue")

    async def on_session_end(self, event: str, data: dict[str, Any]) -> HookResult:
        """Drain any in-flight naming tasks before session teardown.

        Waits up to 15 seconds for each pending task. If a task times out or is
        cancelled, logs at DEBUG and continues — naming is best-effort.
        """
        if not self._pending_tasks:
            return HookResult(action="continue")

        for task in list(self._pending_tasks):
            try:
                await asyncio.wait_for(task, timeout=15.0)
            except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
                logger.debug("Session naming drain timed out or was cancelled: %s", exc)

        return HookResult(action="continue")

    def _get_session_dir(self, session_id: str) -> Path | None:
        """Get session directory path."""
        # Try to get from coordinator's session info
        if hasattr(self.coordinator, "session_dir"):
            return Path(self.coordinator.session_dir)

        # Try standard Amplifier session paths
        home = Path.home()

        # Check in projects structure
        projects_dir = home / ".amplifier" / "projects"
        if projects_dir.exists():
            for project_dir in projects_dir.iterdir():
                if project_dir.is_dir():
                    session_path = project_dir / "sessions" / session_id
                    if session_path.exists():
                        return session_path

        # Check legacy sessions location
        legacy_path = home / ".amplifier" / "sessions" / session_id
        if legacy_path.exists():
            return legacy_path

        return None

    def _load_metadata(self, session_dir: Path) -> dict:
        """Load session metadata."""
        metadata_path = session_dir / "metadata.json"
        if metadata_path.exists():
            try:
                return json.loads(metadata_path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load metadata: {e}")
        return {}

    def _save_metadata(self, session_dir: Path, metadata: dict) -> None:
        """Save session metadata atomically."""
        metadata_path = session_dir / "metadata.json"
        temp_path = session_dir / "metadata.json.tmp"
        try:
            temp_path.write_text(json.dumps(metadata, indent=2))
            temp_path.replace(metadata_path)
        except OSError as e:
            logger.error(f"Failed to save metadata: {e}")
            if temp_path.exists():
                temp_path.unlink()

    async def _generate_name(
        self, session_id: str, session_dir: Path, is_update: bool
    ) -> None:
        """Generate or update session name/description in background."""
        try:
            # Load current metadata
            metadata = self._load_metadata(session_dir)
            current_name = metadata.get("name")
            current_description = metadata.get("description", "")

            # Get conversation context
            context = await self._get_conversation_context(
                session_dir, current_name, current_description
            )
            if not context:
                logger.debug(f"No conversation context for session {session_id[:8]}")
                return

            # Build prompt
            if is_update:
                prompt = DESCRIPTION_UPDATE_PROMPT.format(
                    name=current_name, description=current_description, context=context
                )
            else:
                prompt = INITIAL_NAMING_PROMPT.format(context=context)

            # Call the provider — hard timeout caps stalled providers
            try:
                response = await asyncio.wait_for(
                    self._call_provider(prompt, session_id), timeout=10.0
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Session naming provider call timed out (10 s) for session %s",
                    session_id[:8],
                )
                await self.coordinator.hooks.emit(
                    "session-naming:timeout",
                    {"session_id": session_id, "is_update": is_update},
                )
                return
            if not response:
                return

            # Parse response
            result = self._parse_response(response)
            if not result:
                return

            action = result.get("action")
            now = datetime.now(UTC).isoformat()

            if action == "defer":
                # Increment defer count for retries
                self._defer_counts[session_id] = (
                    self._defer_counts.get(session_id, 0) + 1
                )
                defer_count = self._defer_counts[session_id]
                logger.debug(
                    "Session %s deferred naming (attempt %d)",
                    session_id[:8],
                    defer_count,
                )
                await self.coordinator.hooks.emit(
                    "session-naming:deferred",
                    {"session_id": session_id, "defer_count": defer_count},
                )
                return

            if action == "set":
                # Update metadata
                if not is_update and result.get("name"):
                    name = result["name"][: self.config.max_name_length]
                    metadata["name"] = name
                    metadata["name_generated_at"] = now
                    logger.info("Session %s named: %s", session_id[:8], name)

                if result.get("description"):
                    description = result["description"][
                        : self.config.max_description_length
                    ]
                    metadata["description"] = description
                    metadata["description_updated_at"] = now
                    if is_update:
                        logger.debug("Session %s description updated", session_id[:8])

                self._save_metadata(session_dir, metadata)
                # Clear defer count on success
                self._defer_counts.pop(session_id, None)
                await self.coordinator.hooks.emit(
                    "session-naming:set",
                    {
                        "session_id": session_id,
                        "name": metadata.get("name"),
                        "description": metadata.get("description"),
                        "is_update": is_update,
                    },
                )

            elif action == "keep":
                logger.debug("Session %s description unchanged", session_id[:8])

        except asyncio.CancelledError:
            logger.debug("Naming task cancelled for session %s", session_id[:8])
        except Exception as e:
            logger.error("Error generating name for session %s: %s", session_id[:8], e)
            try:
                await self.coordinator.hooks.emit(
                    "session-naming:error",
                    {"session_id": session_id, "error": str(e)},
                )
            except Exception:
                pass  # emit failure must never suppress the original error path

    async def _get_conversation_context(
        self,
        session_dir: Path,
        current_name: str | None,
        current_description: str | None,
    ) -> str | None:
        """Extract conversation context for naming prompt."""
        # Try to get messages from context manager
        messages = await self._get_messages_from_context()

        # Fallback: read from transcript
        if not messages:
            messages = self._read_transcript(session_dir)

        if not messages:
            return None

        return self._extract_naming_context(messages, current_name, current_description)

    async def _get_messages_from_context(self) -> list[dict] | None:
        """Get messages from the context manager if available."""
        try:
            # Access context manager through coordinator
            context = self.coordinator.mount_points.get("context")
            if context and hasattr(context, "get_messages"):
                messages = await context.get_messages()
                if messages:
                    return messages
        except Exception as e:
            logger.debug(f"Could not get messages from context manager: {e}")
        return None

    def _read_transcript(self, session_dir: Path) -> list[dict]:
        """Read messages from transcript.jsonl file."""
        transcript_path = session_dir / "transcript.jsonl"
        if not transcript_path.exists():
            return []

        messages = []
        try:
            with open(transcript_path) as f:
                for line in f:
                    if line.strip():
                        try:
                            msg = json.loads(line)
                            if msg.get("role") in ("user", "assistant"):
                                messages.append(msg)
                        except json.JSONDecodeError:
                            continue
        except OSError as e:
            logger.warning(f"Failed to read transcript: {e}")

        return messages

    def _extract_naming_context(
        self,
        messages: list[dict],
        current_name: str | None,
        current_description: str | None,
    ) -> str:
        """Extract representative context using bookend + sampling."""
        n = len(messages)
        if n == 0:
            return ""

        parts = []

        # Include prior name/description as context anchor
        if current_name:
            parts.append(f"Current session name: {current_name}")
            if current_description:
                parts.append(f"Current description: {current_description}")
            parts.append("")

        # First 3 turns (original intent)
        parts.append("=== Opening ===")
        for msg in messages[: min(3, n)]:
            content = self._truncate_content(msg.get("content", ""), 400)
            parts.append(f"[{msg.get('role', 'unknown')}]: {content}")

        # Sample from middle if conversation is long
        if n > 10:
            parts.append("")
            parts.append("=== Middle (sampled) ===")
            indices = [n // 4, n // 2, 3 * n // 4]
            for i in indices:
                if 3 <= i < n - 5:
                    msg = messages[i]
                    content = self._truncate_content(msg.get("content", ""), 250)
                    parts.append(f"[{msg.get('role', 'unknown')}]: {content}")

        # Last 5 turns (current state)
        if n > 3:
            parts.append("")
            parts.append("=== Recent ===")
            for msg in messages[-min(5, n - 3) :]:
                content = self._truncate_content(msg.get("content", ""), 400)
                parts.append(f"[{msg.get('role', 'unknown')}]: {content}")

        # Add metadata
        parts.append("")
        parts.append(f"[Total conversation: {n} messages]")

        return "\n".join(parts)

    def _truncate_content(self, content: str, max_len: int) -> str:
        """Truncate content, preferring to break at word boundaries."""
        if not content or len(content) <= max_len:
            return content or ""

        # Handle list content (tool results, etc.)
        if isinstance(content, list):
            content = str(content)

        truncated = content[:max_len]
        # Try to break at a word boundary
        last_space = truncated.rfind(" ")
        if last_space > max_len * 0.7:
            truncated = truncated[:last_space]
        return truncated + "..."

    @staticmethod
    def _priority_of(provider: Any) -> float:
        """Selection priority for one provider (lower wins, default 100).

        Mirrors the streaming orchestrator's own rule (``provider.priority``,
        then ``provider.config["priority"]``, then 100) so that the provider
        this module picks for an unpinned session is *the same one answering
        the conversation*, not an independent guess. Non-numeric values (a
        test double's auto-attribute, a misconfigured string) are ignored
        rather than crashing the comparison.
        """
        candidates = [getattr(provider, "priority", None)]
        config = getattr(provider, "config", None)
        if isinstance(config, dict):
            candidates.append(config.get("priority"))
        for value in candidates:
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return float(value)
        return 100.0

    @staticmethod
    def _vendor_of(provider: Any) -> str | None:
        """Vendor identity of a provider via the kernel contract
        ``get_info().id`` (e.g. ``"anthropic"``), lowercased.

        Returns None when the vendor cannot be established — callers must
        treat that as "cannot prove same vendor" and refuse, never as "no
        conflict". Two mount names sharing an id (anthropic-sonnet /
        anthropic-haiku) are the SAME vendor.
        """
        get_info = getattr(provider, "get_info", None)
        if not callable(get_info):
            return None
        try:
            info = get_info()
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("get_info() failed while checking provider vendor: %s", e)
            return None
        vendor = getattr(info, "id", None)
        if vendor is None and isinstance(info, dict):
            vendor = info.get("id")
        if isinstance(vendor, str) and vendor.strip():
            return vendor.strip().lower()
        return None

    def _same_vendor(self, a: Any, b: Any) -> bool:
        """True only when both vendors are known AND equal (fail closed)."""
        if a is b:
            return True
        vendor_a = self._vendor_of(a)
        vendor_b = self._vendor_of(b)
        return bool(vendor_a and vendor_b and vendor_a == vendor_b)

    def _select_session_provider(
        self, providers: dict[str, Any]
    ) -> tuple[str | None, Any | None]:
        """The provider answering THIS session — never an arbitrary one.

        1. The conversation-scope pin, when the ``conversation.provider_pin``
           capability reports one. A pin naming a provider that is no longer
           mounted returns ``(None, None)``: refuse, never fall through to
           another provider the user did not choose.
        2. Otherwise priority ordering, identical to the orchestrator's rule,
           with insertion order breaking ties — so the result *is* the
           session's own conversation provider rather than
           ``next(iter(providers.values()))`` reached by coincidence.
        """
        get_capability = getattr(self.coordinator, "get_capability", None)
        pinned: str | None = None
        if callable(get_capability):
            try:
                pin_capability = get_capability("conversation.provider_pin")
            except Exception as e:  # pragma: no cover - defensive
                logger.debug("conversation.provider_pin lookup failed: %s", e)
                pin_capability = None
            current = getattr(pin_capability, "current", None)
            if callable(current):
                try:
                    name = current()
                except Exception as e:  # pragma: no cover - defensive
                    logger.debug("conversation.provider_pin.current() failed: %s", e)
                    name = None
                if isinstance(name, str) and name:
                    pinned = name

        if pinned is not None:
            provider = providers.get(pinned)
            if provider is None:
                logger.warning(
                    "This conversation is pinned to provider %r, which is no"
                    " longer mounted. Skipping session naming rather than"
                    " naming on a provider this session never pinned.",
                    pinned,
                )
                return None, None
            return pinned, provider

        ranked = [
            (self._priority_of(provider), index, name, provider)
            for index, (name, provider) in enumerate(providers.items())
        ]
        if not ranked:
            return None, None
        ranked.sort(key=lambda entry: (entry[0], entry[1]))
        _, _, name, provider = ranked[0]
        return name, provider

    @staticmethod
    def _match_resolved_provider(
        providers: dict[str, Any], resolved_name: str
    ) -> tuple[str | None, Any | None]:
        """Mounted provider whose mount name contains the resolved name."""
        if not isinstance(resolved_name, str) or not resolved_name:
            return None, None
        needle = resolved_name.lower()
        for key, provider in providers.items():
            if needle in key.lower():
                return key, provider
        return None, None

    def _stamped_provider(self, provider: Any) -> Any | None:
        """A view of ``provider`` whose emitted events carry naming provenance.

        A provider emits ``llm:request`` / ``llm:response`` through the
        coordinator it holds on ``self.coordinator`` — the ROOT session's
        coordinator — and the kernel stamps ``session_id``/``parent_id``
        defaults onto every event. So a naming call's events are otherwise
        indistinguishable from the root agent's own, and every scorer reading
        events.jsonl counts them as root responses.

        Attribute reads inside the provider's own methods bind to its real
        instance, so a forwarding proxy cannot intercept them — only a copy
        with its own ``coordinator`` attribute can. The copy is shallow: the
        SDK client, config and credentials are shared with the original.

        Returns:
            The stamped copy; the provider itself when it emits nothing
            (no coordinator, so nothing can leak); or None when the copy
            cannot be made or the coordinator cannot be swapped — the caller
            must then SKIP the call rather than emit unattributable events
            into the session's stream.
        """
        base = getattr(provider, "coordinator", None)
        if base is None or not hasattr(base, "hooks"):
            # Nothing is emitted through this provider, so nothing to stamp.
            return provider
        if isinstance(base, _NamingCoordinator):
            return provider

        cached = self._stamped_providers.get(id(provider))
        if cached is not None and cached[0] is provider:
            return cached[1]

        try:
            stamped = copy.copy(provider)
            stamped.coordinator = _NamingCoordinator(base)
        except Exception as e:
            logger.debug("Could not build a stamped provider view: %s", e)
            return None

        if not isinstance(getattr(stamped, "coordinator", None), _NamingCoordinator):
            # e.g. a frozen model that swallowed the assignment.
            return None

        self._stamped_providers[id(provider)] = (provider, stamped)
        return stamped

    def _warn_once(self, session_id: str | None, seen: set[str], *args: Any) -> None:
        """WARNING the first time per session, DEBUG on every repeat.

        Naming retries every few turns for the life of a session, so a stable
        configuration gap would otherwise re-emit the identical warning
        forever.
        """
        warn_key = session_id or ""
        if warn_key not in seen:
            seen.add(warn_key)
            logger.warning(*args)
        else:
            logger.debug(*args)

    async def _call_provider(
        self, prompt: str, session_id: str | None = None
    ) -> str | None:
        """Call the LLM provider to generate name/description.

        THE PROVIDER IS NEVER ARBITRARY. Every path lands on either the
        session's own conversation provider or a same-vendor sibling of it;
        there is no ``next(iter(providers.values()))`` here. A session pinned
        to one provider can never emit a naming call on another vendor: the
        historical bug was that ``model_role`` resolved through the routing
        matrix (whose default matrix is openai) and, failing to match a mount,
        fell through to whichever provider instance happened to be first in
        the mount dict — an order-dependent, silent cross-provider borrow.

        Resolution order (highest to lowest priority):
          1. model_role — resolved via the ``model_role_resolver`` capability,
             ACCEPTED ONLY IF the resolved provider is mounted here and is the
             same vendor as the session's own provider.
          2. The session's own conversation provider (pin, else priority
             order), with no model override.

        model_role resolution requires a routing bundle. When none is
        installed (no model_role_resolver capability registered at all), logs
        a debug message and falls back to #2 — that fallback is legitimate
        and intended.

        When a model_role_resolver IS registered and resolution itself raises
        (e.g. a transient provider API hiccup while listing models), the
        failure mode is unknown and possibly transient. Silently substituting
        the fallback provider in that case could quietly route a cheap
        background chore onto the session's primary/expensive model on every
        retry until the transient error clears. So this case still aborts
        (returns None) and lets the self-retrying trigger try again on a
        later turn.

        But when the resolver runs cleanly and simply resolves to *no
        candidates* for the configured role (e.g. no "fast" model configured
        for the active provider), that is a stable configuration gap, not a
        transient error — retrying later changes nothing. Skipping silently
        in that case means session naming is a feature that quietly never
        runs, with only a log line nobody reads to explain why. So this case
        falls back to #2 and logs a WARNING naming the unresolved role and
        the provider substituted for it — once per session (via
        ``session_id``), since naming retries every few turns and repeating
        the identical warning on every retry would just be noise.

        A resolved candidate that is NOT mounted here, or that belongs to a
        different vendor than the session's own provider, is REFUSED the same
        loud way: warn once, then name on the session's own provider with no
        model override.
        """
        try:
            providers = self.coordinator.get("providers")
            if not providers:
                logger.warning("No provider available for session naming")
                return None

            session_provider_name, session_provider = self._select_session_provider(
                providers
            )
            if session_provider is None:
                # _select_session_provider already logged the specific cause.
                logger.debug("No session provider resolved for session naming")
                return None

            # Resolution order: model_role (same vendor only) > session provider
            provider = None
            provider_name: str | None = None
            model_override: str | None = None
            role_had_no_candidates = False
            refusal: tuple[str, str] | None = None

            if self.config.model_role:
                # Look up the model_role_resolver capability registered by
                # whichever routing bundle (matrix-based, cost-aware, etc.)
                # is active in this session. Duck-typed contract:
                #     async def resolve(model_role) -> list[ProviderPreference]
                resolver = (
                    self.coordinator.get_capability("model_role_resolver")
                    if hasattr(self.coordinator, "get_capability")
                    else None
                )
                if resolver is None:
                    logger.debug(
                        "model_role %r set but no model_role_resolver capability"
                        " registered, falling back to the session's own provider",
                        self.config.model_role,
                    )
                else:
                    try:
                        resolved = await resolver.resolve(self.config.model_role)
                    except Exception as e:
                        logger.warning(
                            "model_role %r resolver raised %s; skipping session"
                            " naming for this turn rather than silently falling"
                            " back to the priority (expensive) provider — will"
                            " retry on a later turn",
                            self.config.model_role,
                            e,
                        )
                        return None
                    if resolved:
                        # ProviderPreference attrs: .provider, .model, .config
                        resolved_provider_name = resolved[0].provider
                        candidate_name, candidate = self._match_resolved_provider(
                            providers, resolved_provider_name
                        )
                        if candidate is None:
                            refusal = (
                                str(resolved_provider_name),
                                "no provider with that name is mounted in this session",
                            )
                        elif self._same_vendor(candidate, session_provider):
                            provider = candidate
                            provider_name = candidate_name
                            model_override = resolved[0].model
                        else:
                            refusal = (
                                str(candidate_name),
                                "it is a different provider vendor than the one"
                                " answering this session",
                            )
                    else:
                        role_had_no_candidates = True

            # Fall back to the session's OWN provider. Reached when model_role
            # is unset, no resolver capability is registered, the role resolved
            # to no candidates, or the resolved candidate was refused as
            # foreign — the last two are announced loudly below rather than
            # substituted silently.
            if provider is None:
                provider = session_provider
                provider_name = session_provider_name
                model_override = None

                if refusal is not None:
                    refused_name, reason = refusal
                    self._warn_once(
                        session_id,
                        self._cross_provider_refused,
                        "model_role %r resolved to provider %r, but %s."
                        " REFUSING to borrow it: session naming will run on"
                        " %r, the provider answering this session. (Naming"
                        " must never issue a call on a provider this session"
                        " never selected. Further occurrences this session"
                        " are logged at DEBUG.)",
                        self.config.model_role,
                        refused_name,
                        reason,
                        provider_name,
                    )
                elif role_had_no_candidates:
                    self._warn_once(
                        session_id,
                        self._role_fallback_warned,
                        "model_role %r resolved to no candidates; session"
                        " naming is falling back to provider %r (the"
                        " session's own conversation provider) instead of"
                        " skipping. This uses whatever model that provider is"
                        " already configured with, which may be more"
                        " expensive than intended — configure a %r"
                        " candidate in the routing matrix to route naming"
                        " to a cheap model instead. (Further occurrences"
                        " this session are logged at DEBUG.)",
                        self.config.model_role,
                        provider_name,
                        self.config.model_role,
                    )

            if not provider:
                logger.warning("No provider available for session naming")
                return None

            # Attribution: the provider emits llm:request / llm:response into
            # THIS session's event stream. Route those emits through a stamping
            # coordinator so every one of them carries purpose="session-naming"
            # and a scorer can exclude them from the root agent's own work.
            # If the events cannot be stamped, SKIP the call — naming is a
            # best-effort background chore, and an unattributable call is worse
            # than a missing session name.
            call_provider = self._stamped_provider(provider)
            if call_provider is None:
                self._warn_once(
                    session_id,
                    self._unstampable_warned,
                    "Session naming cannot stamp provider %r's events with"
                    " purpose=%r, so its llm:request/llm:response would be"
                    " indistinguishable from this session's own work."
                    " SKIPPING naming rather than emitting unattributable"
                    " events. (Further occurrences this session are logged at"
                    " DEBUG.)",
                    provider_name,
                    NAMING_PURPOSE,
                )
                return None

            # Make the request — model=None means use provider default.
            # metadata={"stream": False} signals to the provider that this is
            # a background utility call and must NOT take the streaming branch.
            # The streaming branch emits llm:stream_block_start/delta/end events
            # on the hook bus; without a session_id those events are indistinguishable
            # from foreground output and leak into the streaming-UI overlay.
            from amplifier_core import ChatRequest, Message

            # max_output_tokens: the naming response is a tiny JSON object
            # ({"action": "set"|"defer", "name": "...", "description": "..."}).
            # Without an explicit cap, the request inherits the provider's large
            # default, which trips Anthropic's "streaming is required for operations
            # that may take longer than 10 minutes" guard when stream=False.
            # 256 tokens is a generous budget for the expected output and well
            # below the threshold that triggers the guard.
            request = ChatRequest(
                messages=[Message(role="user", content=prompt)],
                model=model_override,
                metadata={"stream": False},
                max_output_tokens=256,
            )

            # extended_thinking=False: this is a mechanical classification chore,
            # not a reasoning task. Without this explicit opt-out, a provider with
            # a session-level reasoning effort configured (e.g. Anthropic
            # `effort: medium`) force-enables extended thinking on this call and
            # floors max_output_tokens up to the thinking budget (tens of
            # thousands), which — combined with stream=False above — trips
            # Anthropic's "streaming is required for operations that may take
            # longer than 10 minutes" guard and makes naming fail every retry.
            # Providers without a thinking concept ignore this kwarg.
            response = await call_provider.complete(request, extended_thinking=False)

            if response and response.content:
                # Extract text from content blocks
                text_parts = []
                for block in response.content:
                    if hasattr(block, "text"):
                        text_parts.append(block.text)
                    elif hasattr(block, "content") and isinstance(block.content, str):
                        text_parts.append(block.content)
                return "".join(text_parts) if text_parts else None

        except Exception as e:
            logger.error(f"Provider call failed: {e}")

        return None

    def _parse_response(self, response: str) -> dict | None:
        """Parse the JSON naming response from the LLM.

        The response is a small JSON object of the form
        ``{"action": ..., "name": ..., "description": ...}``. Because the
        provider call caps output tokens, a long ``description`` (the last,
        free-text field) can push the response past the cap and truncate it
        mid-string, leaving invalid JSON. Rather than discard the whole
        response, salvage the fields that completed: ``action`` and ``name``
        both precede ``description``, so they are intact whenever truncation
        lands inside the description. Naming then still succeeds and the
        description is refreshed on a later update pass.
        """
        try:
            # Try to extract JSON from response (may have markdown wrapper)
            json_match = re.search(r"\{[^{}]*\}", response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return json.loads(response)
        except json.JSONDecodeError as e:
            salvaged = self._salvage_partial(response)
            if salvaged:
                logger.debug(
                    "Naming response truncated; salvaged fields: %s",
                    ", ".join(salvaged),
                )
                return salvaged
            # Best-effort background chore: a genuinely unparseable response is
            # not worth a user-facing warning. Log quietly, like every other
            # non-fatal naming outcome in this module.
            logger.debug("Could not parse naming response: %s", e)
            logger.debug("Response was: %s", response[:200])
            return None

    @staticmethod
    def _salvage_partial(response: str) -> dict | None:
        """Recover completed fields from a truncated naming JSON response.

        Only ``action`` and ``name`` are recovered: they precede the free-text
        ``description`` that overflows the token cap, so they are complete
        whenever truncation happens inside ``description``. Returns None when
        nothing actionable can be read (e.g. truncation landed inside ``name``
        itself), letting the caller treat it as a clean miss and retry on a
        later turn.
        """
        # A complete JSON string value: opening quote, any escaped or
        # non-quote characters, closing quote.
        string_val = r'"((?:[^"\\]|\\.)*)"'
        name_match = re.search(r'"name"\s*:\s*' + string_val, response)
        action_match = re.search(r'"action"\s*:\s*' + string_val, response)
        action = action_match.group(1) if action_match else None

        if name_match:
            raw = name_match.group(1)
            try:
                # Round-trip through json to unescape \", \\, etc.
                name = json.loads(f'"{raw}"')
            except json.JSONDecodeError:
                name = raw
            # A recovered name is only meaningful for the "set" action.
            return {"action": action or "set", "name": name}

        # No name recovered, but a short action-only response (defer/keep)
        # may still have completed its action field.
        if action in ("defer", "keep"):
            return {"action": action}

        return None


async def mount(
    coordinator: Any, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Mount the session naming hook module.

    Config options:
        initial_trigger_turn: int (default: 2) - Turn to start naming
        update_interval_turns: int (default: 5) - Update description every N turns
        max_name_length: int (default: 50) - Maximum name length
        max_description_length: int (default: 200) - Maximum description length
        max_retries: int (default: 3) - Max retries on defer
        model_role: str | None (default: "fast") - Model role resolved via routing matrix.
            Defaults to "fast" so naming uses a cheap model automatically.
            Set to None to use the session's own conversation provider explicitly.
            A resolved candidate is honoured only when it is mounted in this
            session AND shares the vendor of the session's own provider;
            anything else is refused with a WARNING and naming runs on the
            session's own provider. Falls back to that provider (debug-logged)
            when no routing bundle is installed.
    """
    config = config or {}

    hook_config = SessionNamingConfig(
        initial_trigger_turn=config.get("initial_trigger_turn", 2),
        update_interval_turns=config.get("update_interval_turns", 5),
        max_name_length=config.get("max_name_length", 50),
        max_description_length=config.get("max_description_length", 200),
        max_retries=config.get("max_retries", 3),
        model_role=config.get("model_role", "fast"),
    )

    hook = SessionNamingHook(coordinator, hook_config)

    # Register for prompt completion events (fires after each turn)
    # Use low priority (high number) so we run after other hooks
    coordinator.hooks.register(
        "prompt:complete",
        hook.on_orchestrator_complete,
        priority=100,
        name="session-naming",
    )

    # Register for session end to drain any in-flight naming task
    from amplifier_core.events import SESSION_END

    coordinator.hooks.register(
        SESSION_END,
        hook.on_session_end,
        priority=100,
        name="session-naming-drain",
    )

    return {
        "name": "hooks-session-naming",
        "version": "0.2.0",
        "description": "Automatic session naming and description generation",
        "config": {
            "initial_trigger_turn": hook_config.initial_trigger_turn,
            "update_interval_turns": hook_config.update_interval_turns,
        },
    }
