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
- settings.exclude_tools: Tools spawned agents should NOT inherit (default: ["delegate"])
- settings.exclude_hooks: Hooks spawned agents should NOT inherit (default: [])
- settings.timeout: Maximum total execution time for child session in seconds (default: None/disabled)

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
import logging
import uuid
from typing import Any

from amplifier_core import ModuleCoordinator, ToolResult
from amplifier_foundation import ProviderPreference

logger = logging.getLogger(__name__)


async def mount(coordinator: ModuleCoordinator, config: dict[str, Any] | None = None):
    """Mount the agent delegation tool.

    Args:
        coordinator: The module coordinator
        config: Optional configuration with features and settings

    Returns:
        None - No cleanup needed for this module
    """
    config = config or {}

    # Declare observable lifecycle events for this module
    obs_events = coordinator.get_capability("observability.events") or []
    obs_events.extend(
        [
            "delegate:agent_spawned",  # When agent sub-session spawned
            "delegate:agent_resumed",  # When agent sub-session resumed
            "delegate:agent_completed",  # When agent sub-session completed
            "delegate:error",  # When delegation fails
        ]
    )
    coordinator.register_capability("observability.events", obs_events)

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

        # Settings
        self.exclude_tools: list[str] = settings.get("exclude_tools", ["delegate"])
        self.exclude_hooks: list[str] = settings.get("exclude_hooks", [])
        self.timeout: int | None = settings.get("timeout", None)

        # Build feature registry for dynamic description composition
        self._feature_registry = self._build_feature_registry()

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
        ]

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

CRITICAL: Delegation is your PRIMARY operating mode, not an optimization.

ALWAYS use this tool when:
- Task requires reading more than 2 files
- Task requires exploration or investigation
- Task matches any agent's specialty (check Available agents below)
- Task would benefit from specialized context or tools
- You're about to use grep, glob, or read_file more than twice
- User asks you to "look into", "investigate", "explore", or "analyze" something

NEVER do these yourself - ALWAYS delegate:
- Codebase exploration → foundation:explorer
- Git commits/PRs → foundation:git-ops  
- Session/conversation analysis → foundation:session-analyst
- Debugging errors → foundation:bug-hunter
- Architecture decisions → foundation:zen-architect
- Implementation work → foundation:modular-builder

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
- Each agent invocation is stateless - provide complete context in your instruction
- DEFAULT TO DELEGATION - only do simple single-step work yourself"""

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
        """
        return {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Agent to delegate to (e.g., 'foundation:explorer', 'self', or bundle path)",
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
            return await self._resume_existing_session(session_id, instruction, hooks)

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

        # Get parent session ID
        parent_session_id = self.coordinator.session_id

        # Generate hierarchical sub-session ID
        child_span = uuid.uuid4().hex[:16]
        sub_session_id = f"{parent_session_id}-{child_span}_{agent_name}"

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

            # Emit delegate:agent_spawned event
            if hooks:
                await hooks.emit(
                    "delegate:agent_spawned",
                    {
                        "agent": agent_name,
                        "sub_session_id": sub_session_id,
                        "parent_session_id": parent_session_id,
                        "context_depth": context_depth,
                        "context_scope": context_scope,
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

            # Extract orchestrator config from parent session for inheritance
            orchestrator_config = None
            parent_config = parent_session.config or {}
            session_config = parent_config.get("session", {})
            orch_section = session_config.get("orchestrator", {})
            if orch_config := orch_section.get("config"):
                orchestrator_config = orch_config
                logger.debug(f"Inheriting orchestrator config: {orchestrator_config}")

            # Calculate self-delegation depth for child session
            # Named agents reset to 0, self-delegation increments
            if agent_name == "self":
                current_depth = (
                    self.coordinator.get_capability("self_delegation_depth") or 0
                )
                child_self_delegation_depth = current_depth + 1
            else:
                child_self_delegation_depth = 0  # Named agents start fresh chain

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
                orchestrator_config=orchestrator_config,
                provider_preferences=provider_preferences,
                self_delegation_depth=child_self_delegation_depth,
            )
            if self.timeout is not None:
                async with asyncio.timeout(self.timeout):
                    result = await spawn_coro
            else:
                result = await spawn_coro

            # Emit delegate:agent_completed event
            if hooks:
                await hooks.emit(
                    "delegate:agent_completed",
                    {
                        "agent": agent_name,
                        "sub_session_id": sub_session_id,
                        "parent_session_id": parent_session_id,
                        "success": True,
                    },
                )

            # Return output with session_id for multi-turn capability
            session_id_result = result["session_id"]
            return ToolResult(
                success=True,
                output={
                    "response": result["output"],
                    "session_id": session_id_result,
                    "agent": agent_name,
                    "turn_count": result.get("turn_count", 1),
                    "status": result.get("status", "success"),
                    "metadata": result.get("metadata", {}),
                },
            )

        except TimeoutError:
            # asyncio.timeout raises TimeoutError (which may propagate as
            # CancelledError internally).  Surface the source clearly so the
            # caller knows this was a delegation-level wall-clock timeout,
            # not a provider or network issue.
            timeout_msg = (
                f"Agent '{agent_name}' timed out after {self.timeout}s "
                f"(delegate tool session-level timeout). "
                f"Increase or disable the timeout in tool-delegate settings "
                f"(settings.timeout) to allow longer-running agents."
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
                    },
                )
            return ToolResult(success=False, error={"message": timeout_msg})

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
                    },
                )

            return ToolResult(success=False, error={"message": error_msg})

    async def _resume_existing_session(
        self, session_id: str, instruction: str, hooks
    ) -> ToolResult:
        """Resume existing agent session.

        Args:
            session_id: Full agent session ID to resume (from previous delegate call)
            instruction: Follow-up instruction
            hooks: Hook coordinator for event emission

        Returns:
            ToolResult with success status and output or error
        """
        parent_session_id = self.coordinator.session_id

        try:
            # Use session_id as-is (no short ID resolution - LLMs can handle full IDs)
            full_session_id = session_id

            # Emit delegate:agent_resumed event
            if hooks:
                await hooks.emit(
                    "delegate:agent_resumed",
                    {
                        "session_id": full_session_id,
                        "parent_session_id": parent_session_id,
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

            # Resume agent session (with optional session-level timeout)
            resume_coro = resume_fn(
                sub_session_id=full_session_id,
                instruction=instruction,
            )
            if self.timeout is not None:
                async with asyncio.timeout(self.timeout):
                    result = await resume_coro
            else:
                result = await resume_coro

            # Emit delegate:agent_completed event
            if hooks:
                await hooks.emit(
                    "delegate:agent_completed",
                    {
                        "sub_session_id": full_session_id,
                        "parent_session_id": parent_session_id,
                        "success": True,
                    },
                )

            # Extract agent name from session ID if possible
            agent_name = "unknown"
            if "_" in full_session_id:
                agent_name = full_session_id.split("_")[-1]

            # Return output with session info
            session_id_result = result["session_id"]
            return ToolResult(
                success=True,
                output={
                    "response": result["output"],
                    "session_id": session_id_result,
                    "agent": agent_name,
                    "turn_count": result.get("turn_count", 1),
                    "status": result.get("status", "success"),
                    "metadata": result.get("metadata", {}),
                },
            )

        except ValueError as e:
            # Session ID resolution error
            if hooks:
                await hooks.emit(
                    "delegate:error",
                    {
                        "session_id": session_id,
                        "parent_session_id": parent_session_id,
                        "error": str(e),
                    },
                )
            return ToolResult(success=False, error={"message": str(e)})

        except FileNotFoundError as e:
            # Session not found
            if hooks:
                await hooks.emit(
                    "delegate:error",
                    {
                        "session_id": session_id,
                        "parent_session_id": parent_session_id,
                        "error": f"Session not found: {str(e)}",
                    },
                )
            return ToolResult(
                success=False,
                error={
                    "message": f"Agent session '{session_id}' not found. May have expired or never existed."
                },
            )

        except TimeoutError:
            # Extract agent name for the message
            resume_agent = "unknown"
            if "_" in session_id:
                resume_agent = session_id.split("_")[-1]
            timeout_msg = (
                f"Resumed agent '{resume_agent}' timed out after {self.timeout}s "
                f"(delegate tool session-level timeout). "
                f"Increase or disable the timeout in tool-delegate settings "
                f"(settings.timeout) to allow longer-running agents."
            )
            logger.warning(timeout_msg)
            if hooks:
                await hooks.emit(
                    "delegate:error",
                    {
                        "session_id": session_id,
                        "parent_session_id": parent_session_id,
                        "error": timeout_msg,
                    },
                )
            return ToolResult(success=False, error={"message": timeout_msg})

        except Exception as e:
            # Other errors — include exception type for clear source attribution
            error_type = type(e).__name__
            error_detail = str(e) or "(no detail)"
            error_msg = f"Agent resume failed ({error_type}): {error_detail}"
            if hooks:
                await hooks.emit(
                    "delegate:error",
                    {
                        "session_id": session_id,
                        "parent_session_id": parent_session_id,
                        "error": error_msg,
                    },
                )
            return ToolResult(success=False, error={"message": error_msg})
