"""
Unified session spawning primitive.

This module provides the core spawn_bundle() function that all session
spawning in Amplifier should use. It handles:
- Bundle resolution (URI, Bundle, or PreparedBundle)
- Configuration inheritance (providers, tools, hooks)
- Infrastructure wiring (resolvers, sys.path, cancellation)
- Session persistence (optional)
- Background execution (optional)
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from amplifier_core import AmplifierSession

    from amplifier_foundation.bundle import Bundle, PreparedBundle

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class SpawnResult:
    """Result of spawning a bundle."""

    output: str
    """Final response from the spawned session."""

    session_id: str
    """Session ID for potential resumption."""

    turn_count: int
    """Number of turns executed."""


# =============================================================================
# Protocols
# =============================================================================


class SessionStorage(Protocol):
    """
    Protocol for session persistence.

    Implementations handle storing and retrieving session state for
    resumption. The CLI's SessionStore is the reference implementation.

    Note: Methods are sync to match existing SessionStore. If async
    storage is needed, wrap in run_in_executor.
    """

    def save(
        self,
        session_id: str,
        transcript: list[dict],
        metadata: dict,
    ) -> None:
        """Persist session state."""
        ...

    def load(
        self,
        session_id: str,
    ) -> tuple[list[dict], dict]:
        """Load session state. Returns (transcript, metadata)."""
        ...

    def exists(self, session_id: str) -> bool:
        """Check if session exists in storage."""
        ...


# =============================================================================
# Helper Functions
# =============================================================================


def _merge_module_lists(
    base: list[dict],
    overlay: list[dict],
) -> list[dict]:
    """
    Merge two module config lists. Overlay wins on conflict.

    Args:
        base: Base list (e.g., parent's tools)
        overlay: Overlay list (e.g., bundle's tools)

    Returns:
        Merged list with overlay taking precedence for same module names.
    """
    result = list(base)
    overlay_modules = {m.get("module") for m in overlay}

    # Remove base modules that overlay overrides
    result = [m for m in result if m.get("module") not in overlay_modules]

    # Add all overlay modules
    result.extend(overlay)

    return result


def _filter_modules(
    modules: list[dict],
    inherit: bool | list[str],
) -> list[dict]:
    """
    Filter module list based on inheritance policy.

    Args:
        modules: List of module configs
        inherit:
            - False: Return empty list
            - True: Return all modules
            - list[str]: Return only modules with names in list

    Returns:
        Filtered module list.
    """
    if inherit is False:
        return []
    if inherit is True:
        return list(modules)
    # inherit is a list of module names
    return [m for m in modules if m.get("module") in inherit]


def _share_sys_paths(
    parent_session: AmplifierSession,
) -> list[str]:
    """
    Get sys.path entries that should be shared with child sessions.

    Collects paths from:
    1. Parent loader's _added_paths
    2. bundle_package_paths capability

    Returns:
        List of paths to add to sys.path.
    """
    paths_to_share: list[str] = []

    # Source 1: Module paths from parent loader
    if hasattr(parent_session, "loader") and parent_session.loader is not None:
        parent_added_paths = getattr(parent_session.loader, "_added_paths", [])
        paths_to_share.extend(parent_added_paths)

    # Source 2: Bundle package paths capability
    bundle_package_paths = parent_session.coordinator.get_capability(
        "bundle_package_paths"
    )
    if bundle_package_paths:
        paths_to_share.extend(bundle_package_paths)

    return paths_to_share


def _extract_recent_turns(
    messages: list[dict],
    n: int,
) -> list[dict]:
    """
    Extract the last N user→assistant turns from messages.

    Args:
        messages: Full message list
        n: Number of turns to extract

    Returns:
        Last N turns worth of messages.
    """
    # Find indices where user messages start (turn boundaries)
    turn_starts = [i for i, m in enumerate(messages) if m.get("role") == "user"]

    if len(turn_starts) <= n:
        return messages

    start_index = turn_starts[-n]
    return messages[start_index:]


async def _build_context_messages(
    parent_session: AmplifierSession,
    context_depth: str,
    context_scope: str,
    context_turns: int,
) -> list[dict]:
    """
    Build context messages to inherit from parent.

    Args:
        parent_session: Parent session to get context from
        context_depth: "none" | "recent" | "all"
        context_scope: "conversation" | "agents" | "full"
        context_turns: Number of turns for "recent" depth

    Returns:
        List of messages to inject into child context.
    """
    if context_depth == "none":
        return []

    context = parent_session.coordinator.get("context")
    if not context or not hasattr(context, "get_messages"):
        return []

    # Get messages (handle both sync and async)
    if asyncio.iscoroutinefunction(context.get_messages):
        messages = await context.get_messages()
    else:
        messages = context.get_messages()

    # Filter by scope
    if context_scope == "conversation":
        # Only user/assistant messages
        messages = [m for m in messages if m.get("role") in ("user", "assistant")]
    elif context_scope == "agents":
        # Include delegate tool results
        messages = [
            m
            for m in messages
            if m.get("role") in ("user", "assistant")
            or (m.get("role") == "tool" and "delegate" in str(m.get("name", "")))
        ]
    # "full" = all messages

    # Apply depth
    if context_depth == "recent":
        messages = _extract_recent_turns(messages, context_turns)

    return messages


# =============================================================================
# Main Function
# =============================================================================


async def spawn_bundle(
    # === What to spawn ===
    bundle: PreparedBundle | Bundle | str,
    instruction: str,
    parent_session: AmplifierSession,
    # === Inheritance controls ===
    inherit_providers: bool = True,
    inherit_tools: bool | list[str] = False,
    inherit_hooks: bool | list[str] = False,
    # === Context inheritance (matches tool-delegate) ===
    context_depth: str = "none",  # "none" | "recent" | "all"
    context_scope: str = "conversation",  # "conversation" | "agents" | "full"
    context_turns: int = 5,
    # === Session identity ===
    session_id: str | None = None,
    session_name: str | None = None,
    # === Persistence ===
    session_storage: SessionStorage | None = None,
    # === Execution control ===
    timeout: float | None = None,
    background: bool = False,
    # === Event routing (for background completion) ===
    event_router: Any | None = None,
    # === Additional setup ===
    system_instruction: str | None = None,
    additional_capabilities: dict[str, Any] | None = None,
    pre_execute_hook: Any
    | None = None,  # Callable[[AmplifierSession], Awaitable[None]]
    metadata_extra: dict[str, Any] | None = None,  # Extra metadata for persistence
) -> SpawnResult:
    """
    Spawn a bundle as a sub-session.

    This is THE primitive for all session spawning in Amplifier. All other
    spawning patterns (agents, self-delegation, workers) should use this
    function or build on it.

    Args:
        bundle: What to spawn - PreparedBundle, Bundle, or URI string
        instruction: The prompt/instruction to execute
        parent_session: Parent for inheritance and lineage
        inherit_providers: Copy parent's providers if bundle has none
        inherit_tools: Which parent tools to include (False/True/list)
        inherit_hooks: Which parent hooks to include (False/True/list)
        context_depth: How much parent context ("none"/"recent"/"all")
        context_scope: Which content ("conversation"/"agents"/"full")
        context_turns: Number of turns for "recent" depth
        session_id: Explicit session ID (generated if None)
        session_name: Human-readable name for ID generation
        session_storage: Persistence implementation (optional)
        timeout: Maximum execution time in seconds
        background: If True, return immediately, session runs in background
        event_router: EventRouter for background completion events
        system_instruction: System instruction to inject before execution
        additional_capabilities: Dict of capabilities to register on child session
        pre_execute_hook: Async callback(session) called after init, before execution
        metadata_extra: Extra metadata to merge into persistence metadata

    Returns:
        SpawnResult with output, session_id, and turn_count

    Raises:
        BundleLoadError: If bundle URI cannot be loaded
        BundleValidationError: If bundle is invalid
        TimeoutError: If execution exceeds timeout
    """
    from amplifier_core import AmplifierSession

    from amplifier_foundation import generate_sub_session_id, load_bundle
    from amplifier_foundation.bundle import Bundle

    # =========================================================================
    # PHASE 1: Bundle Resolution
    # =========================================================================

    if isinstance(bundle, str):
        # Load bundle from URI
        loaded_bundle = await load_bundle(bundle)
        prepared = await loaded_bundle.prepare()
    elif isinstance(bundle, Bundle):
        prepared = await bundle.prepare()
    else:
        # Already a PreparedBundle
        prepared = bundle

    bundle_name = prepared.bundle.name or "unnamed"

    # =========================================================================
    # PHASE 2: Configuration Inheritance
    # =========================================================================

    config = dict(prepared.mount_plan)

    # --- Provider inheritance ---
    if inherit_providers and not config.get("providers"):
        parent_providers = parent_session.config.get("providers", [])
        if parent_providers:
            config["providers"] = list(parent_providers)
            logger.debug(f"Inherited {len(parent_providers)} providers from parent")

    # --- Tool inheritance ---
    if inherit_tools:
        parent_tools = parent_session.config.get("tools", [])
        bundle_tools = config.get("tools", [])
        filtered_parent = _filter_modules(parent_tools, inherit_tools)
        config["tools"] = _merge_module_lists(filtered_parent, bundle_tools)
        logger.debug(f"Inherited {len(filtered_parent)} tools from parent")

    # --- Hook inheritance ---
    if inherit_hooks:
        parent_hooks = parent_session.config.get("hooks", [])
        bundle_hooks = config.get("hooks", [])
        filtered_parent = _filter_modules(parent_hooks, inherit_hooks)
        config["hooks"] = _merge_module_lists(filtered_parent, bundle_hooks)
        logger.debug(f"Inherited {len(filtered_parent)} hooks from parent")

    # =========================================================================
    # PHASE 3: Session Identity
    # =========================================================================

    if not session_id:
        session_id = generate_sub_session_id(
            agent_name=session_name or bundle_name,
            parent_session_id=parent_session.session_id,
            parent_trace_id=getattr(parent_session, "trace_id", None),
        )

    # session_id is guaranteed to be set at this point
    assert session_id is not None

    # =========================================================================
    # PHASE 4: Session Creation
    # =========================================================================

    approval_system = parent_session.coordinator.approval_system
    display_system = parent_session.coordinator.display_system

    child_session = AmplifierSession(
        config=config,
        loader=None,  # Each session gets its own loader
        session_id=session_id,
        parent_id=parent_session.session_id,
        approval_system=approval_system,
        display_system=display_system,
    )

    # =========================================================================
    # PHASE 5: Infrastructure Wiring (BEFORE initialize)
    # =========================================================================

    # --- Module resolver ---
    parent_resolver = parent_session.coordinator.get("module-source-resolver")
    if parent_resolver:
        await child_session.coordinator.mount("module-source-resolver", parent_resolver)
    elif hasattr(prepared, "resolver") and prepared.resolver:
        await child_session.coordinator.mount(
            "module-source-resolver", prepared.resolver
        )

    # --- sys.path sharing ---
    paths_to_share = _share_sys_paths(parent_session)
    for path in paths_to_share:
        if path not in sys.path:
            sys.path.insert(0, path)
    if paths_to_share:
        logger.debug(f"Shared {len(paths_to_share)} sys.path entries")

    # =========================================================================
    # PHASE 6: Initialization
    # =========================================================================

    await child_session.initialize()

    # =========================================================================
    # PHASE 7: Post-Initialize Wiring
    # =========================================================================

    # --- Cancellation propagation (skip for background) ---
    parent_cancellation = None
    child_cancellation = None
    if not background:
        parent_cancellation = parent_session.coordinator.cancellation
        child_cancellation = child_session.coordinator.cancellation
        if parent_cancellation and child_cancellation:
            parent_cancellation.register_child(child_cancellation)
            logger.debug(f"Registered child cancellation for {session_id}")

    # --- Mention resolver inheritance ---
    parent_mention_resolver = parent_session.coordinator.get_capability(
        "mention_resolver"
    )
    if parent_mention_resolver:
        child_session.coordinator.register_capability(
            "mention_resolver", parent_mention_resolver
        )

    # --- Mention deduplicator inheritance ---
    parent_deduplicator = parent_session.coordinator.get_capability(
        "mention_deduplicator"
    )
    if parent_deduplicator:
        child_session.coordinator.register_capability(
            "mention_deduplicator", parent_deduplicator
        )

    # --- Working directory inheritance ---
    parent_working_dir = parent_session.coordinator.get_capability(
        "session.working_dir"
    )
    if parent_working_dir:
        child_session.coordinator.register_capability(
            "session.working_dir", parent_working_dir
        )

    # --- Nested spawning capability ---
    async def child_spawn_capability(**kwargs: Any) -> dict:
        result = await spawn_bundle(
            parent_session=child_session,
            session_storage=session_storage,
            event_router=event_router,
            **kwargs,
        )
        return {"output": result.output, "session_id": result.session_id}

    child_session.coordinator.register_capability(
        "session.spawn", child_spawn_capability
    )

    # --- Event emitter capability ---
    if event_router:
        from amplifier_foundation.events import EventRouter

        if isinstance(event_router, EventRouter):
            session_emitter = event_router.create_session_emitter(session_id)
            child_session.coordinator.register_capability(
                "event.emit", session_emitter.emit
            )
            logger.debug(f"Registered event.emit capability for session {session_id}")

    # --- Additional capabilities from caller ---
    if additional_capabilities:
        for name, capability in additional_capabilities.items():
            child_session.coordinator.register_capability(name, capability)
        logger.debug(
            f"Registered {len(additional_capabilities)} additional capabilities"
        )

    # =========================================================================
    # PHASE 8: System Instruction & Context Inheritance
    # =========================================================================

    # --- System instruction injection ---
    if system_instruction:
        child_context = child_session.coordinator.get("context")
        if child_context and hasattr(child_context, "add_message"):
            await child_context.add_message(
                {"role": "system", "content": system_instruction}
            )
            logger.debug("Injected system instruction")

    if context_depth != "none":
        parent_messages = await _build_context_messages(
            parent_session, context_depth, context_scope, context_turns
        )
        if parent_messages:
            child_context = child_session.coordinator.get("context")
            if child_context and hasattr(child_context, "add_message"):
                for msg in parent_messages:
                    await child_context.add_message(msg)
                logger.debug(f"Inherited {len(parent_messages)} context messages")

    # =========================================================================
    # PHASE 9: Pre-Execute Hook
    # =========================================================================

    if pre_execute_hook:
        await pre_execute_hook(child_session)
        logger.debug("Executed pre_execute_hook")

    # =========================================================================
    # PHASE 10: Execution
    # =========================================================================

    if background:
        # Fire-and-forget: spawn as asyncio task
        asyncio.create_task(
            _execute_background_session(
                child_session,
                instruction,
                session_id,
                bundle_name,
                session_storage,
                event_router,
            )
        )
        # Return immediately
        return SpawnResult(
            output="[Background session started]",
            session_id=session_id,
            turn_count=0,
        )

    # Foreground execution
    try:
        if timeout:
            response = await asyncio.wait_for(
                child_session.execute(instruction),
                timeout=timeout,
            )
        else:
            response = await child_session.execute(instruction)
    finally:
        # Unregister cancellation BEFORE cleanup
        if parent_cancellation and child_cancellation:
            parent_cancellation.unregister_child(child_cancellation)
            logger.debug(f"Unregistered child cancellation for {session_id}")

    # =========================================================================
    # PHASE 11: Persistence
    # =========================================================================

    if session_storage:
        context = child_session.coordinator.get("context")
        transcript: list[dict] = []
        if context and hasattr(context, "get_messages"):
            if asyncio.iscoroutinefunction(context.get_messages):
                transcript = await context.get_messages()
            else:
                transcript = context.get_messages()

        metadata = {
            "session_id": session_id,
            "parent_id": parent_session.session_id,
            "bundle_name": bundle_name,
            "created": datetime.now(UTC).isoformat(),
            "turn_count": 1,
        }

        # Merge any extra metadata from caller
        if metadata_extra:
            metadata.update(metadata_extra)

        session_storage.save(session_id, transcript, metadata)
        logger.debug(f"Persisted session {session_id}")

    # =========================================================================
    # PHASE 12: Cleanup & Return
    # =========================================================================

    await child_session.cleanup()

    return SpawnResult(
        output=response,
        session_id=session_id,
        turn_count=1,
    )


async def _execute_background_session(
    session: "AmplifierSession",
    instruction: str,
    session_id: str,
    bundle_name: str,
    session_storage: SessionStorage | None,
    event_router: Any | None,
) -> None:
    """
    Execute a background session and emit completion event.

    This runs as an asyncio task for fire-and-forget spawning.
    """
    try:
        response = await session.execute(instruction)

        # Persist if storage provided
        if session_storage:
            context = session.coordinator.get("context")
            transcript: list[dict] = []
            if context and hasattr(context, "get_messages"):
                if asyncio.iscoroutinefunction(context.get_messages):
                    transcript = await context.get_messages()
                else:
                    transcript = context.get_messages()

            metadata = {
                "session_id": session_id,
                "bundle_name": bundle_name,
                "created": datetime.now(UTC).isoformat(),
                "turn_count": 1,
            }
            session_storage.save(session_id, transcript, metadata)

        # Emit completion event
        if event_router:
            await event_router.emit(
                "session:completed",
                {
                    "session_id": session_id,
                    "bundle_name": bundle_name,
                    "output": response,
                    "success": True,
                },
            )

    except Exception as e:
        logger.error(f"Background session {session_id} failed: {e}")

        # Emit error event
        if event_router:
            await event_router.emit(
                "session:error",
                {
                    "session_id": session_id,
                    "bundle_name": bundle_name,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            )
    finally:
        await session.cleanup()
