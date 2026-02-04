"""
Tests for cross-session event routing.

Tests the EventRouter, SessionEvent, and SessionEmitter classes.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from amplifier_foundation.events import EventRouter, SessionEvent


# =============================================================================
# SessionEvent Tests
# =============================================================================


class TestSessionEvent:
    """Tests for SessionEvent dataclass."""

    def test_event_creation_with_all_fields(self):
        """Test creating an event with all fields specified."""
        timestamp = datetime.now(UTC)
        event = SessionEvent(
            name="test:event",
            data={"key": "value"},
            source_session_id="session-123",
            timestamp=timestamp,
        )

        assert event.name == "test:event"
        assert event.data == {"key": "value"}
        assert event.source_session_id == "session-123"
        assert event.timestamp == timestamp

    def test_event_creation_with_defaults(self):
        """Test creating an event with default values."""
        event = SessionEvent(
            name="test:event",
            data={"key": "value"},
        )

        assert event.name == "test:event"
        assert event.data == {"key": "value"}
        assert event.source_session_id is None
        assert event.timestamp is not None
        assert isinstance(event.timestamp, datetime)

    def test_event_timestamp_is_utc(self):
        """Test that default timestamp uses UTC."""
        event = SessionEvent(name="test", data={})
        # Check that timestamp is timezone-aware UTC
        assert event.timestamp.tzinfo is not None


# =============================================================================
# EventRouter Basic Tests
# =============================================================================


class TestEventRouterBasic:
    """Basic tests for EventRouter."""

    @pytest.mark.asyncio
    async def test_emit_to_single_subscriber(self):
        """Test emitting an event to a single subscriber."""
        router = EventRouter()
        received_events: list[SessionEvent] = []

        async def collector():
            async for event in router.subscribe(["test:event"]):
                received_events.append(event)
                break  # Only collect one event

        # Start subscriber in background
        task = asyncio.create_task(collector())

        # Give subscriber time to register
        await asyncio.sleep(0.01)

        # Emit event
        await router.emit("test:event", {"message": "hello"}, source_session_id="src-1")

        # Wait for collector to finish
        await asyncio.wait_for(task, timeout=1.0)

        assert len(received_events) == 1
        assert received_events[0].name == "test:event"
        assert received_events[0].data == {"message": "hello"}
        assert received_events[0].source_session_id == "src-1"

    @pytest.mark.asyncio
    async def test_emit_to_multiple_subscribers(self):
        """Test emitting an event to multiple subscribers."""
        router = EventRouter()
        received_1: list[SessionEvent] = []
        received_2: list[SessionEvent] = []

        async def collector_1():
            async for event in router.subscribe(["test:event"]):
                received_1.append(event)
                break

        async def collector_2():
            async for event in router.subscribe(["test:event"]):
                received_2.append(event)
                break

        # Start both subscribers
        task1 = asyncio.create_task(collector_1())
        task2 = asyncio.create_task(collector_2())

        await asyncio.sleep(0.01)

        # Emit one event
        await router.emit("test:event", {"value": 42})

        # Wait for both collectors
        await asyncio.wait_for(asyncio.gather(task1, task2), timeout=1.0)

        # Both should receive the event
        assert len(received_1) == 1
        assert len(received_2) == 1
        assert received_1[0].data == {"value": 42}
        assert received_2[0].data == {"value": 42}

    @pytest.mark.asyncio
    async def test_wildcard_subscription(self):
        """Test subscribing with wildcard receives all events."""
        router = EventRouter()
        received_events: list[SessionEvent] = []

        async def collector():
            count = 0
            async for event in router.subscribe(["*"]):
                received_events.append(event)
                count += 1
                if count >= 3:
                    break

        task = asyncio.create_task(collector())
        await asyncio.sleep(0.01)

        # Emit different event types
        await router.emit("event:one", {"n": 1})
        await router.emit("event:two", {"n": 2})
        await router.emit("event:three", {"n": 3})

        await asyncio.wait_for(task, timeout=1.0)

        # Should receive all three events
        assert len(received_events) == 3
        names = [e.name for e in received_events]
        assert "event:one" in names
        assert "event:two" in names
        assert "event:three" in names

    @pytest.mark.asyncio
    async def test_source_session_filtering(self):
        """Test filtering events by source session ID."""
        router = EventRouter()
        received_events: list[SessionEvent] = []

        async def collector():
            async for event in router.subscribe(
                ["test:event"], source_sessions=["session-A"]
            ):
                received_events.append(event)
                break

        task = asyncio.create_task(collector())
        await asyncio.sleep(0.01)

        # Emit from different sessions
        await router.emit("test:event", {"from": "B"}, source_session_id="session-B")
        await router.emit("test:event", {"from": "A"}, source_session_id="session-A")

        await asyncio.wait_for(task, timeout=1.0)

        # Should only receive from session-A
        assert len(received_events) == 1
        assert received_events[0].data == {"from": "A"}
        assert received_events[0].source_session_id == "session-A"

    @pytest.mark.asyncio
    async def test_queue_full_handling(self):
        """Test that full queue logs warning but doesn't block emitter."""
        router = EventRouter()

        # Subscribe with very small queue
        async def slow_collector():
            async for event in router.subscribe(["test:event"], queue_size=1):
                # Don't consume events quickly
                await asyncio.sleep(1.0)
                break

        task = asyncio.create_task(slow_collector())
        await asyncio.sleep(0.01)

        # Emit more events than queue can hold
        # This should not block
        for i in range(5):
            await router.emit("test:event", {"n": i})

        # Cancel the slow collector
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Test passes if we get here without blocking

    @pytest.mark.asyncio
    async def test_subscriber_cleanup(self):
        """Test that unsubscribing removes queue from router."""
        router = EventRouter()

        # Initial state
        assert router.subscriber_count == 0

        # Start a subscription
        async def collector():
            async for event in router.subscribe(["test:event"]):
                break

        task = asyncio.create_task(collector())
        await asyncio.sleep(0.01)

        # Should have one subscriber
        assert router.subscriber_count == 1

        # Emit to trigger the break
        await router.emit("test:event", {})
        await asyncio.wait_for(task, timeout=1.0)

        # After subscription ends, should be cleaned up
        # (give it a moment to clean up)
        await asyncio.sleep(0.01)
        assert router.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_event_timestamp_populated(self):
        """Test that emitted events have timestamp populated."""
        router = EventRouter()
        received_event: SessionEvent | None = None

        async def collector():
            nonlocal received_event
            async for event in router.subscribe(["test:event"]):
                received_event = event
                break

        task = asyncio.create_task(collector())
        await asyncio.sleep(0.01)

        before = datetime.now(UTC)
        await router.emit("test:event", {})
        after = datetime.now(UTC)

        await asyncio.wait_for(task, timeout=1.0)

        assert received_event is not None
        assert received_event.timestamp >= before
        assert received_event.timestamp <= after


# =============================================================================
# EventRouter Advanced Tests
# =============================================================================


class TestEventRouterAdvanced:
    """Advanced tests for EventRouter."""

    @pytest.mark.asyncio
    async def test_concurrent_emit_subscribe(self):
        """Test thread-safety under concurrent access."""
        router = EventRouter()
        received_counts = [0, 0, 0]

        async def collector(index: int, event_count: int):
            count = 0
            async for _ in router.subscribe(["concurrent:event"]):
                count += 1
                if count >= event_count:
                    break
            received_counts[index] = count

        # Start multiple subscribers
        tasks = [asyncio.create_task(collector(i, 10)) for i in range(3)]

        await asyncio.sleep(0.01)

        # Emit many events concurrently
        emit_tasks = [router.emit("concurrent:event", {"n": i}) for i in range(10)]
        await asyncio.gather(*emit_tasks)

        # Wait for all collectors
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=2.0)

        # All collectors should have received all events
        assert all(count == 10 for count in received_counts)

    @pytest.mark.asyncio
    async def test_multiple_event_names_subscription(self):
        """Test subscribing to multiple event names."""
        router = EventRouter()
        received_events: list[SessionEvent] = []

        async def collector():
            count = 0
            async for event in router.subscribe(["event:a", "event:b"]):
                received_events.append(event)
                count += 1
                if count >= 2:
                    break

        task = asyncio.create_task(collector())
        await asyncio.sleep(0.01)

        await router.emit("event:a", {"type": "a"})
        await router.emit("event:c", {"type": "c"})  # Should not be received
        await router.emit("event:b", {"type": "b"})

        await asyncio.wait_for(task, timeout=1.0)

        assert len(received_events) == 2
        types = [e.data["type"] for e in received_events]
        assert "a" in types
        assert "b" in types
        assert "c" not in types

    @pytest.mark.asyncio
    async def test_wait_for_event_success(self):
        """Test wait_for_event returns event when received."""
        router = EventRouter()

        async def emitter():
            await asyncio.sleep(0.05)
            await router.emit("awaited:event", {"result": "success"})

        asyncio.create_task(emitter())

        event = await router.wait_for_event(["awaited:event"], timeout=1.0)

        assert event is not None
        assert event.name == "awaited:event"
        assert event.data == {"result": "success"}

    @pytest.mark.asyncio
    async def test_wait_for_event_timeout(self):
        """Test wait_for_event returns None on timeout."""
        router = EventRouter()

        event = await router.wait_for_event(["never:happens"], timeout=0.05)

        assert event is None

    @pytest.mark.asyncio
    async def test_wait_for_event_with_source_filter(self):
        """Test wait_for_event with source session filtering."""
        router = EventRouter()

        async def emitter():
            await asyncio.sleep(0.02)
            await router.emit(
                "test:event", {"from": "wrong"}, source_session_id="wrong-session"
            )
            await asyncio.sleep(0.02)
            await router.emit(
                "test:event", {"from": "right"}, source_session_id="right-session"
            )

        asyncio.create_task(emitter())

        event = await router.wait_for_event(
            ["test:event"],
            source_sessions=["right-session"],
            timeout=1.0,
        )

        assert event is not None
        assert event.data == {"from": "right"}
        assert event.source_session_id == "right-session"


# =============================================================================
# SessionEmitter Tests
# =============================================================================


class TestSessionEmitter:
    """Tests for SessionEmitter."""

    @pytest.mark.asyncio
    async def test_session_emitter_binds_id(self):
        """Test that SessionEmitter auto-fills source_session_id."""
        router = EventRouter()
        emitter = router.create_session_emitter("my-session-123")

        received_event: SessionEvent | None = None

        async def collector():
            nonlocal received_event
            async for event in router.subscribe(["test:event"]):
                received_event = event
                break

        task = asyncio.create_task(collector())
        await asyncio.sleep(0.01)

        # Emit via session emitter (no source_session_id parameter)
        await emitter.emit("test:event", {"data": "test"})

        await asyncio.wait_for(task, timeout=1.0)

        assert received_event is not None
        assert received_event.source_session_id == "my-session-123"
        assert received_event.data == {"data": "test"}

    def test_session_emitter_session_id_property(self):
        """Test SessionEmitter exposes session_id property."""
        router = EventRouter()
        emitter = router.create_session_emitter("session-xyz")

        assert emitter.session_id == "session-xyz"

    @pytest.mark.asyncio
    async def test_multiple_emitters_different_sessions(self):
        """Test multiple emitters can coexist for different sessions."""
        router = EventRouter()
        emitter_a = router.create_session_emitter("session-A")
        emitter_b = router.create_session_emitter("session-B")

        received_events: list[SessionEvent] = []

        async def collector():
            count = 0
            async for event in router.subscribe(["test:event"]):
                received_events.append(event)
                count += 1
                if count >= 2:
                    break

        task = asyncio.create_task(collector())
        await asyncio.sleep(0.01)

        await emitter_a.emit("test:event", {"from": "A"})
        await emitter_b.emit("test:event", {"from": "B"})

        await asyncio.wait_for(task, timeout=1.0)

        assert len(received_events) == 2
        sources = {e.source_session_id for e in received_events}
        assert sources == {"session-A", "session-B"}


# =============================================================================
# Integration Tests
# =============================================================================


class TestEventRouterIntegration:
    """Integration tests for EventRouter with spawn_bundle patterns."""

    @pytest.mark.asyncio
    async def test_background_session_completion_event(self):
        """Simulate background session emitting completion event."""
        router = EventRouter()
        completion_received = asyncio.Event()
        received_data: dict = {}

        async def completion_handler():
            async for event in router.subscribe(["session:completed"]):
                received_data.update(event.data)
                completion_received.set()
                break

        # Start handler
        handler_task = asyncio.create_task(completion_handler())
        await asyncio.sleep(0.01)

        # Simulate what spawn_bundle does for background sessions
        await router.emit(
            "session:completed",
            {
                "session_id": "worker-123",
                "bundle_name": "test-bundle",
                "output": "Task completed successfully",
                "success": True,
            },
        )

        await asyncio.wait_for(completion_received.wait(), timeout=1.0)
        handler_task.cancel()
        try:
            await handler_task
        except asyncio.CancelledError:
            pass

        assert received_data["session_id"] == "worker-123"
        assert received_data["success"] is True

    @pytest.mark.asyncio
    async def test_cross_session_communication(self):
        """Test Session A emits, Session B receives via EventRouter."""
        router = EventRouter()

        # Session A's emitter
        session_a_emitter = router.create_session_emitter("session-A")

        # Session B subscribes
        received_by_b: list[SessionEvent] = []

        async def session_b_receiver():
            async for event in router.subscribe(
                ["work:completed"], source_sessions=["session-A"]
            ):
                received_by_b.append(event)
                break

        receiver_task = asyncio.create_task(session_b_receiver())
        await asyncio.sleep(0.01)

        # Session A emits
        await session_a_emitter.emit(
            "work:completed", {"task_id": "task-456", "result": "done"}
        )

        await asyncio.wait_for(receiver_task, timeout=1.0)

        assert len(received_by_b) == 1
        assert received_by_b[0].source_session_id == "session-A"
        assert received_by_b[0].data["task_id"] == "task-456"
