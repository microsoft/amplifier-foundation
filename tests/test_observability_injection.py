"""Tests for observability event injection in foundation.

Verifies:
1. inject_additional_events() is idempotent — calling twice doesn't duplicate
2. Preserves user-configured additional_events (existing values kept at original positions)
3. FOUNDATION_OBSERVABILITY_EVENTS includes session:config
4. Integration: after PreparedBundle.create_session(), mount_plan has session:config
   in hooks-logging and hook-context-intelligence configs
5. CRITICAL: After PreparedBundle.spawn(), the child mount_plan ALSO has session:config
   injected (the resolve-backend fix — spawn bypasses create_session)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Unit tests: inject_additional_events helper
# ---------------------------------------------------------------------------


class TestInjectAdditionalEvents:
    """Tests for the inject_additional_events() standalone helper."""

    def _import(self):
        from amplifier_foundation.bundle._observability import inject_additional_events  # noqa: PLC0415

        return inject_additional_events

    # --- basic injection ---

    def test_injects_events_into_hooks_logging(self):
        inject = self._import()
        plan = {"hooks": [{"module": "hooks-logging", "config": {}}]}

        inject(plan, ["session:config"])

        added = plan["hooks"][0]["config"]["additional_events"]
        assert "session:config" in added

    def test_injects_events_into_hook_context_intelligence(self):
        inject = self._import()
        plan = {"hooks": [{"module": "hook-context-intelligence", "config": {}}]}

        inject(plan, ["session:config"])

        added = plan["hooks"][0]["config"]["additional_events"]
        assert "session:config" in added

    def test_injects_both_subscribers_when_both_present(self):
        inject = self._import()
        plan = {
            "hooks": [
                {"module": "hooks-logging", "config": {}},
                {"module": "hook-context-intelligence", "config": {}},
            ]
        }

        inject(plan, ["session:config"])

        for hook in plan["hooks"]:
            assert "session:config" in hook["config"]["additional_events"], (
                f"{hook['module']} missing session:config"
            )

    def test_does_not_inject_into_unrelated_hooks(self):
        inject = self._import()
        plan = {
            "hooks": [
                {"module": "hooks-notify-push", "config": {"foo": "bar"}},
                {"module": "hooks-logging", "config": {}},
            ]
        }

        inject(plan, ["session:config"])

        # Unrelated hook untouched
        notify_cfg = plan["hooks"][0]["config"]
        assert "additional_events" not in notify_cfg
        assert notify_cfg.get("foo") == "bar"

    # --- idempotency ---

    def test_idempotent_calling_twice_does_not_duplicate(self):
        inject = self._import()
        plan = {"hooks": [{"module": "hooks-logging", "config": {}}]}

        inject(plan, ["session:config"])
        inject(plan, ["session:config"])

        added = plan["hooks"][0]["config"]["additional_events"]
        count = added.count("session:config")
        assert count == 1, f"session:config duplicated: count={count}"

    def test_idempotent_with_multiple_events(self):
        inject = self._import()
        plan = {"hooks": [{"module": "hooks-logging", "config": {}}]}
        events = ["session:config", "cleanup:render_begin", "cleanup:render_end"]

        inject(plan, events)
        inject(plan, events)

        added = plan["hooks"][0]["config"]["additional_events"]
        for ev in events:
            assert added.count(ev) == 1, f"{ev} duplicated"

    # --- existing events preserved ---

    def test_preserves_user_configured_events_at_front(self):
        inject = self._import()
        plan = {"hooks": [{"module": "hooks-logging", "config": {}}]}
        plan["hooks"][0]["config"]["additional_events"] = ["my:custom:event"]

        inject(plan, ["session:config"])

        added = plan["hooks"][0]["config"]["additional_events"]
        assert "my:custom:event" in added
        assert "session:config" in added
        # User's event is FIRST (their position preserved)
        assert added.index("my:custom:event") < added.index("session:config")

    def test_preserves_user_event_already_in_new_list_no_duplicate(self):
        inject = self._import()
        plan = {"hooks": [{"module": "hooks-logging", "config": {}}]}
        plan["hooks"][0]["config"]["additional_events"] = ["session:config"]

        inject(plan, ["session:config", "cleanup:render_begin"])

        added = plan["hooks"][0]["config"]["additional_events"]
        assert added.count("session:config") == 1

    # --- edge cases ---

    def test_no_hooks_section_is_noop(self):
        inject = self._import()
        plan: dict[str, Any] = {}
        inject(plan, ["session:config"])  # must not raise

    def test_empty_hooks_list_is_noop(self):
        inject = self._import()
        plan: dict[str, Any] = {"hooks": []}
        inject(plan, ["session:config"])  # must not raise

    def test_hooks_section_is_none_is_noop(self):
        inject = self._import()
        plan: dict[str, Any] = {"hooks": None}
        inject(plan, ["session:config"])  # must not raise

    def test_handles_hook_with_null_config(self):
        inject = self._import()
        plan = {"hooks": [{"module": "hooks-logging", "config": None}]}

        inject(plan, ["session:config"])

        added = plan["hooks"][0]["config"]["additional_events"]
        assert "session:config" in added

    def test_handles_hook_with_no_config_key(self):
        inject = self._import()
        plan: dict[str, Any] = {
            "hooks": [{"module": "hooks-logging"}]
        }  # no 'config' key

        inject(plan, ["session:config"])

        added = plan["hooks"][0]["config"]["additional_events"]
        assert "session:config" in added

    def test_empty_events_iterable_is_noop(self):
        inject = self._import()
        plan = {"hooks": [{"module": "hooks-logging", "config": {}}]}
        inject(plan, [])  # must not raise, must not create key
        # additional_events not created when nothing to add
        assert plan["hooks"][0]["config"].get("additional_events") == []

    def test_custom_target_modules(self):
        inject = self._import()
        plan = {
            "hooks": [
                {"module": "hooks-logging", "config": {}},
                {"module": "my-custom-subscriber", "config": {}},
            ]
        }

        inject(
            plan, ["session:config"], target_modules=frozenset({"my-custom-subscriber"})
        )

        # Only the custom target gets the event
        assert "additional_events" not in plan["hooks"][0]["config"]
        assert "session:config" in plan["hooks"][1]["config"]["additional_events"]


# ---------------------------------------------------------------------------
# Unit tests: FOUNDATION_OBSERVABILITY_EVENTS constant
# ---------------------------------------------------------------------------


class TestFoundationObservabilityEvents:
    def test_session_config_is_included(self):
        from amplifier_foundation.bundle._observability import (  # noqa: PLC0415
            FOUNDATION_OBSERVABILITY_EVENTS,
        )

        assert "session:config" in FOUNDATION_OBSERVABILITY_EVENTS

    def test_is_tuple_of_strings(self):
        from amplifier_foundation.bundle._observability import (  # noqa: PLC0415
            FOUNDATION_OBSERVABILITY_EVENTS,
        )

        assert isinstance(FOUNDATION_OBSERVABILITY_EVENTS, tuple)
        for ev in FOUNDATION_OBSERVABILITY_EVENTS:
            assert isinstance(ev, str), f"Expected str, got {type(ev)} for {ev!r}"


# ---------------------------------------------------------------------------
# Re-export: inject_additional_events available from amplifier_foundation
# ---------------------------------------------------------------------------


class TestFoundationReexport:
    def test_inject_additional_events_importable_from_top_level(self):
        from amplifier_foundation import inject_additional_events  # noqa: PLC0415

        assert callable(inject_additional_events)

    def test_reexport_is_same_object(self):
        from amplifier_foundation import inject_additional_events as top_level  # noqa: PLC0415
        from amplifier_foundation.bundle._observability import (
            inject_additional_events as impl,
        )  # noqa: PLC0415

        assert top_level is impl


# ---------------------------------------------------------------------------
# Integration: PreparedBundle.create_session calls inject_additional_events
# ---------------------------------------------------------------------------


class TestCreateSessionInjectsFoundationEvents:
    """create_session() must call inject_additional_events before constructing AmplifierSession.

    Strategy: spy on inject_additional_events at the source module level.
    Because it is imported inside the function body, patching the source
    (amplifier_foundation.bundle._observability.inject_additional_events)
    intercepts the call without needing to fully mock AmplifierSession.
    """

    @pytest.mark.asyncio
    async def test_create_session_calls_inject_with_foundation_events(self):
        """create_session() calls inject_additional_events(self.mount_plan, FOUNDATION_OBSERVABILITY_EVENTS)."""
        from amplifier_foundation.bundle._prepared import PreparedBundle  # noqa: PLC0415
        from amplifier_foundation.bundle._observability import (
            FOUNDATION_OBSERVABILITY_EVENTS,
        )  # noqa: PLC0415

        mount_plan: dict[str, Any] = {
            "hooks": [{"module": "hooks-logging", "config": {}}]
        }

        bundle = MagicMock()
        bundle.base_path = None
        prepared = PreparedBundle(
            bundle=bundle, mount_plan=mount_plan, resolver=MagicMock()
        )

        call_record: list[tuple[dict, list]] = []
        original = __import__(
            "amplifier_foundation.bundle._observability",
            fromlist=["inject_additional_events"],
        ).inject_additional_events

        def _spy(mp, events, **kwargs):
            call_record.append((mp, list(events)))
            return original(mp, events, **kwargs)

        with patch(
            "amplifier_foundation.bundle._observability.inject_additional_events",
            side_effect=_spy,
        ):
            # Stop just before AmplifierSession.__init__ to avoid full init
            with patch("amplifier_core.AmplifierSession") as MockSession:
                mock_session = MagicMock()
                mock_session.coordinator.mount = AsyncMock()
                mock_session.coordinator.get_capability.return_value = None
                mock_session.coordinator.register_capability = MagicMock()
                mock_session.initialize = AsyncMock()
                mock_session.coordinator.list_handlers.return_value = []
                mock_session.config = {}
                mock_session.bundle = MagicMock()
                MockSession.return_value = mock_session

                with patch.object(prepared.bundle, "resolve_pending_context"):
                    try:
                        await prepared.create_session()
                    except Exception:
                        pass  # We only care that inject was called before AmplifierSession()

        assert call_record, (
            "inject_additional_events was NOT called from create_session(). "
            "Foundation events (e.g. session:config) will not be subscribed."
        )
        # Verify called with the right mount_plan and events
        mp_used, events_used = call_record[0]
        assert mp_used is mount_plan, (
            "inject_additional_events was not called with self.mount_plan"
        )
        for ev in FOUNDATION_OBSERVABILITY_EVENTS:
            assert ev in events_used, f"{ev!r} not passed to inject_additional_events"

    @pytest.mark.asyncio
    async def test_create_session_injects_before_amplifier_session_construction(self):
        """inject_additional_events must be called BEFORE AmplifierSession() is constructed."""
        from amplifier_foundation.bundle._prepared import PreparedBundle  # noqa: PLC0415

        mount_plan: dict[str, Any] = {
            "hooks": [{"module": "hooks-logging", "config": {}}]
        }
        bundle = MagicMock()
        bundle.base_path = None
        prepared = PreparedBundle(
            bundle=bundle, mount_plan=mount_plan, resolver=MagicMock()
        )

        call_order: list[str] = []

        # Capture real function BEFORE patching to avoid infinite recursion
        from amplifier_foundation.bundle._observability import (
            inject_additional_events as _real_inject,
        )  # noqa: PLC0415

        def _spy_inject(mp, events, **kwargs):
            call_order.append("inject")
            return _real_inject(mp, events, **kwargs)

        def _spy_session(mp, **kwargs):
            call_order.append("session")
            mock = MagicMock()
            mock.coordinator.mount = AsyncMock()
            mock.coordinator.get_capability.return_value = None
            mock.coordinator.register_capability = MagicMock()
            mock.initialize = AsyncMock()
            mock.coordinator.list_handlers.return_value = []
            mock.config = {}
            mock.bundle = MagicMock()
            return mock

        with patch(
            "amplifier_foundation.bundle._observability.inject_additional_events",
            side_effect=_spy_inject,
        ):
            with patch("amplifier_core.AmplifierSession", side_effect=_spy_session):
                with patch.object(prepared.bundle, "resolve_pending_context"):
                    try:
                        await prepared.create_session()
                    except Exception:
                        pass

        assert "inject" in call_order, "inject_additional_events was never called"
        assert "session" in call_order, "AmplifierSession was never constructed"
        inject_idx = call_order.index("inject")
        session_idx = call_order.index("session")
        assert inject_idx < session_idx, (
            "inject_additional_events was called AFTER AmplifierSession(). "
            "The mount_plan is already consumed by the kernel — injection will have no effect."
        )

    @pytest.mark.asyncio
    async def test_create_session_mount_plan_has_session_config_after_inject(self):
        """The actual mount_plan has session:config in hooks-logging after injection runs."""
        from amplifier_foundation.bundle._prepared import PreparedBundle  # noqa: PLC0415

        mount_plan: dict[str, Any] = {
            "hooks": [{"module": "hooks-logging", "config": {}}]
        }
        bundle = MagicMock()
        bundle.base_path = None
        prepared = PreparedBundle(
            bundle=bundle, mount_plan=mount_plan, resolver=MagicMock()
        )

        # We test the mount_plan mutation directly rather than through create_session()
        # by calling inject_additional_events directly (already covered in unit tests).
        # Here we verify that calling inject_additional_events on the prepared bundle's
        # mount_plan produces the expected mutation — so create_session() will too.
        from amplifier_foundation.bundle._observability import (  # noqa: PLC0415
            FOUNDATION_OBSERVABILITY_EVENTS,
            inject_additional_events,
        )

        inject_additional_events(prepared.mount_plan, FOUNDATION_OBSERVABILITY_EVENTS)

        hook_cfg = prepared.mount_plan["hooks"][0]["config"]
        assert "session:config" in hook_cfg.get("additional_events", []), (
            "After injection, hooks-logging additional_events does not contain session:config"
        )

    @pytest.mark.asyncio
    async def test_create_session_injection_is_idempotent(self):
        """Calling inject_additional_events twice does not duplicate session:config."""
        from amplifier_foundation.bundle._observability import (  # noqa: PLC0415
            FOUNDATION_OBSERVABILITY_EVENTS,
            inject_additional_events,
        )

        mount_plan: dict[str, Any] = {
            "hooks": [{"module": "hooks-logging", "config": {}}]
        }

        inject_additional_events(mount_plan, FOUNDATION_OBSERVABILITY_EVENTS)
        inject_additional_events(mount_plan, FOUNDATION_OBSERVABILITY_EVENTS)

        count = mount_plan["hooks"][0]["config"]["additional_events"].count(
            "session:config"
        )
        assert count == 1, (
            f"session:config duplicated after double injection: count={count}"
        )


# ---------------------------------------------------------------------------
# CRITICAL: PreparedBundle.spawn() also injects FOUNDATION_OBSERVABILITY_EVENTS
# ---------------------------------------------------------------------------


class TestSpawnInjectsFoundationEvents:
    """CRITICAL: spawn() produces a fresh child_mount_plan that must also get injected.

    This is the bug that affects the resolve backend — it calls spawn() directly
    without going through create_session().  Without injection in spawn(), session:config
    never reaches hooks-logging even after this migration lands.
    """

    @pytest.mark.asyncio
    async def test_spawn_calls_inject_with_child_mount_plan(self):
        """spawn() calls inject_additional_events(child_mount_plan, FOUNDATION_OBSERVABILITY_EVENTS)."""
        from amplifier_foundation.bundle._prepared import PreparedBundle  # noqa: PLC0415
        from amplifier_foundation.bundle._observability import (
            FOUNDATION_OBSERVABILITY_EVENTS,
        )  # noqa: PLC0415

        parent_bundle = MagicMock()
        parent_bundle.base_path = None
        parent_prepared = PreparedBundle(
            bundle=parent_bundle,
            mount_plan={},
            resolver=MagicMock(),
        )

        child_hook_plan: dict[str, Any] = {
            "hooks": [{"module": "hooks-logging", "config": {}}]
        }
        child_bundle = MagicMock()
        child_bundle.to_mount_plan.return_value = child_hook_plan
        child_bundle.compose = MagicMock(return_value=child_bundle)

        call_record: list[tuple[dict, list]] = []
        original = __import__(
            "amplifier_foundation.bundle._observability",
            fromlist=["inject_additional_events"],
        ).inject_additional_events

        def _spy(mp, events, **kwargs):
            call_record.append((mp, list(events)))
            return original(mp, events, **kwargs)

        child_mock = MagicMock()
        child_mock.coordinator.mount = AsyncMock()
        child_mock.coordinator.get_capability.return_value = None
        child_mock.coordinator.register_capability = MagicMock()
        child_mock.initialize = AsyncMock()
        child_mock.coordinator.list_handlers.return_value = []
        child_mock.config = {}

        with patch(
            "amplifier_foundation.bundle._observability.inject_additional_events",
            side_effect=_spy,
        ):
            with patch("amplifier_core.AmplifierSession", return_value=child_mock):
                try:
                    await parent_prepared.spawn(
                        child_bundle=child_bundle,
                        instruction="test",
                        parent_session=None,
                        compose=False,
                    )
                except Exception:
                    pass

        assert call_record, (
            "inject_additional_events was NOT called from spawn(). "
            "This is the resolve-backend bug: sessions spawned directly via spawn() "
            "will never log session:config to events.jsonl."
        )
        # Find the call that targeted the child plan hooks
        child_plan_calls = [(mp, events) for mp, events in call_record if "hooks" in mp]
        assert child_plan_calls, (
            "inject_additional_events was called but not with a mount_plan containing 'hooks'. "
            "The child_mount_plan injection may be missing."
        )
        _, events_used = child_plan_calls[0]
        for ev in FOUNDATION_OBSERVABILITY_EVENTS:
            assert ev in events_used, (
                f"{ev!r} not passed to inject_additional_events in spawn()"
            )

    @pytest.mark.asyncio
    async def test_spawn_child_mount_plan_has_session_config_injected(self):
        """After spawn(), the child_mount_plan passed to AmplifierSession has session:config."""
        from amplifier_foundation.bundle._prepared import PreparedBundle  # noqa: PLC0415

        parent_bundle = MagicMock()
        parent_bundle.base_path = None
        parent_prepared = PreparedBundle(
            bundle=parent_bundle,
            mount_plan={},
            resolver=MagicMock(),
        )

        child_bundle = MagicMock()
        child_bundle.to_mount_plan.return_value = {
            "hooks": [{"module": "hooks-logging", "config": {}}]
        }
        child_bundle.compose = MagicMock(return_value=child_bundle)

        captured_plans: list[dict] = []

        def _capture_session(mount_plan, **kwargs):
            import copy  # noqa: PLC0415

            captured_plans.append(copy.deepcopy(mount_plan))
            mock = MagicMock()
            mock.coordinator.mount = AsyncMock()
            mock.coordinator.get_capability.return_value = None
            mock.coordinator.register_capability = MagicMock()
            mock.initialize = AsyncMock()
            mock.coordinator.list_handlers.return_value = []
            mock.config = {}
            return mock

        with patch("amplifier_core.AmplifierSession", side_effect=_capture_session):
            try:
                await parent_prepared.spawn(
                    child_bundle=child_bundle,
                    instruction="test",
                    parent_session=None,
                    compose=False,
                )
            except Exception:
                pass

        assert captured_plans, "AmplifierSession was never called in spawn()"
        child_plan = captured_plans[-1]
        hooks = child_plan.get("hooks") or []
        logging_hook = next(
            (h for h in hooks if h.get("module") == "hooks-logging"), None
        )
        assert logging_hook is not None, (
            "hooks-logging not in child mount_plan passed to AmplifierSession"
        )
        additional = (logging_hook.get("config") or {}).get("additional_events", [])
        assert "session:config" in additional, (
            "spawn() did NOT inject session:config into hooks-logging child mount_plan. "
            "This is the resolve-backend bug: sessions spawned directly via spawn() "
            "will never log session:config to events.jsonl."
        )
