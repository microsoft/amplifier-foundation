"""Tests for eager `mention_resolver` capability registration (root fix).

ROOT FIX (late skill-source resolution): PreparedBundle.create_session() and
PreparedBundle.spawn() now register the "mention_resolver" (and
"mention_deduplicator") capability BEFORE session.initialize() /
child_session.initialize() runs, so that any module mounted during
initialize() -- e.g. tool-skills resolving an ``@namespace:skills`` source --
can call ``coordinator.get_capability("mention_resolver")`` at mount time and
get a real resolver instance instead of ``None``.

Before this fix:
- create_session() registered the capability AFTER session.initialize(), and
  only when the bundle had inline instruction/context/pending_context content.
- spawn() never registered the capability for the child session at all, at
  any point.

Contract asserted here (this is the NEW, deliberate contract -- see also
tests/test_mentions_resolved_event.py::TestObservabilityRegistration, which
covers the still-guarded ``mentions:resolved`` observability registration
that intentionally stays conditional):

1. A module mounted during session.initialize()/child_session.initialize()
   observes a non-None, real BaseMentionResolver instance.
2. Registration is unconditional -- it happens even for a bundle with no
   instruction/context (namespace resolution doesn't depend on that content).
3. register_capability("mention_resolver", ...) is called strictly before
   initialize() is awaited.
4. The same holds for spawn()'s child session.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_foundation.bundle import Bundle
from amplifier_foundation.bundle._prepared import BundleModuleResolver
from amplifier_foundation.bundle._prepared import PreparedBundle
from amplifier_foundation.mentions import BaseMentionResolver

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeCoordinator:
    """Coordinator stand-in with REAL capability storage.

    A bare MagicMock's get_capability() always returns the same canned value
    regardless of what register_capability() was called with, which cannot
    prove ordering/visibility. This fake actually stores what is registered
    so a test can prove a module mounted mid-initialize() sees exactly what
    was registered before initialize() was called.
    """

    def __init__(self) -> None:
        self._capabilities: dict[str, Any] = {}
        self.mount = AsyncMock(side_effect=self._record_mount)
        self.register_contributor = MagicMock()
        self.hooks = AsyncMock()
        self.hooks.list_handlers = MagicMock(return_value={})
        self.hooks.register = MagicMock(return_value=MagicMock())
        self.mounted_modules: list[str] = []

    async def _record_mount(
        self, mount_point: str, module: Any, name: str | None = None
    ) -> None:
        self.mounted_modules.append(mount_point)

    def register_capability(self, name: str, value: Any) -> None:
        self._capabilities[name] = value

    def get_capability(self, name: str) -> Any:
        return self._capabilities.get(name)

    def get(self, mount_point: str, name: str | None = None) -> Any:
        return None


class _FakeSession:
    """AmplifierSession stand-in whose initialize() simulates a module mount
    reading get_capability("mention_resolver") -- exactly what tool-skills
    does when resolving an @namespace:skills mount-time source."""

    def __init__(self) -> None:
        self.coordinator = _FakeCoordinator()
        self.observed_resolver_during_init: Any = "NOT_CAPTURED"
        self.execute = AsyncMock(return_value="ok")
        self.cleanup = AsyncMock()
        self.session_id = "fake-session-id"

    async def initialize(self) -> None:
        self.observed_resolver_during_init = self.coordinator.get_capability(
            "mention_resolver"
        )


def _make_prepared(bundle: Bundle) -> PreparedBundle:
    return PreparedBundle(
        mount_plan={},
        bundle=bundle,
        resolver=BundleModuleResolver(module_paths={}),
    )


# ---------------------------------------------------------------------------
# create_session()
# ---------------------------------------------------------------------------


class TestCreateSessionEagerResolver:
    @pytest.mark.asyncio
    async def test_module_mounted_during_initialize_sees_real_resolver(self) -> None:
        """The core proof: a module mounted DURING initialize() sees a real
        resolver, not None."""
        bundle = Bundle(name="test", instruction="Hello")
        prepared = _make_prepared(bundle)
        fake_session = _FakeSession()

        with patch("amplifier_core.AmplifierSession", return_value=fake_session):
            await prepared.create_session()

        assert fake_session.observed_resolver_during_init is not None, (
            "mention_resolver capability was None during session.initialize() -- "
            "modules mounted at this point (e.g. tool-skills) cannot resolve "
            "@namespace:... sources eagerly."
        )
        assert isinstance(
            fake_session.observed_resolver_during_init, BaseMentionResolver
        )

    @pytest.mark.asyncio
    async def test_registered_even_for_bundle_with_no_instruction_or_context(
        self,
    ) -> None:
        """Registration is now UNCONDITIONAL: even a bundle with no
        instruction/context/pending_context gets a real resolver at mount
        time, because bundle namespace resolution does not depend on that
        content."""
        bundle = Bundle(name="empty")
        prepared = _make_prepared(bundle)
        fake_session = _FakeSession()

        with patch("amplifier_core.AmplifierSession", return_value=fake_session):
            await prepared.create_session()

        assert fake_session.observed_resolver_during_init is not None
        assert isinstance(
            fake_session.observed_resolver_during_init, BaseMentionResolver
        )

    @pytest.mark.asyncio
    async def test_capability_registered_before_initialize_is_called(self) -> None:
        """Explicit ordering proof: register_capability("mention_resolver", ...)
        is called strictly before initialize() runs."""
        bundle = Bundle(name="test", instruction="Hi")
        prepared = _make_prepared(bundle)

        call_order: list[str] = []

        class _OrderedCoordinator(_FakeCoordinator):
            def register_capability(self, name: str, value: Any) -> None:
                if name == "mention_resolver":
                    call_order.append("register_mention_resolver")
                super().register_capability(name, value)

        class _OrderedSession(_FakeSession):
            def __init__(self) -> None:
                super().__init__()
                self.coordinator = _OrderedCoordinator()

            async def initialize(self) -> None:
                call_order.append("initialize")
                await super().initialize()

        fake_session = _OrderedSession()
        with patch("amplifier_core.AmplifierSession", return_value=fake_session):
            await prepared.create_session()

        assert "register_mention_resolver" in call_order, (
            "mention_resolver was never registered"
        )
        assert "initialize" in call_order, "initialize() was never called"
        assert call_order.index("register_mention_resolver") < call_order.index(
            "initialize"
        ), f"registration did not happen before initialize(): {call_order}"


# ---------------------------------------------------------------------------
# spawn()
# ---------------------------------------------------------------------------


class TestSpawnEagerResolver:
    @pytest.mark.asyncio
    async def test_child_module_mounted_during_initialize_sees_real_resolver(
        self,
    ) -> None:
        """spawn()'s child session previously never registered mention_resolver
        at all (a strictly worse gap than late registration). This proves the
        child now has a real resolver visible during child_session.initialize().
        """
        parent_bundle = Bundle(name="parent")
        child_bundle = Bundle(name="child", instruction="Do something")
        prepared = _make_prepared(parent_bundle)
        fake_child = _FakeSession()

        with patch("amplifier_core.AmplifierSession", return_value=fake_child):
            await prepared.spawn(child_bundle, "Do something", compose=False)

        assert fake_child.observed_resolver_during_init is not None, (
            "spawn() did not register mention_resolver before "
            "child_session.initialize()"
        )
        assert isinstance(
            fake_child.observed_resolver_during_init, BaseMentionResolver
        )

    @pytest.mark.asyncio
    async def test_child_capability_registered_before_initialize_is_called(
        self,
    ) -> None:
        parent_bundle = Bundle(name="parent")
        child_bundle = Bundle(name="child", instruction="Do something")
        prepared = _make_prepared(parent_bundle)

        call_order: list[str] = []

        class _OrderedCoordinator(_FakeCoordinator):
            def register_capability(self, name: str, value: Any) -> None:
                if name == "mention_resolver":
                    call_order.append("register_mention_resolver")
                super().register_capability(name, value)

        class _OrderedSession(_FakeSession):
            def __init__(self) -> None:
                super().__init__()
                self.coordinator = _OrderedCoordinator()

            async def initialize(self) -> None:
                call_order.append("initialize")
                await super().initialize()

        fake_child = _OrderedSession()
        with patch("amplifier_core.AmplifierSession", return_value=fake_child):
            await prepared.spawn(child_bundle, "Do something", compose=False)

        assert "register_mention_resolver" in call_order
        assert "initialize" in call_order
        assert call_order.index("register_mention_resolver") < call_order.index(
            "initialize"
        ), f"registration did not happen before initialize(): {call_order}"
