"""Public delegation configurations leave call/time limits opt-in."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from amplifier_module_tool_delegate import DelegateTool
from tests.agent_catalog_support import prepare_agent_catalog


ROOT = Path(__file__).parent.parent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source", ["bundles/anchors/bundle.md", "behaviors/agents.yaml"]
)
async def test_public_delegate_configs_do_not_enable_limits(tmp_path, monkeypatch, source):
    prepared, _bundle = await prepare_agent_catalog(
        tmp_path, monkeypatch, (ROOT / source).as_uri()
    )
    entry = next(
        t for t in prepared.mount_plan["tools"] if t["module"] == "tool-delegate"
    )
    settings = entry["config"]["settings"]
    assert settings["timeout"] is None
    assert settings["max_llm_calls"] is None
    coordinator = MagicMock()
    coordinator.config = prepared.mount_plan
    coordinator.session_state = {}
    coordinator._tool_dispatch_context = {}
    tool = DelegateTool(coordinator, entry["config"])
    assert tool.timeout is None
    assert tool.max_llm_calls is None


def test_call_budget_guidance_is_opt_in_and_describes_inheritance():
    tool = DelegateTool(MagicMock(), {})
    description = tool.input_schema["properties"]["max_llm_calls"]["description"]
    assert "Omit unless the user requests a cap" in description
    assert "does not clear inherited orchestrator limits" in description
    assert "Raise for known-large tasks" not in description