"""
Tests for trigger infrastructure.

Tests TriggerEvent, TriggerSource protocol, and built-in trigger implementations.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from amplifier_foundation.events import EventRouter
from amplifier_foundation.triggers import (
    ManualTrigger,
    SessionEventTrigger,
    TimerTrigger,
    TriggerEvent,
    TriggerSource,
    TriggerType,
)


# =============================================================================
# TriggerEvent Tests
# =============================================================================


class TestTriggerEvent:
    """Tests for TriggerEvent dataclass."""

    def test_event_creation_with_all_fields(self):
        """Test creating an event with all fields specified."""
        timestamp = datetime.now(UTC)
        event = TriggerEvent(
            type=TriggerType.FILE_CHANGE,
            source="file-watcher",
            timestamp=timestamp,
            data={"key": "value"},
            file_path="/path/to/file.py",
            change_type="modified",
        )

        assert event.type == TriggerType.FILE_CHANGE
        assert event.source == "file-watcher"
        assert event.timestamp == timestamp
        assert event.data == {"key": "value"}
        assert event.file_path == "/path/to/file.py"
        assert event.change_type == "modified"

    def test_event_creation_with_defaults(self):
        """Test creating an event with default values."""
        event = TriggerEvent(
            type=TriggerType.TIMER,
            source="timer",
        )

        assert event.type == TriggerType.TIMER
        assert event.source == "timer"
        assert event.timestamp is not None
        assert event.data == {}
        assert event.file_path is None
        assert event.change_type is None

    def test_session_event_fields(self):
        """Test session event specific fields."""
        event = TriggerEvent(
            type=TriggerType.SESSION_EVENT,
            source="session-trigger",
            source_session_id="session-123",
            event_name="work:completed",
        )

        assert event.type == TriggerType.SESSION_EVENT
        assert event.source_session_id == "session-123"
        assert event.event_name == "work:completed"


class TestTriggerType:
    """Tests for TriggerType enum."""

    def test_all_types_exist(self):
        """Test that all expected trigger types exist."""
        assert TriggerType.FILE_CHANGE.value == "file_change"
        assert TriggerType.TIMER.value == "timer"
        assert TriggerType.SESSION_EVENT.value == "session_event"
        assert TriggerType.WEBHOOK.value == "webhook"
        assert TriggerType.ISSUE_EVENT.value == "issue_event"
        assert TriggerType.MANUAL.value == "manual"


# =============================================================================
# TriggerSource Protocol Tests
# =============================================================================


class TestTriggerSourceProtocol:
    """Tests for TriggerSource protocol conformance."""

    def test_timer_trigger_is_trigger_source(self):
        """Test that TimerTrigger conforms to TriggerSource protocol."""
        trigger = TimerTrigger()
        assert isinstance(trigger, TriggerSource)

    def test_manual_trigger_is_trigger_source(self):
        """Test that ManualTrigger conforms to TriggerSource protocol."""
        trigger = ManualTrigger()
        assert isinstance(trigger, TriggerSource)

    def test_session_event_trigger_is_trigger_source(self):
        """Test that SessionEventTrigger conforms to TriggerSource protocol."""
        router = EventRouter()
        trigger = SessionEventTrigger(router)
        assert isinstance(trigger, TriggerSource)


# =============================================================================
# TimerTrigger Tests
# =============================================================================


class TestTimerTrigger:
    """Tests for TimerTrigger."""

    def test_configure_defaults(self):
        """Test default configuration."""
        trigger = TimerTrigger()
        assert trigger.interval_seconds == 60.0
        assert trigger.immediate is False

    def test_configure_from_dict(self):
        """Test configuration from dictionary."""
        trigger = TimerTrigger()
        trigger.configure({"interval_seconds": 30, "immediate": True})

        assert trigger.interval_seconds == 30.0
        assert trigger.immediate is True

    @pytest.mark.asyncio
    async def test_timer_emits_immediate(self):
        """Test that timer emits immediately when configured."""
        trigger = TimerTrigger()
        trigger.configure({"interval_seconds": 10, "immediate": True})

        events: list[TriggerEvent] = []

        async def collect():
            async for event in trigger.watch():
                events.append(event)
                await trigger.stop()
                break

        await asyncio.wait_for(collect(), timeout=1.0)

        assert len(events) == 1
        assert events[0].type == TriggerType.TIMER
        assert events[0].data["fire_count"] == 1

    @pytest.mark.asyncio
    async def test_timer_emits_on_interval(self):
        """Test that timer emits at configured interval."""
        trigger = TimerTrigger()
        trigger.configure({"interval_seconds": 0.1})  # Very short for testing

        events: list[TriggerEvent] = []

        async def collect():
            async for event in trigger.watch():
                events.append(event)
                if len(events) >= 2:
                    await trigger.stop()
                    break

        await asyncio.wait_for(collect(), timeout=1.0)

        assert len(events) == 2
        assert events[0].data["fire_count"] == 1
        assert events[1].data["fire_count"] == 2

    @pytest.mark.asyncio
    async def test_timer_stop(self):
        """Test that timer stops cleanly."""
        trigger = TimerTrigger()
        trigger.configure({"interval_seconds": 0.05})

        task = asyncio.create_task(trigger.watch().__anext__())
        await asyncio.sleep(0.1)

        await trigger.stop()
        task.cancel()

        try:
            await task
        except (asyncio.CancelledError, StopAsyncIteration):
            pass

        # Should be stopped
        assert trigger._running is False


# =============================================================================
# ManualTrigger Tests
# =============================================================================


class TestManualTrigger:
    """Tests for ManualTrigger."""

    def test_configure_noop(self):
        """Test that configure is a no-op."""
        trigger = ManualTrigger()
        trigger.configure({"any": "config"})  # Should not raise

    @pytest.mark.asyncio
    async def test_fire_emits_event(self):
        """Test that fire() emits an event."""
        trigger = ManualTrigger()
        received: list[TriggerEvent] = []

        async def collect():
            async for event in trigger.watch():
                received.append(event)
                break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.05)

        await trigger.fire({"reason": "test"})
        await asyncio.wait_for(task, timeout=1.0)

        assert len(received) == 1
        assert received[0].type == TriggerType.MANUAL
        assert received[0].data == {"reason": "test"}

    @pytest.mark.asyncio
    async def test_fire_multiple_events(self):
        """Test firing multiple events."""
        trigger = ManualTrigger()
        received: list[TriggerEvent] = []

        async def collect():
            count = 0
            async for event in trigger.watch():
                received.append(event)
                count += 1
                if count >= 3:
                    break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.05)

        await trigger.fire({"n": 1})
        await trigger.fire({"n": 2})
        await trigger.fire({"n": 3})

        await asyncio.wait_for(task, timeout=1.0)

        assert len(received) == 3
        assert [e.data["n"] for e in received] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_stop(self):
        """Test that stop works correctly."""
        trigger = ManualTrigger()

        async def watch_briefly():
            async for _ in trigger.watch():
                break

        task = asyncio.create_task(watch_briefly())
        await asyncio.sleep(0.05)

        await trigger.stop()
        await trigger.fire({})  # Fire to unblock

        await asyncio.wait_for(task, timeout=1.0)


# =============================================================================
# SessionEventTrigger Tests
# =============================================================================


class TestSessionEventTrigger:
    """Tests for SessionEventTrigger."""

    def test_configure_defaults(self):
        """Test default configuration."""
        router = EventRouter()
        trigger = SessionEventTrigger(router)

        assert trigger._event_names == ["*"]
        assert trigger._source_sessions is None

    def test_configure_from_dict(self):
        """Test configuration from dictionary."""
        router = EventRouter()
        trigger = SessionEventTrigger(router)
        trigger.configure(
            {
                "event_names": ["work:completed", "work:failed"],
                "source_sessions": ["session-A", "session-B"],
            }
        )

        assert trigger._event_names == ["work:completed", "work:failed"]
        assert trigger._source_sessions == ["session-A", "session-B"]

    @pytest.mark.asyncio
    async def test_receives_session_events(self):
        """Test that trigger receives events from EventRouter."""
        router = EventRouter()
        trigger = SessionEventTrigger(router)
        trigger.configure({"event_names": ["test:event"]})

        received: list[TriggerEvent] = []

        async def collect():
            async for event in trigger.watch():
                received.append(event)
                break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.05)

        await router.emit("test:event", {"value": 42}, source_session_id="src-session")
        await asyncio.wait_for(task, timeout=1.0)

        assert len(received) == 1
        assert received[0].type == TriggerType.SESSION_EVENT
        assert received[0].event_name == "test:event"
        assert received[0].source_session_id == "src-session"
        assert received[0].data == {"value": 42}

    @pytest.mark.asyncio
    async def test_filters_by_event_name(self):
        """Test that trigger filters by event name."""
        router = EventRouter()
        trigger = SessionEventTrigger(router)
        trigger.configure({"event_names": ["wanted:event"]})

        received: list[TriggerEvent] = []

        async def collect():
            async for event in trigger.watch():
                received.append(event)
                break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.05)

        # This should NOT be received
        await router.emit("unwanted:event", {"n": 1})
        await asyncio.sleep(0.02)

        # This SHOULD be received
        await router.emit("wanted:event", {"n": 2})

        await asyncio.wait_for(task, timeout=1.0)

        assert len(received) == 1
        assert received[0].event_name == "wanted:event"

    @pytest.mark.asyncio
    async def test_stop(self):
        """Test that stop works correctly."""
        router = EventRouter()
        trigger = SessionEventTrigger(router)

        async def watch_briefly():
            async for _ in trigger.watch():
                break

        task = asyncio.create_task(watch_briefly())
        await asyncio.sleep(0.05)

        await trigger.stop()
        # Emit to ensure we can break out
        await router.emit("any:event", {})

        try:
            await asyncio.wait_for(task, timeout=1.0)
        except asyncio.CancelledError:
            pass


# =============================================================================
# Integration Tests
# =============================================================================


class TestTriggerIntegration:
    """Integration tests combining multiple triggers."""

    @pytest.mark.asyncio
    async def test_session_event_chain(self):
        """Test Session A completes -> triggers Session B pattern."""
        router = EventRouter()

        # Session B's trigger: watch for session:completed from Session A
        trigger_b = SessionEventTrigger(router)
        trigger_b.configure(
            {
                "event_names": ["session:completed"],
                "source_sessions": ["session-A"],
            }
        )

        received_by_b: list[TriggerEvent] = []

        async def session_b_watcher():
            async for event in trigger_b.watch():
                received_by_b.append(event)
                break

        watcher_task = asyncio.create_task(session_b_watcher())
        await asyncio.sleep(0.05)

        # Session A completes and emits event
        await router.emit(
            "session:completed",
            {"output": "task done", "session_id": "session-A-child"},
            source_session_id="session-A",
        )

        await asyncio.wait_for(watcher_task, timeout=1.0)

        assert len(received_by_b) == 1
        assert received_by_b[0].type == TriggerType.SESSION_EVENT
        assert received_by_b[0].source_session_id == "session-A"
        assert received_by_b[0].data["output"] == "task done"
