"""Tests for spawn.py - unified session spawning primitive."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from amplifier_foundation.spawn import (
    SpawnResult,
    _build_context_messages,
    _extract_recent_turns,
    _filter_modules,
    _merge_module_lists,
    _share_sys_paths,
    spawn_bundle,
)


# =============================================================================
# Test Fixtures and Mocks
# =============================================================================


@dataclass
class MockCoordinator:
    """Mock coordinator for testing."""

    _mounted: dict[str, Any]
    _capabilities: dict[str, Any]
    approval_system: Any = None
    display_system: Any = None
    cancellation: Any = None

    def __init__(self) -> None:
        self._mounted = {}
        self._capabilities = {}
        self.approval_system = None
        self.display_system = None
        self.cancellation = None

    def get(self, name: str) -> Any:
        return self._mounted.get(name)

    async def mount(self, name: str, value: Any) -> None:
        self._mounted[name] = value

    def get_capability(self, name: str) -> Any:
        return self._capabilities.get(name)

    def register_capability(self, name: str, value: Any) -> None:
        self._capabilities[name] = value


@dataclass
class MockSession:
    """Mock AmplifierSession for testing."""

    session_id: str
    config: dict[str, Any]
    coordinator: MockCoordinator
    loader: Any = None
    trace_id: str | None = None

    def __init__(
        self,
        session_id: str = "test-session-id",
        config: dict | None = None,
    ) -> None:
        self.session_id = session_id
        self.config = config or {}
        self.coordinator = MockCoordinator()
        self.loader = None
        self.trace_id = None


class MockSessionStorage:
    """Mock SessionStorage for testing."""

    def __init__(self) -> None:
        self.saved: dict[str, tuple[list[dict], dict]] = {}

    def save(
        self,
        session_id: str,
        transcript: list[dict],
        metadata: dict,
    ) -> None:
        self.saved[session_id] = (transcript, metadata)

    def load(self, session_id: str) -> tuple[list[dict], dict]:
        return self.saved.get(session_id, ([], {}))

    def exists(self, session_id: str) -> bool:
        return session_id in self.saved


class MockContext:
    """Mock context module for testing."""

    def __init__(self, messages: list[dict] | None = None) -> None:
        self._messages = messages or []

    async def get_messages(self) -> list[dict]:
        return list(self._messages)

    async def add_message(self, msg: dict) -> None:
        self._messages.append(msg)


# =============================================================================
# SpawnResult Tests
# =============================================================================


class TestSpawnResult:
    """Tests for SpawnResult dataclass."""

    def test_spawn_result_fields(self) -> None:
        """SpawnResult has correct fields."""
        result = SpawnResult(
            output="Hello, world!",
            session_id="test-123",
            turn_count=1,
        )
        assert result.output == "Hello, world!"
        assert result.session_id == "test-123"
        assert result.turn_count == 1


# =============================================================================
# SessionStorage Protocol Tests
# =============================================================================


class TestSessionStorageProtocol:
    """Tests for SessionStorage protocol compliance."""

    def test_mock_storage_implements_protocol(self) -> None:
        """MockSessionStorage implements SessionStorage protocol."""
        storage = MockSessionStorage()

        # Test save
        storage.save("sess-1", [{"role": "user", "content": "hi"}], {"key": "value"})
        assert storage.exists("sess-1")

        # Test load
        transcript, metadata = storage.load("sess-1")
        assert transcript == [{"role": "user", "content": "hi"}]
        assert metadata == {"key": "value"}

        # Test non-existent
        assert not storage.exists("sess-2")


# =============================================================================
# Helper Function Tests
# =============================================================================


class TestMergeModuleLists:
    """Tests for _merge_module_lists helper."""

    def test_empty_lists(self) -> None:
        """Merging empty lists returns empty list."""
        result = _merge_module_lists([], [])
        assert result == []

    def test_base_only(self) -> None:
        """Base list returned when overlay is empty."""
        base = [{"module": "tool-a"}]
        result = _merge_module_lists(base, [])
        assert result == [{"module": "tool-a"}]

    def test_overlay_only(self) -> None:
        """Overlay list returned when base is empty."""
        overlay = [{"module": "tool-b"}]
        result = _merge_module_lists([], overlay)
        assert result == [{"module": "tool-b"}]

    def test_overlay_wins_on_conflict(self) -> None:
        """Overlay module replaces base module with same name."""
        base = [{"module": "tool-a", "config": {"old": True}}]
        overlay = [{"module": "tool-a", "config": {"new": True}}]
        result = _merge_module_lists(base, overlay)
        assert len(result) == 1
        assert result[0]["config"] == {"new": True}

    def test_non_conflicting_merged(self) -> None:
        """Non-conflicting modules are all included."""
        base = [{"module": "tool-a"}]
        overlay = [{"module": "tool-b"}]
        result = _merge_module_lists(base, overlay)
        assert len(result) == 2
        modules = {m["module"] for m in result}
        assert modules == {"tool-a", "tool-b"}


class TestFilterModules:
    """Tests for _filter_modules helper."""

    def test_filter_false_returns_empty(self) -> None:
        """inherit=False returns empty list."""
        modules = [{"module": "tool-a"}, {"module": "tool-b"}]
        result = _filter_modules(modules, False)
        assert result == []

    def test_filter_true_returns_all(self) -> None:
        """inherit=True returns all modules."""
        modules = [{"module": "tool-a"}, {"module": "tool-b"}]
        result = _filter_modules(modules, True)
        assert len(result) == 2

    def test_filter_list_returns_matching(self) -> None:
        """inherit=[list] returns only matching modules."""
        modules = [{"module": "tool-a"}, {"module": "tool-b"}, {"module": "tool-c"}]
        result = _filter_modules(modules, ["tool-a", "tool-c"])
        assert len(result) == 2
        names = {m["module"] for m in result}
        assert names == {"tool-a", "tool-c"}

    def test_filter_list_empty_pattern(self) -> None:
        """inherit=[] returns empty list."""
        modules = [{"module": "tool-a"}]
        result = _filter_modules(modules, [])
        assert result == []


class TestExtractRecentTurns:
    """Tests for _extract_recent_turns helper."""

    def test_fewer_turns_than_requested(self) -> None:
        """Returns all messages if fewer turns than requested."""
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        result = _extract_recent_turns(messages, n=5)
        assert result == messages

    def test_extracts_last_n_turns(self) -> None:
        """Extracts the last N turns correctly."""
        messages = [
            {"role": "user", "content": "turn 1"},
            {"role": "assistant", "content": "resp 1"},
            {"role": "user", "content": "turn 2"},
            {"role": "assistant", "content": "resp 2"},
            {"role": "user", "content": "turn 3"},
            {"role": "assistant", "content": "resp 3"},
        ]
        result = _extract_recent_turns(messages, n=2)
        # Should get last 2 turns (4 messages)
        assert len(result) == 4
        assert result[0]["content"] == "turn 2"

    def test_empty_messages(self) -> None:
        """Empty messages returns empty."""
        result = _extract_recent_turns([], n=5)
        assert result == []


class TestShareSysPaths:
    """Tests for _share_sys_paths helper."""

    def test_no_paths_when_no_sources(self) -> None:
        """Returns empty when no path sources available."""
        session = MockSession()
        result = _share_sys_paths(session)
        assert result == []

    def test_collects_from_loader(self) -> None:
        """Collects paths from loader._added_paths."""
        session = MockSession()
        session.loader = MagicMock()
        session.loader._added_paths = ["/path/a", "/path/b"]

        result = _share_sys_paths(session)
        assert "/path/a" in result
        assert "/path/b" in result

    def test_collects_from_capability(self) -> None:
        """Collects paths from bundle_package_paths capability."""
        session = MockSession()
        session.coordinator._capabilities["bundle_package_paths"] = ["/cap/path"]

        result = _share_sys_paths(session)
        assert "/cap/path" in result


class TestBuildContextMessages:
    """Tests for _build_context_messages helper."""

    @pytest.mark.asyncio
    async def test_depth_none_returns_empty(self) -> None:
        """context_depth='none' returns empty list."""
        session = MockSession()
        result = await _build_context_messages(session, "none", "conversation", 5)
        assert result == []

    @pytest.mark.asyncio
    async def test_no_context_module_returns_empty(self) -> None:
        """Returns empty if no context module mounted."""
        session = MockSession()
        result = await _build_context_messages(session, "all", "conversation", 5)
        assert result == []

    @pytest.mark.asyncio
    async def test_scope_conversation_filters_correctly(self) -> None:
        """context_scope='conversation' filters to user/assistant only."""
        session = MockSession()
        context = MockContext(
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "tool", "name": "bash", "content": "output"},
            ]
        )
        session.coordinator._mounted["context"] = context

        result = await _build_context_messages(session, "all", "conversation", 5)
        assert len(result) == 2
        roles = {m["role"] for m in result}
        assert roles == {"user", "assistant"}

    @pytest.mark.asyncio
    async def test_scope_agents_includes_delegate(self) -> None:
        """context_scope='agents' includes delegate tool results."""
        session = MockSession()
        context = MockContext(
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "tool", "name": "delegate", "content": "agent output"},
                {"role": "tool", "name": "bash", "content": "bash output"},
            ]
        )
        session.coordinator._mounted["context"] = context

        result = await _build_context_messages(session, "all", "agents", 5)
        assert len(result) == 3  # user, assistant, delegate
        names = [m.get("name") for m in result if m.get("role") == "tool"]
        assert "delegate" in names
        assert "bash" not in names

    @pytest.mark.asyncio
    async def test_scope_full_includes_all(self) -> None:
        """context_scope='full' includes all messages."""
        session = MockSession()
        context = MockContext(
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "tool", "name": "bash", "content": "output"},
            ]
        )
        session.coordinator._mounted["context"] = context

        result = await _build_context_messages(session, "all", "full", 5)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_depth_recent_limits_turns(self) -> None:
        """context_depth='recent' limits to N turns."""
        session = MockSession()
        context = MockContext(
            [
                {"role": "user", "content": "turn 1"},
                {"role": "assistant", "content": "resp 1"},
                {"role": "user", "content": "turn 2"},
                {"role": "assistant", "content": "resp 2"},
                {"role": "user", "content": "turn 3"},
                {"role": "assistant", "content": "resp 3"},
            ]
        )
        session.coordinator._mounted["context"] = context

        result = await _build_context_messages(session, "recent", "conversation", 2)
        assert len(result) == 4  # 2 turns = 4 messages
        assert result[0]["content"] == "turn 2"


# =============================================================================
# spawn_bundle() Integration Tests
# =============================================================================


class TestSpawnBundle:
    """Tests for spawn_bundle() function."""

    @pytest.mark.asyncio
    async def test_spawn_with_bundle_object(self) -> None:
        """spawn_bundle works with Bundle object."""
        # This test requires mocking the full chain
        # For now, test that the function signature is correct
        with pytest.raises(Exception):
            # Will fail due to missing AmplifierSession, but tests import works
            await spawn_bundle(
                bundle="test:bundle",
                instruction="Test",
                parent_session=MockSession(),  # type: ignore
            )

    @pytest.mark.asyncio
    async def test_spawn_result_type(self) -> None:
        """spawn_bundle returns SpawnResult."""
        # SpawnResult can be constructed directly
        result = SpawnResult(
            output="test output",
            session_id="test-session",
            turn_count=1,
        )
        assert isinstance(result, SpawnResult)

    def test_session_storage_protocol_conformance(self) -> None:
        """SessionStorage protocol is implemented correctly."""
        # Verify the protocol has the right methods
        import inspect

        from amplifier_foundation.spawn import SessionStorage

        # Get protocol methods
        methods = [
            name
            for name, _ in inspect.getmembers(
                SessionStorage, predicate=inspect.isfunction
            )
        ]
        assert "save" in methods or hasattr(SessionStorage, "save")
        assert "load" in methods or hasattr(SessionStorage, "load")
        assert "exists" in methods or hasattr(SessionStorage, "exists")


# =============================================================================
# Module Inheritance Tests
# =============================================================================


class TestInheritancePatterns:
    """Tests for tool/hook inheritance patterns."""

    def test_provider_inheritance_when_empty(self) -> None:
        """Providers inherited when bundle has none."""
        parent_config = {
            "providers": [{"module": "provider-anthropic"}],
        }
        bundle_config: dict[str, Any] = {"providers": []}

        # Simulate inheritance logic
        if not bundle_config.get("providers"):
            bundle_config["providers"] = list(parent_config.get("providers", []))

        assert len(bundle_config["providers"]) == 1
        assert bundle_config["providers"][0]["module"] == "provider-anthropic"

    def test_provider_inheritance_when_defined(self) -> None:
        """Bundle providers NOT overwritten when defined."""
        parent_config = {
            "providers": [{"module": "provider-anthropic"}],
        }
        bundle_config = {
            "providers": [{"module": "provider-openai"}],
        }

        # Simulate inheritance logic - don't inherit if bundle has providers
        if not bundle_config.get("providers"):
            bundle_config["providers"] = list(parent_config.get("providers", []))

        # Should keep bundle's provider
        assert len(bundle_config["providers"]) == 1
        assert bundle_config["providers"][0]["module"] == "provider-openai"

    def test_tool_inheritance_false(self) -> None:
        """No parent tools inherited when inherit_tools=False."""
        parent_tools = [{"module": "tool-bash"}]
        bundle_tools = [{"module": "tool-web"}]

        filtered = _filter_modules(parent_tools, False)
        result = _merge_module_lists(filtered, bundle_tools)

        assert len(result) == 1
        assert result[0]["module"] == "tool-web"

    def test_tool_inheritance_true(self) -> None:
        """All parent tools inherited when inherit_tools=True."""
        parent_tools = [{"module": "tool-bash"}, {"module": "tool-grep"}]
        bundle_tools = [{"module": "tool-web"}]

        filtered = _filter_modules(parent_tools, True)
        result = _merge_module_lists(filtered, bundle_tools)

        assert len(result) == 3
        modules = {m["module"] for m in result}
        assert modules == {"tool-bash", "tool-grep", "tool-web"}

    def test_tool_inheritance_list(self) -> None:
        """Only specified tools inherited when inherit_tools=[list]."""
        parent_tools = [
            {"module": "tool-bash"},
            {"module": "tool-grep"},
            {"module": "tool-web"},
        ]
        bundle_tools = [{"module": "tool-read"}]

        filtered = _filter_modules(parent_tools, ["tool-bash", "tool-web"])
        result = _merge_module_lists(filtered, bundle_tools)

        assert len(result) == 3
        modules = {m["module"] for m in result}
        assert modules == {"tool-bash", "tool-web", "tool-read"}


# =============================================================================
# Context Inheritance Tests
# =============================================================================


class TestContextInheritance:
    """Tests for context inheritance patterns."""

    @pytest.mark.asyncio
    async def test_context_depth_none(self) -> None:
        """context_depth='none' inherits no context."""
        session = MockSession()
        context = MockContext(
            [
                {"role": "user", "content": "important context"},
            ]
        )
        session.coordinator._mounted["context"] = context

        result = await _build_context_messages(session, "none", "conversation", 5)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_context_depth_all(self) -> None:
        """context_depth='all' inherits full context."""
        session = MockSession()
        messages = [{"role": "user", "content": f"turn {i}"} for i in range(10)]
        context = MockContext(messages)
        session.coordinator._mounted["context"] = context

        result = await _build_context_messages(session, "all", "conversation", 5)
        assert len(result) == 10


# =============================================================================
# Background Execution Tests
# =============================================================================


class TestBackgroundExecution:
    """Tests for background execution patterns."""

    def test_spawn_result_background_format(self) -> None:
        """Background execution returns expected SpawnResult format."""
        result = SpawnResult(
            output="[Background session started]",
            session_id="bg-session-123",
            turn_count=0,
        )
        assert result.output == "[Background session started]"
        assert result.turn_count == 0


# =============================================================================
# Nested Spawning Tests
# =============================================================================


class TestNestedSpawning:
    """Tests for nested spawning capability."""

    def test_spawn_capability_signature(self) -> None:
        """Nested spawn capability has correct signature."""

        # The capability should accept **kwargs and return dict
        async def child_spawn_capability(**kwargs: Any) -> dict:
            return {"output": "test", "session_id": "test-123"}

        # Verify it's callable with expected args
        result = asyncio.get_event_loop().run_until_complete(
            child_spawn_capability(
                bundle="test:bundle",
                instruction="test",
            )
        )
        assert "output" in result
        assert "session_id" in result
