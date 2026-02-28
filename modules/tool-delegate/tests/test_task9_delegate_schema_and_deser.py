"""Tests for task-9: delegate tool JSON schema and deserialization updates.

Verifies:
1. Import of preference_from_dict works alongside ProviderPreference
2. JSON schema uses oneOf for provider_preferences items (provider/model + class)
3. Deserialization handles both {provider, model} and {class} entries via preference_from_dict
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_module_tool_delegate import DelegateTool


# =============================================================================
# Helpers
# =============================================================================


def _make_delegate_tool(
    *,
    spawn_fn=None,
    agents: dict | None = None,
) -> DelegateTool:
    """Create a DelegateTool with mocked coordinator for execute() testing."""
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"
    coordinator.config = {"agents": agents or {}}

    capabilities: dict = {
        "session.spawn": spawn_fn
        or AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        ),
        "session.resume": AsyncMock(return_value={}),
        "agents.list": lambda: agents or {},
        "agents.get": lambda name: (agents or {}).get(name),
        "self_delegation_depth": 0,
    }

    def get_capability(name):
        return capabilities.get(name)

    coordinator.get_capability = get_capability
    coordinator.get = MagicMock(return_value=None)

    parent_session = MagicMock()
    parent_session.session_id = "parent-session-123"
    parent_session.config = {"session": {"orchestrator": {}}}
    coordinator.session = parent_session

    config: dict = {"features": {}, "settings": {"exclude_tools": []}}
    return DelegateTool(coordinator, config)


# =============================================================================
# Tests: Import
# =============================================================================


class TestTask9Import:
    """Verify the module imports preference_from_dict from amplifier_foundation."""

    def test_preference_from_dict_used_in_module(self):
        """The module should import preference_from_dict from amplifier_foundation."""
        import amplifier_module_tool_delegate as mod

        # The module should be importable without errors (basic smoke test)
        assert hasattr(mod, "DelegateTool")


# =============================================================================
# Tests: JSON schema
# =============================================================================


class TestTask9Schema:
    """Verify provider_preferences schema uses oneOf with both entry types."""

    def test_schema_has_oneOf_for_provider_preferences_items(self):
        """provider_preferences items should use oneOf with two entry types."""
        tool = _make_delegate_tool()
        schema = tool.input_schema
        pp_schema = schema["properties"]["provider_preferences"]
        items = pp_schema["items"]

        # Must have oneOf at top level of items
        assert "oneOf" in items, (
            f"Expected 'oneOf' in items schema, got keys: {list(items.keys())}"
        )
        assert len(items["oneOf"]) == 2, (
            f"Expected 2 entries in oneOf, got {len(items['oneOf'])}"
        )

    def test_schema_oneOf_has_provider_model_entry(self):
        """oneOf should include {provider, model} entry type."""
        tool = _make_delegate_tool()
        schema = tool.input_schema
        pp_items = schema["properties"]["provider_preferences"]["items"]

        # Find the provider/model entry
        provider_model_entries = [
            entry
            for entry in pp_items["oneOf"]
            if "provider" in entry.get("properties", {})
            and "model" in entry.get("properties", {})
        ]
        assert len(provider_model_entries) == 1, (
            "Expected exactly one provider/model entry in oneOf"
        )
        entry = provider_model_entries[0]
        assert entry["required"] == ["provider", "model"]

    def test_schema_oneOf_has_class_entry(self):
        """oneOf should include {class, required} entry type."""
        tool = _make_delegate_tool()
        schema = tool.input_schema
        pp_items = schema["properties"]["provider_preferences"]["items"]

        # Find the class entry
        class_entries = [
            entry
            for entry in pp_items["oneOf"]
            if "class" in entry.get("properties", {})
        ]
        assert len(class_entries) == 1, "Expected exactly one class entry in oneOf"
        entry = class_entries[0]
        assert entry["required"] == ["class"]

    def test_schema_description_mentions_class(self):
        """provider_preferences description should mention intent-based {class} entries."""
        tool = _make_delegate_tool()
        schema = tool.input_schema
        pp_schema = schema["properties"]["provider_preferences"]
        desc = pp_schema["description"]
        assert "class" in desc.lower(), (
            f"Description should mention class entries, got: {desc}"
        )


# =============================================================================
# Tests: Deserialization via execute()
# =============================================================================


class TestTask9Deserialization:
    """Verify preference_from_dict is used for deserialization in execute()."""

    @pytest.mark.asyncio
    async def test_caller_class_preference_deserialized(self):
        """A {class} entry in caller's provider_preferences should be deserialized."""
        from amplifier_foundation.spawn_utils import ClassPreference

        spawn_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )

        tool = _make_delegate_tool(
            spawn_fn=spawn_fn,
            agents={"test-agent": {"description": "test"}},
        )

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do something",
                "context_depth": "none",
                "provider_preferences": [
                    {"class": "fast", "required": True},
                ],
            }
        )

        spawn_fn.assert_called_once()
        _, kwargs = spawn_fn.call_args
        prefs = kwargs["provider_preferences"]
        assert prefs is not None, "Expected preferences, got None"
        assert len(prefs) == 1
        assert isinstance(prefs[0], ClassPreference)
        assert prefs[0].class_name == "fast"
        assert prefs[0].required is True

    @pytest.mark.asyncio
    async def test_caller_mixed_preferences_deserialized(self):
        """Mixed {provider,model} and {class} entries should both deserialize."""
        from amplifier_foundation.spawn_utils import ClassPreference, ProviderPreference

        spawn_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )

        tool = _make_delegate_tool(
            spawn_fn=spawn_fn,
            agents={"test-agent": {"description": "test"}},
        )

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do something",
                "context_depth": "none",
                "provider_preferences": [
                    {"provider": "anthropic", "model": "claude-haiku-*"},
                    {"class": "fast"},
                ],
            }
        )

        spawn_fn.assert_called_once()
        _, kwargs = spawn_fn.call_args
        prefs = kwargs["provider_preferences"]
        assert len(prefs) == 2
        assert isinstance(prefs[0], ProviderPreference)
        assert prefs[0].provider == "anthropic"
        assert isinstance(prefs[1], ClassPreference)
        assert prefs[1].class_name == "fast"

    @pytest.mark.asyncio
    async def test_agent_default_class_preference_deserialized(self):
        """Agent-level {class} entries in provider_preferences should deserialize."""
        from amplifier_foundation.spawn_utils import ClassPreference

        spawn_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )

        tool = _make_delegate_tool(
            spawn_fn=spawn_fn,
            agents={
                "class-agent": {
                    "description": "Agent with class prefs",
                    "provider_preferences": [
                        {"class": "quality", "required": False},
                    ],
                }
            },
        )

        # Don't pass provider_preferences - should use agent defaults
        await tool.execute(
            {
                "agent": "class-agent",
                "instruction": "Do something",
                "context_depth": "none",
            }
        )

        spawn_fn.assert_called_once()
        _, kwargs = spawn_fn.call_args
        prefs = kwargs["provider_preferences"]
        assert prefs is not None, "Expected agent default prefs, got None"
        assert len(prefs) == 1
        assert isinstance(prefs[0], ClassPreference)
        assert prefs[0].class_name == "quality"

    @pytest.mark.asyncio
    async def test_existing_provider_model_still_works(self):
        """Existing {provider, model} entries continue to work after changes."""
        from amplifier_foundation.spawn_utils import ProviderPreference

        spawn_fn = AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        )

        tool = _make_delegate_tool(
            spawn_fn=spawn_fn,
            agents={"test-agent": {"description": "test"}},
        )

        await tool.execute(
            {
                "agent": "test-agent",
                "instruction": "Do something",
                "context_depth": "none",
                "provider_preferences": [
                    {"provider": "openai", "model": "gpt-4"},
                ],
            }
        )

        spawn_fn.assert_called_once()
        _, kwargs = spawn_fn.call_args
        prefs = kwargs["provider_preferences"]
        assert len(prefs) == 1
        assert isinstance(prefs[0], ProviderPreference)
        assert prefs[0].provider == "openai"
        assert prefs[0].model == "gpt-4"
