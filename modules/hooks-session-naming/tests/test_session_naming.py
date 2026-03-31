"""Tests for hooks-session-naming async behavior and model role/provider preferences."""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_module_hooks_session_naming import (
    SessionNamingConfig,
    SessionNamingHook,
)


# =============================================================================
# Shared helpers
# =============================================================================


def _make_mock_provider() -> MagicMock:
    """Return a mock provider whose complete() returns a text response."""
    provider = MagicMock()
    text_block = MagicMock()
    text_block.text = '{"action": "set", "name": "Test Session", "description": "A test."}'
    response = MagicMock()
    response.content = [text_block]
    provider.complete = AsyncMock(return_value=response)
    return provider


def _make_coordinator(
    *,
    providers: dict | None = None,
    session_state: dict | None = None,
) -> MagicMock:
    """Return a coordinator mock wired for session-naming tests."""
    coordinator = MagicMock()
    coordinator.session_state = session_state or {}
    coordinator.hooks = MagicMock()
    coordinator.hooks.emit = AsyncMock()
    coordinator.hooks.register = MagicMock()
    coordinator.mount_points = MagicMock()
    coordinator.mount_points.get = MagicMock(return_value=None)

    _providers = providers if providers is not None else {"provider-1": _make_mock_provider()}
    coordinator.get = MagicMock(
        side_effect=lambda key: _providers if key == "providers" else None
    )
    return coordinator


def _make_hook(
    *,
    providers: dict | None = None,
    session_state: dict | None = None,
    model_role: str | None = None,
    provider_preferences: list[dict] | None = None,
    initial_trigger_turn: int = 2,
) -> SessionNamingHook:
    """Return a SessionNamingHook with mocked coordinator."""
    coordinator = _make_coordinator(
        providers=providers,
        session_state=session_state,
    )
    config = SessionNamingConfig(
        initial_trigger_turn=initial_trigger_turn,
        model_role=model_role,
        provider_preferences=provider_preferences,
    )
    return SessionNamingHook(coordinator, config)


def _install_mock_routing(
    *,
    resolve_fn=None,
    find_fn=None,
) -> callable:
    """Inject a mock amplifier_module_hooks_routing.resolver into sys.modules.

    Returns a cleanup() callable — always call it in a finally block.

    Usage:
        cleanup = _install_mock_routing(resolve_fn=AsyncMock(...))
        try:
            ...
        finally:
            cleanup()
    """
    mock_resolver_mod = types.ModuleType("amplifier_module_hooks_routing.resolver")
    if resolve_fn is not None:
        mock_resolver_mod.resolve_model_role = resolve_fn
    if find_fn is not None:
        mock_resolver_mod.find_provider_by_type = find_fn

    mock_routing_mod = types.ModuleType("amplifier_module_hooks_routing")

    originals: dict = {}
    for mod_name in (
        "amplifier_module_hooks_routing",
        "amplifier_module_hooks_routing.resolver",
    ):
        if mod_name in sys.modules:
            originals[mod_name] = sys.modules[mod_name]

    sys.modules["amplifier_module_hooks_routing"] = mock_routing_mod
    sys.modules["amplifier_module_hooks_routing.resolver"] = mock_resolver_mod

    def cleanup() -> None:
        for mod_name in (
            "amplifier_module_hooks_routing",
            "amplifier_module_hooks_routing.resolver",
        ):
            if mod_name in originals:
                sys.modules[mod_name] = originals[mod_name]
            elif mod_name in sys.modules:
                del sys.modules[mod_name]

    return cleanup
