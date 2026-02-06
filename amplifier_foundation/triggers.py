"""
Trigger source infrastructure.

Provides the protocol and base types for event triggers that can
activate sessions based on file changes, timers, session events, or other sources.

Triggers are the input side of event-driven orchestration:
- TriggerSource implementations watch for specific events
- TriggerEvent objects describe what happened
- BackgroundSessionManager connects triggers to session spawning

Example:
    # Timer trigger that fires every 60 seconds
    class TimerTrigger(TriggerSource):
        def configure(self, config):
            self.interval = config.get("interval_seconds", 60)

        async def watch(self):
            while self._running:
                await asyncio.sleep(self.interval)
                yield TriggerEvent(
                    type=TriggerType.TIMER,
                    source="timer",
                    timestamp=datetime.now(UTC),
                    data={"interval": self.interval}
                )

        async def stop(self):
            self._running = False
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, AsyncIterator, Protocol, runtime_checkable

if TYPE_CHECKING:
    from amplifier_foundation.events import EventRouter

logger = logging.getLogger(__name__)


class TriggerType(Enum):
    """Categories of trigger events."""

    FILE_CHANGE = "file_change"
    """File system change (create, modify, delete)."""

    TIMER = "timer"
    """Time-based trigger (interval, cron, scheduled)."""

    SESSION_EVENT = "session_event"
    """Event from another session via EventRouter."""

    WEBHOOK = "webhook"
    """External HTTP webhook call."""

    ISSUE_EVENT = "issue_event"
    """Issue tracker event (created, updated, assigned)."""

    MANUAL = "manual"
    """Manually triggered (e.g., by user command)."""


@dataclass
class TriggerEvent:
    """An event that can trigger session activation.

    This is the unified representation of "something happened" that
    the BackgroundSessionManager uses to decide whether to spawn sessions.

    Attributes:
        type: Category of trigger (file, timer, session, etc.)
        source: Identifier for the trigger source (e.g., "file-watcher", "timer-60s")
        timestamp: When the event occurred
        data: Event-specific payload
    """

    type: TriggerType
    """Category of trigger."""

    source: str
    """Identifier for the trigger source."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    """When the event occurred."""

    data: dict[str, Any] = field(default_factory=dict)
    """Event-specific payload."""

    # File change specific fields
    file_path: str | None = None
    """Path to changed file (for FILE_CHANGE events)."""

    change_type: str | None = None
    """Type of change: 'created', 'modified', 'deleted' (for FILE_CHANGE events)."""

    # Session event specific fields
    source_session_id: str | None = None
    """Session that emitted the event (for SESSION_EVENT events)."""

    event_name: str | None = None
    """Name of the session event (for SESSION_EVENT events)."""


@runtime_checkable
class TriggerSource(Protocol):
    """
    Protocol for trigger sources.

    Implementations watch for specific types of events and yield
    TriggerEvent objects when they occur. Each trigger source is
    configured from bundle configuration and runs as a background
    async iterator.

    Lifecycle:
        1. configure() - Called with config from bundle
        2. watch() - Async iterator that yields events
        3. stop() - Called to gracefully shut down

    Example implementation:
        class TimerTrigger:
            def __init__(self):
                self.interval = 60
                self._running = False

            def configure(self, config: dict[str, Any]) -> None:
                self.interval = config.get("interval_seconds", 60)

            async def watch(self) -> AsyncIterator[TriggerEvent]:
                self._running = True
                while self._running:
                    await asyncio.sleep(self.interval)
                    yield TriggerEvent(
                        type=TriggerType.TIMER,
                        source="timer",
                        timestamp=datetime.now(UTC),
                        data={"interval": self.interval}
                    )

            async def stop(self) -> None:
                self._running = False
    """

    def configure(self, config: dict[str, Any]) -> None:
        """Configure the trigger from bundle config.

        Args:
            config: Configuration dictionary from bundle's triggers section
        """
        ...

    async def watch(self) -> AsyncIterator[TriggerEvent]:
        """Yield events as they occur.

        This is an async generator that runs until stop() is called.
        Implementations should handle cancellation gracefully.

        Yields:
            TriggerEvent objects when trigger conditions are met
        """
        ...
        # Make this a generator for type checking
        if False:
            yield  # pragma: no cover

    async def stop(self) -> None:
        """Stop watching for events.

        Called when the trigger should shut down. Implementations should
        clean up resources and cause watch() to exit gracefully.
        """
        ...


class SessionEventTrigger:
    """
    Trigger source that fires on events from the EventRouter.

    This bridges the EventRouter's pub/sub system to the trigger infrastructure,
    allowing background sessions to be spawned in response to session events.

    Configuration:
        event_names: List of event names to subscribe to (e.g., ["session:completed"])
        source_sessions: Optional list of session IDs to filter by

    Example:
        trigger = SessionEventTrigger()
        trigger.configure({
            "event_names": ["work:completed"],
            "source_sessions": ["worker-pool-*"]  # Not yet implemented: patterns
        })

        async for event in trigger.watch():
            print(f"Session event: {event.event_name}")
    """

    def __init__(self, event_router: "EventRouter") -> None:
        """Initialize with an EventRouter instance.

        Args:
            event_router: The EventRouter to subscribe to
        """
        self._router = event_router
        self._event_names: list[str] = ["*"]
        self._source_sessions: list[str] | None = None
        self._running = False

    def configure(self, config: dict[str, Any]) -> None:
        """Configure from bundle config.

        Args:
            config: Configuration with event_names and optional source_sessions
        """
        self._event_names = config.get("event_names", ["*"])
        self._source_sessions = config.get("source_sessions")

    async def watch(self) -> AsyncIterator[TriggerEvent]:
        """Watch for session events from the EventRouter.

        Yields:
            TriggerEvent for each matching session event
        """
        self._running = True

        try:
            async for session_event in self._router.subscribe(
                self._event_names, self._source_sessions
            ):
                if not self._running:
                    break

                yield TriggerEvent(
                    type=TriggerType.SESSION_EVENT,
                    source="session-event-trigger",
                    timestamp=session_event.timestamp,
                    data=session_event.data,
                    source_session_id=session_event.source_session_id,
                    event_name=session_event.name,
                )
        except asyncio.CancelledError:
            logger.debug("SessionEventTrigger cancelled")
            raise

    async def stop(self) -> None:
        """Stop watching for events."""
        self._running = False


class TimerTrigger:
    """
    Trigger source for time-based events.

    Fires at regular intervals. Useful for periodic tasks like
    health checks, status updates, or scheduled processing.

    Configuration:
        interval_seconds: Seconds between triggers (default: 60)
        immediate: Fire immediately on start (default: False)

    Example:
        trigger = TimerTrigger()
        trigger.configure({"interval_seconds": 300, "immediate": True})

        async for event in trigger.watch():
            print(f"Timer fired at {event.timestamp}")
    """

    def __init__(self) -> None:
        """Initialize the timer trigger."""
        self.interval_seconds: float = 60.0
        self.immediate: bool = False
        self._running = False

    def configure(self, config: dict[str, Any]) -> None:
        """Configure from bundle config.

        Args:
            config: Configuration with interval_seconds and optional immediate flag
        """
        self.interval_seconds = float(config.get("interval_seconds", 60))
        self.immediate = config.get("immediate", False)

    async def watch(self) -> AsyncIterator[TriggerEvent]:
        """Watch for timer events.

        Yields:
            TriggerEvent at each interval
        """
        self._running = True
        fire_count = 0

        try:
            # Optionally fire immediately
            if self.immediate:
                fire_count += 1
                yield TriggerEvent(
                    type=TriggerType.TIMER,
                    source="timer-trigger",
                    timestamp=datetime.now(UTC),
                    data={
                        "interval_seconds": self.interval_seconds,
                        "fire_count": fire_count,
                    },
                )

            while self._running:
                await asyncio.sleep(self.interval_seconds)
                if not self._running:
                    break

                fire_count += 1
                yield TriggerEvent(
                    type=TriggerType.TIMER,
                    source="timer-trigger",
                    timestamp=datetime.now(UTC),
                    data={
                        "interval_seconds": self.interval_seconds,
                        "fire_count": fire_count,
                    },
                )
        except asyncio.CancelledError:
            logger.debug("TimerTrigger cancelled")
            raise

    async def stop(self) -> None:
        """Stop the timer."""
        self._running = False


class ManualTrigger:
    """
    Trigger source for manual/programmatic activation.

    This trigger doesn't watch for external events - instead it provides
    a fire() method that can be called to trigger events programmatically.

    Useful for:
    - Testing trigger handlers
    - User-initiated workflows
    - Programmatic session spawning based on application logic

    Example:
        trigger = ManualTrigger()

        # In one coroutine: watch for events
        async for event in trigger.watch():
            print(f"Manual trigger fired: {event.data}")

        # In another coroutine: fire the trigger
        await trigger.fire({"reason": "user requested"})
    """

    def __init__(self) -> None:
        """Initialize the manual trigger."""
        self._queue: asyncio.Queue[TriggerEvent] = asyncio.Queue()
        self._running = False

    def configure(self, config: dict[str, Any]) -> None:
        """Configure from bundle config (no-op for manual trigger)."""
        pass

    async def watch(self) -> AsyncIterator[TriggerEvent]:
        """Watch for manually fired events.

        Yields:
            TriggerEvent when fire() is called
        """
        self._running = True

        try:
            while self._running:
                try:
                    event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                    yield event
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            logger.debug("ManualTrigger cancelled")
            raise

    async def fire(self, data: dict[str, Any] | None = None) -> None:
        """Fire a manual trigger event.

        Args:
            data: Optional data to include in the event
        """
        event = TriggerEvent(
            type=TriggerType.MANUAL,
            source="manual-trigger",
            timestamp=datetime.now(UTC),
            data=data or {},
        )
        await self._queue.put(event)

    async def stop(self) -> None:
        """Stop the trigger."""
        self._running = False
