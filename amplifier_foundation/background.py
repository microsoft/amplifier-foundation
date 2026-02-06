"""
Background session management.

Provides infrastructure for long-running sessions that respond to triggers.
This enables event-driven orchestration patterns where sessions can be
spawned in response to file changes, timers, or events from other sessions.

Example:
    manager = BackgroundSessionManager(parent_session, event_router)

    # Start a background session that responds to file changes
    session_id = await manager.start(BackgroundSessionConfig(
        name="code-watcher",
        bundle="observers:code-quality",
        triggers=[
            {"type": "timer", "config": {"interval_seconds": 300}}
        ],
    ))

    # Check status
    status = manager.get_status()

    # Stop when done
    await manager.stop(session_id)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Callable

from amplifier_foundation.events import EventRouter
from amplifier_foundation.triggers import (
    ManualTrigger,
    SessionEventTrigger,
    TimerTrigger,
    TriggerEvent,
    TriggerSource,
    TriggerType,
)

if TYPE_CHECKING:
    from amplifier_core import AmplifierSession

    from amplifier_foundation.spawn import SessionStorage

logger = logging.getLogger(__name__)


@dataclass
class BackgroundSessionConfig:
    """Configuration for a background session.

    Attributes:
        name: Human-readable name for the session
        bundle: Bundle URI to spawn when triggered
        triggers: List of trigger configurations
        instruction_template: Template for building instructions from events
        pool_size: Number of concurrent instances allowed
        on_complete_emit: Custom event to emit on completion
        on_error_emit: Custom event to emit on error
        restart_on_failure: Whether to restart the trigger loop on failure
        max_restarts: Maximum restart attempts before giving up
    """

    name: str
    """Human-readable name for the session."""

    bundle: str
    """Bundle URI to spawn when triggered."""

    triggers: list[dict[str, Any]] = field(default_factory=list)
    """List of trigger configurations."""

    instruction_template: str = "Handle this event: {event_summary}"
    """Template for building instructions from trigger events."""

    pool_size: int = 1
    """Number of concurrent instances allowed (for worker pools)."""

    on_complete_emit: str | None = None
    """Optional custom event to emit on completion."""

    on_error_emit: str | None = None
    """Optional custom event to emit on error."""

    restart_on_failure: bool = True
    """Whether to restart the trigger loop on failure."""

    max_restarts: int = 3
    """Maximum restart attempts before giving up."""


@dataclass
class BackgroundSessionState:
    """Runtime state for a background session.

    Tracks the current status, trigger history, and task reference
    for a running background session.
    """

    config: BackgroundSessionConfig
    """Configuration for this session."""

    task: asyncio.Task[None] | None = None
    """The asyncio task running the trigger loop."""

    trigger_count: int = 0
    """Number of times this session has been triggered."""

    spawn_count: int = 0
    """Number of sessions spawned (may differ from trigger_count due to pool limits)."""

    last_trigger_time: datetime | None = None
    """When the last trigger event occurred."""

    restart_count: int = 0
    """Number of times the trigger loop has been restarted."""

    status: str = "stopped"
    """Current status: 'stopped', 'starting', 'running', 'stopping', 'failed'."""

    error: str | None = None
    """Error message if status is 'failed'."""

    active_spawns: int = 0
    """Number of currently running spawned sessions."""


class BackgroundSessionManager:
    """
    Manages background sessions for an orchestrator.

    Handles:
    - Starting/stopping background sessions
    - Trigger source management (timer, session events, manual)
    - Pool size enforcement (limiting concurrent spawns)
    - Restart policies on failure
    - Status reporting

    The manager connects trigger sources to session spawning:
    1. Trigger sources watch for events (timer ticks, session completions, etc.)
    2. When triggered, the manager spawns a session using spawn_bundle()
    3. Results are emitted as events via EventRouter

    Example:
        manager = BackgroundSessionManager(parent_session, event_router)

        session_id = await manager.start(BackgroundSessionConfig(
            name="periodic-check",
            bundle="tools:health-check",
            triggers=[{"type": "timer", "config": {"interval_seconds": 60}}],
        ))

        # Later...
        status = manager.get_status()
        await manager.stop(session_id)
    """

    def __init__(
        self,
        parent_session: "AmplifierSession",
        event_router: EventRouter,
        session_storage: "SessionStorage | None" = None,
        trigger_loader: Callable[[dict[str, Any], EventRouter], TriggerSource]
        | None = None,
    ) -> None:
        """Initialize the background session manager.

        Args:
            parent_session: Parent session for spawning children
            event_router: EventRouter for cross-session communication
            session_storage: Optional storage for session persistence
            trigger_loader: Optional custom trigger loader function
        """
        self.parent_session = parent_session
        self.event_router = event_router
        self.session_storage = session_storage
        self._trigger_loader = trigger_loader or self._default_trigger_loader

        self._sessions: dict[str, BackgroundSessionState] = {}
        self._next_id = 1
        self._lock = asyncio.Lock()

    def _default_trigger_loader(
        self, config: dict[str, Any], event_router: EventRouter
    ) -> TriggerSource:
        """Load a trigger from configuration.

        Args:
            config: Trigger configuration with 'type' and 'config' keys
            event_router: EventRouter for session event triggers

        Returns:
            Configured TriggerSource instance

        Raises:
            ValueError: If trigger type is unknown
        """
        trigger_type = config.get("type", "manual")

        if trigger_type == "timer":
            trigger = TimerTrigger()
        elif trigger_type == "session_event":
            trigger = SessionEventTrigger(event_router)
        elif trigger_type == "manual":
            trigger = ManualTrigger()
        else:
            raise ValueError(f"Unknown trigger type: {trigger_type}")

        trigger.configure(config.get("config", {}))
        return trigger

    async def start(self, config: BackgroundSessionConfig) -> str:
        """Start a background session.

        Args:
            config: Configuration for the background session

        Returns:
            Session ID that can be used to stop or query the session
        """
        async with self._lock:
            session_id = f"bg-{config.name}-{self._next_id:04d}"
            self._next_id += 1

            state = BackgroundSessionState(config=config, status="starting")
            self._sessions[session_id] = state

            # Start the background task
            state.task = asyncio.create_task(
                self._run_background_session(session_id, state),
                name=f"background-{config.name}",
            )

            state.task.add_done_callback(
                lambda t: self._on_task_complete(session_id, t)
            )

            logger.info(f"Started background session: {session_id}")
            return session_id

    async def stop(self, session_id: str) -> bool:
        """Stop a background session.

        Args:
            session_id: ID of the session to stop

        Returns:
            True if session was stopped, False if not found
        """
        async with self._lock:
            state = self._sessions.get(session_id)
            if not state:
                return False

            state.status = "stopping"

            if state.task and not state.task.done():
                state.task.cancel()
                try:
                    await asyncio.wait_for(state.task, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

            state.status = "stopped"
            state.task = None
            logger.info(f"Stopped background session: {session_id}")
            return True

    async def stop_all(self) -> None:
        """Stop all background sessions."""
        session_ids = list(self._sessions.keys())
        for session_id in session_ids:
            await self.stop(session_id)

    def get_status(self, session_id: str | None = None) -> dict[str, Any]:
        """Get status of background sessions.

        Args:
            session_id: Optional specific session to query

        Returns:
            Status dict for one session or all sessions
        """
        if session_id:
            state = self._sessions.get(session_id)
            if not state:
                return {"error": f"Session not found: {session_id}"}
            return self._state_to_dict(session_id, state)

        return {
            "sessions": {
                sid: self._state_to_dict(sid, state)
                for sid, state in self._sessions.items()
            },
            "total": len(self._sessions),
            "running": sum(1 for s in self._sessions.values() if s.status == "running"),
        }

    def _state_to_dict(self, session_id: str, state: BackgroundSessionState) -> dict:
        """Convert state to status dict."""
        return {
            "session_id": session_id,
            "name": state.config.name,
            "bundle": state.config.bundle,
            "status": state.status,
            "trigger_count": state.trigger_count,
            "spawn_count": state.spawn_count,
            "active_spawns": state.active_spawns,
            "last_trigger_time": (
                state.last_trigger_time.isoformat() if state.last_trigger_time else None
            ),
            "restart_count": state.restart_count,
            "error": state.error,
        }

    async def _run_background_session(
        self,
        session_id: str,
        state: BackgroundSessionState,
    ) -> None:
        """Run a background session, responding to triggers."""
        config = state.config
        state.status = "running"

        try:
            # Load trigger sources
            triggers: list[TriggerSource] = []
            for trigger_config in config.triggers:
                trigger = self._trigger_loader(trigger_config, self.event_router)
                triggers.append(trigger)

            if not triggers:
                logger.warning(
                    f"Background session '{config.name}' has no triggers configured"
                )
                # Create a manual trigger so we can still fire events programmatically
                manual = ManualTrigger()
                triggers.append(manual)

            # Merge trigger streams and handle events
            async for event in self._merge_triggers(triggers):
                await self._handle_trigger(session_id, state, event)

        except asyncio.CancelledError:
            logger.debug(f"Background session '{config.name}' cancelled")
            raise
        except Exception as e:
            state.error = str(e)
            state.status = "failed"
            logger.exception(f"Background session '{config.name}' failed: {e}")

            # Emit error event
            await self.event_router.emit(
                "background:error",
                {
                    "session_id": session_id,
                    "name": config.name,
                    "error": str(e),
                },
            )

            # Maybe restart
            if config.restart_on_failure and state.restart_count < config.max_restarts:
                state.restart_count += 1
                logger.info(
                    f"Restarting background session '{config.name}' "
                    f"(attempt {state.restart_count}/{config.max_restarts})"
                )
                await asyncio.sleep(1.0)  # Brief delay before restart
                await self._run_background_session(session_id, state)

    async def _merge_triggers(
        self,
        triggers: list[TriggerSource],
    ):
        """Merge multiple trigger sources into a single stream.

        Args:
            triggers: List of trigger sources to merge

        Yields:
            TriggerEvent objects from any trigger source
        """
        queue: asyncio.Queue[TriggerEvent] = asyncio.Queue()

        async def feed_queue(trigger: TriggerSource) -> None:
            try:
                async for event in trigger.watch():
                    await queue.put(event)
            except asyncio.CancelledError:
                pass

        # Start all triggers
        tasks = [asyncio.create_task(feed_queue(t)) for t in triggers]

        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            # Stop all triggers
            for task in tasks:
                task.cancel()
            for trigger in triggers:
                try:
                    await trigger.stop()
                except Exception:
                    pass

    async def _handle_trigger(
        self,
        session_id: str,
        state: BackgroundSessionState,
        event: TriggerEvent,
    ) -> None:
        """Handle a trigger event by spawning a session.

        Args:
            session_id: Background session ID
            state: Background session state
            event: The trigger event to handle
        """
        config = state.config
        state.trigger_count += 1
        state.last_trigger_time = datetime.now(UTC)

        logger.info(
            f"Background session '{config.name}' triggered by {event.type.value}"
        )

        # Check pool size limit
        if state.active_spawns >= config.pool_size:
            logger.debug(
                f"Pool limit reached for '{config.name}' "
                f"({state.active_spawns}/{config.pool_size}), skipping spawn"
            )
            return

        # Build instruction from event
        instruction = self._build_instruction(config, event)

        # Spawn session
        state.active_spawns += 1
        state.spawn_count += 1

        try:
            # Import here to avoid circular imports
            from amplifier_foundation.spawn import spawn_bundle

            result = await spawn_bundle(
                bundle=config.bundle,
                instruction=instruction,
                parent_session=self.parent_session,
                inherit_providers=True,
                session_name=f"{config.name}-{state.spawn_count}",
                session_storage=self.session_storage,
                event_router=self.event_router,
            )

            # Emit completion event
            completion_data = {
                "background_session_id": session_id,
                "session_name": config.name,
                "spawned_session_id": result.session_id,
                "trigger_type": event.type.value,
                "trigger_data": event.data,
                "output": result.output,
                "turn_count": result.turn_count,
                "success": True,
            }

            await self.event_router.emit(
                "background:spawn:completed",
                completion_data,
                source_session_id=session_id,
            )

            # Emit custom completion event if configured
            if config.on_complete_emit:
                await self.event_router.emit(
                    config.on_complete_emit,
                    completion_data,
                    source_session_id=session_id,
                )

        except Exception as e:
            logger.exception(
                f"Spawn failed for background session '{config.name}': {e}"
            )

            error_data = {
                "background_session_id": session_id,
                "session_name": config.name,
                "trigger_type": event.type.value,
                "trigger_data": event.data,
                "error": str(e),
                "success": False,
            }

            await self.event_router.emit(
                "background:spawn:error",
                error_data,
                source_session_id=session_id,
            )

            # Emit custom error event if configured
            if config.on_error_emit:
                await self.event_router.emit(
                    config.on_error_emit,
                    error_data,
                    source_session_id=session_id,
                )

        finally:
            state.active_spawns -= 1

    def _build_instruction(
        self, config: BackgroundSessionConfig, event: TriggerEvent
    ) -> str:
        """Build instruction string from trigger event.

        Args:
            config: Background session configuration
            event: The trigger event

        Returns:
            Instruction string for the spawned session
        """
        # Build event summary based on type
        if event.type == TriggerType.FILE_CHANGE:
            event_summary = f"File {event.change_type}: {event.file_path}"
        elif event.type == TriggerType.TIMER:
            event_summary = f"Timer tick #{event.data.get('fire_count', '?')}"
        elif event.type == TriggerType.SESSION_EVENT:
            event_summary = (
                f"Session event '{event.event_name}' "
                f"from {event.source_session_id or 'unknown'}"
            )
        elif event.type == TriggerType.MANUAL:
            event_summary = f"Manual trigger: {event.data}"
        else:
            event_summary = f"{event.type.value}: {event.data}"

        return config.instruction_template.format(
            event_summary=event_summary,
            event_type=event.type.value,
            event_data=event.data,
            trigger_source=event.source,
        )

    def _on_task_complete(self, session_id: str, task: asyncio.Task) -> None:
        """Callback when a background task completes."""
        state = self._sessions.get(session_id)
        if not state:
            return

        if task.cancelled():
            state.status = "stopped"
        elif task.exception():
            state.status = "failed"
            state.error = str(task.exception())
        else:
            state.status = "stopped"

    async def fire_manual(
        self, session_id: str, data: dict[str, Any] | None = None
    ) -> bool:
        """Fire a manual trigger for a background session.

        This is useful for testing or for programmatically triggering
        a background session outside of its normal trigger sources.

        Args:
            session_id: Background session to trigger
            data: Optional data to include in the trigger event

        Returns:
            True if trigger was fired, False if session not found
        """
        state = self._sessions.get(session_id)
        if not state or state.status != "running":
            return False

        # Create and handle a manual trigger event directly
        event = TriggerEvent(
            type=TriggerType.MANUAL,
            source="manual-api",
            timestamp=datetime.now(UTC),
            data=data or {},
        )

        await self._handle_trigger(session_id, state, event)
        return True
