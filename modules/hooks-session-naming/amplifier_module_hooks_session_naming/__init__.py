"""Session Naming Hook Module

Automatically generates human-readable session names and descriptions
using the configured LLM provider. Runs in background, never blocking
the main conversation.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from amplifier_core import HookResult

logger = logging.getLogger(__name__)


@dataclass
class SessionNamingConfig:
    """Configuration for session naming."""

    initial_trigger_turn: int = 2
    update_interval_turns: int = 5
    max_name_length: int = 50
    max_description_length: int = 200
    max_retries: int = 3


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

    async def on_orchestrator_complete(
        self, event: str, data: dict[str, Any]
    ) -> HookResult:
        """Handle orchestrator completion - trigger naming if appropriate.

        Naming runs synchronously (awaits the LLM call) to ensure it completes
        before the session ends. This adds a few seconds to turns where naming
        happens, but is more reliable than background tasks.
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
        turn_count = metadata.get("turn_count", 0)
        has_name = metadata.get("name") is not None

        # Initial naming: turn >= initial_trigger and no name yet
        if turn_count >= self.config.initial_trigger_turn and not has_name:
            defer_count = self._defer_counts.get(session_id, 0)
            if defer_count < self.config.max_retries:
                # Run naming synchronously to ensure completion
                await self._generate_name(session_id, session_dir, is_update=False)

        # Description update: has name and at update interval
        elif (
            has_name
            and turn_count > 0
            and turn_count % self.config.update_interval_turns == 0
        ):
            # Run description update synchronously
            await self._generate_name(session_id, session_dir, is_update=True)

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

            # Call the provider
            response = await self._call_provider(prompt)
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
                logger.debug(
                    f"Session {session_id[:8]} deferred naming (attempt {self._defer_counts[session_id]})"
                )
                return

            if action == "set":
                # Update metadata
                if not is_update and result.get("name"):
                    name = result["name"][: self.config.max_name_length]
                    metadata["name"] = name
                    metadata["name_generated_at"] = now
                    logger.info(f"Session {session_id[:8]} named: {name}")

                if result.get("description"):
                    description = result["description"][
                        : self.config.max_description_length
                    ]
                    metadata["description"] = description
                    metadata["description_updated_at"] = now
                    if is_update:
                        logger.debug(f"Session {session_id[:8]} description updated")

                self._save_metadata(session_dir, metadata)
                # Clear defer count on success
                self._defer_counts.pop(session_id, None)

            elif action == "keep":
                logger.debug(f"Session {session_id[:8]} description unchanged")

        except asyncio.CancelledError:
            logger.debug(f"Naming task cancelled for session {session_id[:8]}")
        except Exception as e:
            logger.error(f"Error generating name for session {session_id[:8]}: {e}")

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

    async def _call_provider(self, prompt: str) -> str | None:
        """Call the LLM provider to generate name/description."""
        try:
            # Get the priority provider from coordinator using standard API
            provider = None
            providers = self.coordinator.get("providers")
            if providers:
                # Get first/priority provider
                provider = next(iter(providers.values()), None)

            if not provider:
                logger.warning("No provider available for session naming")
                return None

            # Make the request
            from amplifier_core import ChatRequest, Message

            request = ChatRequest(messages=[Message(role="user", content=prompt)])

            response = await provider.complete(request)

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
        """Parse JSON response from LLM."""
        try:
            # Try to extract JSON from response (may have markdown wrapper)
            json_match = re.search(r"\{[^{}]*\}", response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return json.loads(response)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse naming response: {e}")
            logger.debug(f"Response was: {response[:200]}")
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
    """
    config = config or {}

    hook_config = SessionNamingConfig(
        initial_trigger_turn=config.get("initial_trigger_turn", 2),
        update_interval_turns=config.get("update_interval_turns", 5),
        max_name_length=config.get("max_name_length", 50),
        max_description_length=config.get("max_description_length", 200),
        max_retries=config.get("max_retries", 3),
    )

    hook = SessionNamingHook(coordinator, hook_config)

    # Register for orchestrator completion events (fires after each turn)
    # Use low priority (high number) so we run after other hooks
    coordinator.hooks.register(
        "orchestrator:complete",
        hook.on_orchestrator_complete,
        priority=100,
        name="session-naming",
    )

    return {
        "name": "hooks-session-naming",
        "version": "0.1.0",
        "description": "Automatic session naming and description generation",
        "config": {
            "initial_trigger_turn": hook_config.initial_trigger_turn,
            "update_interval_turns": hook_config.update_interval_turns,
        },
    }
