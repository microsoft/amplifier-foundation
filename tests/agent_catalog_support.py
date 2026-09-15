"""Shared production-path helpers for agent catalog contract tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_foundation.bundle import Bundle, PreparedBundle
from amplifier_foundation.modules.activator import ModuleActivator
from amplifier_foundation.registry import BundleRegistry
from amplifier_module_tool_delegate import DelegateTool


async def prepare_agent_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, uri: str
) -> tuple[PreparedBundle, Bundle]:
    """Prepare one local agent catalog without fetching its runtime modules."""
    registry = BundleRegistry(home=tmp_path / "home")
    bundle = await registry._load_single(uri, auto_include=False)
    bundle.load_agent_metadata()
    monkeypatch.setattr(ModuleActivator, "activate_all", AsyncMock(return_value={}))
    monkeypatch.setattr(ModuleActivator, "finalize", lambda self: None)
    return await bundle.prepare(install_deps=False), bundle


def render_delegate_catalog(mount_plan: dict[str, Any], *, session_id: str) -> str:
    """Render DelegateTool's public agent catalog from a prepared mount plan."""
    coordinator = MagicMock()
    coordinator.session_id = session_id
    coordinator.config = mount_plan
    coordinator.session_state = {}
    coordinator._tool_dispatch_context = {}
    coordinator.get_capability = lambda _name: None
    coordinator.get = MagicMock(return_value=None)
    return DelegateTool(
        coordinator, {"features": {}, "settings": {"exclude_tools": []}}
    ).description