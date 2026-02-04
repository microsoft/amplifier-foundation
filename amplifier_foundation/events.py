"""
Cross-session event routing.

Provides pub/sub infrastructure for sessions to communicate via events.
This enables event-driven orchestration patterns where:
- Background sessions can notify parent sessions of completion
- Sessions can react to events from other sessions
- Triggers can spawn sessions based on event patterns

Example:
    router = EventRouter()

    # Subscribe to events
    async for event in router.subscribe(["work:completed"]):
        print(f"Work done: {event.data}")

    # Emit an event
    await router.emit("work:completed", {"task_id": "123"}, session_id="abc")
"""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


@dataclass
class SessionEvent:
    """An event emitted by a session.

    Attributes:
        name: Event name (e.g., 'session:completed', 'work:done').
              Convention: use namespace:action format.
        data: Event payload containing relevant information.
        source_session_id: Session that emitted the event (None for system events).
        timestamp: When the event was emitted (UTC).
    """

    name: str
    """Event name (e.g., 'session:completed', 'work:done')."""

    data: dict[str, Any]
    """Event payload."""

    source_session_id: str | None = None
    """Session that emitted the event."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    """When the event was emitted."""


class EventRouter:
    """
    Routes events between sessions.

    Provides a simple pub/sub mechanism for cross-session communication.
    Sessions can emit events and subscribe to events from other sessions.

    The router supports:
    - Multiple subscribers per event name
    - Wildcard subscriptions (["*"] receives all events)
    - Source session filtering (only receive from specific sessions)
    - Non-blocking emit (full queues log warning but don't block)

    Example:
        router = EventRouter()

        # Subscribe to events
        async for event in router.subscribe(["work:completed"]):
            print(f"Work done: {event.data}")

        # Emit an event
        await router.emit("work:completed", {"task_id": "123"}, session_id="abc")

    Thread Safety:
        All operations are protected by an asyncio lock for safe concurrent access.
    """

    def __init__(self) -> None:
        """Initialize the event router."""
        self._subscribers: dict[str, list[asyncio.Queue[SessionEvent]]] = defaultdict(
            list
        )
        self._lock = asyncio.Lock()

    async def emit(
        self,
        event_name: str,
        data: dict[str, Any],
        source_session_id: str | None = None,
    ) -> None:
        """
        Emit an event to all subscribers.

        Args:
            event_name: Name of the event (e.g., 'session:completed')
            data: Event payload
            source_session_id: Session emitting the event (None for system events)

        Note:
            If a subscriber's queue is full, the event is dropped for that
            subscriber with a warning logged. This prevents slow subscribers
            from blocking the emitter.
        """
        event = SessionEvent(
            name=event_name,
            data=data,
            source_session_id=source_session_id,
            timestamp=datetime.now(UTC),
        )

        delivered_count = 0

        async with self._lock:
            # Deliver to specific event subscribers
            for queue in self._subscribers.get(event_name, []):
                try:
                    queue.put_nowait(event)
                    delivered_count += 1
                except asyncio.QueueFull:
                    logger.warning(f"Event queue full for {event_name}")

            # Deliver to wildcard subscribers
            for queue in self._subscribers.get("*", []):
                try:
                    queue.put_nowait(event)
                    delivered_count += 1
                except asyncio.QueueFull:
                    logger.warning("Wildcard event queue full")

        logger.debug(f"Event '{event_name}' emitted to {delivered_count} subscribers")

    async def subscribe(
        self,
        event_names: list[str],
        source_sessions: list[str] | None = None,
        queue_size: int = 100,
    ) -> AsyncIterator[SessionEvent]:
        """
        Subscribe to events.

        Args:
            event_names: Events to subscribe to (use ["*"] for all events)
            source_sessions: Filter by source session IDs (None = all sessions)
            queue_size: Maximum queued events before dropping

        Yields:
            SessionEvent objects as they arrive.

        Note:
            The subscription is automatically cleaned up when the iterator
            is garbage collected or when the async for loop exits.

        Example:
            # Subscribe to specific events
            async for event in router.subscribe(["task:completed", "task:failed"]):
                handle_task_event(event)

            # Subscribe to all events from a specific session
            async for event in router.subscribe(["*"], source_sessions=["session-123"]):
                handle_session_event(event)
        """
        queue: asyncio.Queue[SessionEvent] = asyncio.Queue(maxsize=queue_size)

        # Register with each event name
        async with self._lock:
            for name in event_names:
                self._subscribers[name].append(queue)

        try:
            while True:
                event = await queue.get()

                # Filter by source session if specified
                if source_sessions and event.source_session_id not in source_sessions:
                    continue

                yield event
        finally:
            # Unsubscribe - clean up queues
            async with self._lock:
                for name in event_names:
                    if queue in self._subscribers[name]:
                        self._subscribers[name].remove(queue)

    async def wait_for_event(
        self,
        event_names: list[str],
        source_sessions: list[str] | None = None,
        timeout: float | None = None,
    ) -> SessionEvent | None:
        """
        Wait for a single event matching the criteria.

        This is a convenience method for waiting for exactly one event.

        Args:
            event_names: Events to wait for
            source_sessions: Filter by source session IDs (None = all)
            timeout: Maximum seconds to wait (None = wait forever)

        Returns:
            The first matching SessionEvent, or None if timeout occurred.

        Example:
            # Wait for a session to complete
            event = await router.wait_for_event(
                ["session:completed"],
                source_sessions=["child-session-id"],
                timeout=60.0
            )
            if event:
                print(f"Session completed with: {event.data}")
        """
        try:
            async with asyncio.timeout(timeout):
                async for event in self.subscribe(event_names, source_sessions):
                    return event
        except asyncio.TimeoutError:
            return None
        return None  # Should never reach here, but satisfies type checker

    def create_session_emitter(
        self,
        session_id: str,
    ) -> "SessionEmitter":
        """
        Create an emitter bound to a specific session.

        The returned emitter automatically fills in the source_session_id
        for all emitted events.

        Args:
            session_id: Session ID to bind to

        Returns:
            SessionEmitter that auto-fills source_session_id

        Example:
            emitter = router.create_session_emitter("my-session-123")
            await emitter.emit("work:started", {"task": "process_data"})
            # Event will have source_session_id="my-session-123"
        """
        return SessionEmitter(self, session_id)

    @property
    def subscriber_count(self) -> int:
        """Return total number of active subscriptions (for testing/monitoring)."""
        return sum(len(queues) for queues in self._subscribers.values())


class SessionEmitter:
    """
    Event emitter bound to a specific session.

    This is a convenience wrapper that automatically fills in the
    source_session_id for all emitted events. Sessions should use
    this rather than calling router.emit() directly.

    Example:
        emitter = router.create_session_emitter("session-123")
        await emitter.emit("task:completed", {"result": "success"})
    """

    def __init__(self, router: EventRouter, session_id: str) -> None:
        """
        Initialize the session emitter.

        Args:
            router: The EventRouter to emit through
            session_id: Session ID to attach to all events
        """
        self._router = router
        self._session_id = session_id

    async def emit(self, event_name: str, data: dict[str, Any]) -> None:
        """
        Emit an event from this session.

        Args:
            event_name: Name of the event
            data: Event payload
        """
        await self._router.emit(event_name, data, self._session_id)

    @property
    def session_id(self) -> str:
        """Return the session ID this emitter is bound to."""
        return self._session_id
