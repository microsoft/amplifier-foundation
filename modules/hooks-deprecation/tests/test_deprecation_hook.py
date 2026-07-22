"""Tests for the deprecation hook module."""

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_module_hooks_deprecation import (
    DeprecationConfig,
    DeprecationHook,
    build_user_message,
    build_warning_text,
    effective_severity,
    find_source_files,
    mount,
    on_session_ready,
    parse_deprecation_configs,
)


# — Config Tests —


class TestDeprecationConfig:
    """Tests for DeprecationConfig dataclass."""

    def test_minimal_config(self):
        """Only required fields: bundle_name and message."""
        cfg = DeprecationConfig.from_dict(
            {
                "bundle_name": "lsp-python",
                "message": "lsp-python is deprecated",
            }
        )
        assert cfg.bundle_name == "lsp-python"
        assert cfg.message == "lsp-python is deprecated"
        assert cfg.replacement is None
        assert cfg.migration is None
        assert cfg.severity == "warning"
        assert cfg.sunset_date is None

    def test_full_config(self):
        """All fields provided."""
        cfg = DeprecationConfig.from_dict(
            {
                "bundle_name": "lsp-python",
                "replacement": "python-dev",
                "message": "lsp-python is deprecated",
                "migration": "Update your includes:\n- old\n+ new",
                "severity": "info",
                "sunset_date": "2026-06-01",
            }
        )
        assert cfg.bundle_name == "lsp-python"
        assert cfg.replacement == "python-dev"
        assert cfg.migration == "Update your includes:\n- old\n+ new"
        assert cfg.severity == "info"
        assert cfg.sunset_date == date(2026, 6, 1)

    def test_missing_bundle_name_raises(self):
        """bundle_name is required."""
        with pytest.raises(ValueError, match="bundle_name"):
            DeprecationConfig.from_dict({"message": "deprecated"})

    def test_missing_message_raises(self):
        """message is required."""
        with pytest.raises(ValueError, match="message"):
            DeprecationConfig.from_dict({"bundle_name": "lsp-python"})

    def test_invalid_severity_raises(self):
        """severity must be 'warning' or 'info'."""
        with pytest.raises(ValueError, match="severity"):
            DeprecationConfig.from_dict(
                {
                    "bundle_name": "lsp-python",
                    "message": "deprecated",
                    "severity": "error",
                }
            )

    def test_invalid_sunset_date_raises(self):
        """sunset_date must be a valid YYYY-MM-DD string."""
        with pytest.raises(ValueError, match="sunset_date"):
            DeprecationConfig.from_dict(
                {
                    "bundle_name": "lsp-python",
                    "message": "deprecated",
                    "sunset_date": "not-a-date",
                }
            )

    def test_severity_defaults_to_warning(self):
        """severity defaults to 'warning' when not provided."""
        cfg = DeprecationConfig.from_dict(
            {
                "bundle_name": "lsp-python",
                "message": "deprecated",
            }
        )
        assert cfg.severity == "warning"

    def test_require_evidence_defaults_to_false(self):
        """require_evidence defaults to False when not provided in config dict."""
        cfg = DeprecationConfig.from_dict(
            {
                "bundle_name": "lsp-python",
                "message": "deprecated",
            }
        )
        assert cfg.require_evidence is False


# — Source File Scanner Tests —


class TestFindSourceFiles:
    """Tests for find_source_files() best-effort scanner."""

    def test_finds_yaml_with_bundle_name(self, tmp_path):
        """Finds YAML files containing the bundle name string."""
        amp_dir = tmp_path / ".amplifier"
        amp_dir.mkdir()
        settings = amp_dir / "settings.yaml"
        settings.write_text(
            "includes:\n"
            "  - bundle: git+https://github.com/microsoft/amplifier-bundle-lsp-python@main\n"
        )
        results = find_source_files("lsp-python", [tmp_path])
        assert len(results) == 1
        assert results[0] == str(settings)

    def test_ignores_yaml_without_bundle_name(self, tmp_path):
        """Ignores YAML files that don't contain the bundle name."""
        amp_dir = tmp_path / ".amplifier"
        amp_dir.mkdir()
        settings = amp_dir / "settings.yaml"
        settings.write_text(
            "includes:\n"
            "  - bundle: git+https://github.com/microsoft/amplifier-bundle-python-dev@main\n"
        )
        results = find_source_files("lsp-python", [tmp_path])
        assert results == []

    def test_scans_multiple_directories(self, tmp_path):
        """Scans across multiple base directories."""
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        for d in [dir_a, dir_b]:
            amp = d / ".amplifier"
            amp.mkdir(parents=True)
            (amp / "settings.yaml").write_text("includes: lsp-python\n")

        results = find_source_files("lsp-python", [dir_a, dir_b])
        assert len(results) == 2

    def test_handles_missing_amplifier_dir(self, tmp_path):
        """Doesn't crash if .amplifier/ doesn't exist."""
        results = find_source_files("lsp-python", [tmp_path])
        assert results == []

    def test_scans_nested_yaml_files(self, tmp_path):
        """Finds bundle references in nested .amplifier/ subdirectories."""
        amp_dir = tmp_path / ".amplifier" / "bundles"
        amp_dir.mkdir(parents=True)
        config = amp_dir / "my-config.yaml"
        config.write_text("- bundle: lsp-python\n")
        results = find_source_files("lsp-python", [tmp_path])
        assert len(results) == 1
        assert results[0] == str(config)

    def test_handles_unreadable_files(self, tmp_path):
        """Gracefully skips files that can't be read."""
        amp_dir = tmp_path / ".amplifier"
        amp_dir.mkdir()
        bad_file = amp_dir / "binary.yaml"
        bad_file.write_bytes(b"\x80\x81\x82\x83")  # Invalid UTF-8
        results = find_source_files("lsp-python", [tmp_path])
        assert results == []

    def test_excludes_cache_directory_matches(self, tmp_path):
        """Cached/resolved artifacts under a 'cache' dir are excluded (#344).

        The tombstone's own carrier config is cached on every install and
        would otherwise always self-match, defeating require_evidence gating.
        """
        amp_dir = tmp_path / ".amplifier"
        amp_dir.mkdir()
        real_file = amp_dir / "real.yaml"
        real_file.write_text("includes: lsp-python\n")

        cache_dir = amp_dir / "cache" / "dep"
        cache_dir.mkdir(parents=True)
        cached_file = cache_dir / "cached.yaml"
        cached_file.write_text("includes: lsp-python\n")

        results = find_source_files("lsp-python", [tmp_path])

        assert results == [str(real_file)]
        assert not any("cache" in Path(p).parts for p in results)

    def test_cache_in_ancestor_of_base_dir_does_not_suppress(self, tmp_path):
        """A 'cache' segment ABOVE .amplifier must not suppress real matches (#344).

        Only artifacts under .amplifier/cache/ are resolved copies. A user whose
        project simply lives beneath some 'cache/' ancestor still has genuine
        authored config, and the scan must find it. Guards against scoping the
        exclusion to the full absolute path instead of the .amplifier-relative one.
        """
        base = tmp_path / "cache" / "myproject"
        amp_dir = base / ".amplifier"
        amp_dir.mkdir(parents=True)
        real_file = amp_dir / "settings.yaml"
        real_file.write_text("includes: lsp-python\n")

        results = find_source_files("lsp-python", [base])

        assert results == [str(real_file)]


# — Sunset Escalation Tests —


class TestEffectiveSeverity:
    """Tests for sunset date severity escalation."""

    def test_no_sunset_returns_configured_severity(self):
        """Without sunset_date, return configured severity unchanged."""
        cfg = DeprecationConfig.from_dict(
            {
                "bundle_name": "lsp-python",
                "message": "deprecated",
                "severity": "info",
            }
        )
        assert effective_severity(cfg) == "info"

    def test_future_sunset_returns_configured_severity(self):
        """Sunset date in the future — no escalation."""
        future = date.today() + timedelta(days=30)
        cfg = DeprecationConfig.from_dict(
            {
                "bundle_name": "lsp-python",
                "message": "deprecated",
                "severity": "info",
                "sunset_date": future.isoformat(),
            }
        )
        assert effective_severity(cfg) == "info"

    def test_past_sunset_escalates_info_to_warning(self):
        """Past sunset date escalates info → warning."""
        past = date.today() - timedelta(days=1)
        cfg = DeprecationConfig.from_dict(
            {
                "bundle_name": "lsp-python",
                "message": "deprecated",
                "severity": "info",
                "sunset_date": past.isoformat(),
            }
        )
        assert effective_severity(cfg) == "warning"

    def test_past_sunset_escalates_warning_to_urgent(self):
        """Past sunset date with severity=warning returns 'urgent'."""
        past = date.today() - timedelta(days=1)
        cfg = DeprecationConfig.from_dict(
            {
                "bundle_name": "lsp-python",
                "message": "deprecated",
                "severity": "warning",
                "sunset_date": past.isoformat(),
            }
        )
        assert effective_severity(cfg) == "urgent"

    def test_today_is_not_past(self):
        """Sunset date equal to today is NOT past — no escalation."""
        today = date.today()
        cfg = DeprecationConfig.from_dict(
            {
                "bundle_name": "lsp-python",
                "message": "deprecated",
                "severity": "info",
                "sunset_date": today.isoformat(),
            }
        )
        assert effective_severity(cfg) == "info"


# — Warning Text Builder Tests —


class TestBuildWarningText:
    """Tests for AI context injection text."""

    def test_minimal_warning(self):
        """Minimal config produces basic warning block."""
        cfg = DeprecationConfig.from_dict(
            {
                "bundle_name": "lsp-python",
                "message": "lsp-python is deprecated",
            }
        )
        text = build_warning_text(cfg, severity="warning", source_files=[])
        assert "DEPRECATION WARNING" in text
        assert "lsp-python" in text
        assert "lsp-python is deprecated" in text

    def test_includes_replacement(self):
        """Replacement bundle is mentioned when configured."""
        cfg = DeprecationConfig.from_dict(
            {
                "bundle_name": "lsp-python",
                "message": "deprecated",
                "replacement": "python-dev",
            }
        )
        text = build_warning_text(cfg, severity="warning", source_files=[])
        assert "python-dev" in text

    def test_includes_migration_instructions(self):
        """Migration text is included when configured."""
        cfg = DeprecationConfig.from_dict(
            {
                "bundle_name": "lsp-python",
                "message": "deprecated",
                "migration": "- old\n+ new",
            }
        )
        text = build_warning_text(cfg, severity="warning", source_files=[])
        assert "- old" in text
        assert "+ new" in text

    def test_includes_source_files(self):
        """Source file paths are included when found."""
        cfg = DeprecationConfig.from_dict(
            {
                "bundle_name": "lsp-python",
                "message": "deprecated",
            }
        )
        text = build_warning_text(
            cfg,
            severity="warning",
            source_files=["/home/user/.amplifier/settings.yaml"],
        )
        assert "/home/user/.amplifier/settings.yaml" in text

    def test_urgent_severity_shows_urgent_prefix(self):
        """Urgent severity adds URGENT prefix."""
        cfg = DeprecationConfig.from_dict(
            {
                "bundle_name": "lsp-python",
                "message": "deprecated",
            }
        )
        text = build_warning_text(cfg, severity="urgent", source_files=[])
        assert "URGENT" in text

    def test_includes_sunset_date(self):
        """Sunset date is mentioned when configured."""
        cfg = DeprecationConfig.from_dict(
            {
                "bundle_name": "lsp-python",
                "message": "deprecated",
                "sunset_date": "2026-06-01",
            }
        )
        text = build_warning_text(cfg, severity="warning", source_files=[])
        assert "2026-06-01" in text


# — User Message Builder Tests —


class TestBuildUserMessage:
    """Tests for user-facing warning message."""

    def test_minimal_user_message(self):
        """Basic user message includes bundle name and message."""
        cfg = DeprecationConfig.from_dict(
            {
                "bundle_name": "lsp-python",
                "message": "lsp-python is deprecated",
            }
        )
        msg = build_user_message(cfg, severity="warning")
        assert "lsp-python" in msg
        assert "deprecated" in msg.lower()

    def test_urgent_user_message(self):
        """Urgent severity adds URGENT to user message."""
        cfg = DeprecationConfig.from_dict(
            {
                "bundle_name": "lsp-python",
                "message": "deprecated",
            }
        )
        msg = build_user_message(cfg, severity="urgent")
        assert "URGENT" in msg

    def test_includes_replacement_hint(self):
        """User message mentions replacement when available."""
        cfg = DeprecationConfig.from_dict(
            {
                "bundle_name": "lsp-python",
                "message": "deprecated",
                "replacement": "python-dev",
            }
        )
        msg = build_user_message(cfg, severity="warning")
        assert "python-dev" in msg


# — Handler Tests —


class TestDeprecationHook:
    """Tests for the DeprecationHook handler class."""

    def _make_config(self, **overrides):
        """Helper to create a DeprecationConfig with defaults."""
        base = {"bundle_name": "lsp-python", "message": "lsp-python is deprecated"}
        base.update(overrides)
        return DeprecationConfig.from_dict(base)

    def _make_hook(self, config=None, search_dirs=None):
        """Helper to create a DeprecationHook with optional overrides."""
        cfg = config or self._make_config()
        hooks_mock = MagicMock()
        hooks_mock.emit = AsyncMock()
        return DeprecationHook(cfg, hooks_mock, search_dirs=search_dirs or [])

    @pytest.mark.asyncio
    async def test_fires_once_per_session(self):
        """Handler returns inject_context on first call, continue on subsequent."""
        hook = self._make_hook()

        result1 = await hook.on_session_start("session:start", {})
        assert result1.action == "inject_context"

        result2 = await hook.on_session_start("session:start", {})
        assert result2.action == "continue"

    @pytest.mark.asyncio
    async def test_context_injection_contains_warning(self):
        """Context injection text contains the deprecation warning."""
        hook = self._make_hook()
        result = await hook.on_session_start("session:start", {})
        assert result.context_injection is not None
        assert "DEPRECATION WARNING" in result.context_injection
        assert "lsp-python" in result.context_injection

    @pytest.mark.asyncio
    async def test_user_message_set(self):
        """User-visible message is set."""
        hook = self._make_hook()
        result = await hook.on_session_start("session:start", {})
        assert result.user_message is not None
        assert "lsp-python" in result.user_message

    @pytest.mark.asyncio
    async def test_user_message_level_matches_severity(self):
        """user_message_level matches the configured severity."""
        hook = self._make_hook(config=self._make_config(severity="warning"))
        result = await hook.on_session_start("session:start", {})
        assert result.user_message_level == "warning"

    @pytest.mark.asyncio
    async def test_info_severity_message_level(self):
        """Info severity sets user_message_level to info."""
        hook = self._make_hook(config=self._make_config(severity="info"))
        result = await hook.on_session_start("session:start", {})
        assert result.user_message_level == "info"

    @pytest.mark.asyncio
    async def test_urgent_severity_maps_to_warning_level(self):
        """Urgent severity (escalated) maps to user_message_level=warning."""
        past = (date.today() - timedelta(days=1)).isoformat()
        hook = self._make_hook(
            config=self._make_config(severity="warning", sunset_date=past)
        )
        result = await hook.on_session_start("session:start", {})
        # urgent maps to "warning" for user_message_level (HookResult only has info/warning/error)
        assert result.user_message_level == "warning"
        assert result.user_message is not None
        assert "URGENT" in result.user_message

    @pytest.mark.asyncio
    async def test_emits_deprecation_event(self):
        """Emits a deprecation:warning event via coordinator hooks."""
        hooks_mock = MagicMock()
        hooks_mock.emit = AsyncMock()
        cfg = self._make_config()
        hook = DeprecationHook(cfg, hooks_mock, search_dirs=[])

        await hook.on_session_start("session:start", {})

        hooks_mock.emit.assert_called_once()
        call_args = hooks_mock.emit.call_args
        assert call_args[0][0] == "deprecation:warning"
        event_data = call_args[0][1]
        assert event_data["bundle_name"] == "lsp-python"

    @pytest.mark.asyncio
    async def test_source_files_included_in_context(self, tmp_path):
        """Source files found by scanner appear in context injection."""
        amp_dir = tmp_path / ".amplifier"
        amp_dir.mkdir()
        settings = amp_dir / "settings.yaml"
        settings.write_text("includes: lsp-python\n")

        hook = self._make_hook(search_dirs=[tmp_path])
        result = await hook.on_session_start("session:start", {})
        assert result.context_injection is not None
        assert str(settings) in result.context_injection

    @pytest.mark.asyncio
    async def test_user_message_source_is_deprecation(self):
        """user_message_source is 'deprecation' for display attribution."""
        hook = self._make_hook()
        result = await hook.on_session_start("session:start", {})
        assert result.user_message_source == "deprecation"

    @pytest.mark.asyncio
    async def test_require_evidence_true_no_source_files_stays_silent(self, tmp_path):
        """require_evidence=True + no source files found -> stays silent."""
        cfg = self._make_config(require_evidence=True)
        hook = self._make_hook(config=cfg, search_dirs=[tmp_path])

        result = await hook.on_session_start("session:start", {})

        assert result.action == "continue"
        assert result.context_injection is None
        assert result.user_message is None
        hook.hooks.emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_require_evidence_true_with_source_file_fires_normally(
        self, tmp_path
    ):
        """require_evidence=True + a source file present -> fires normally."""
        amp_dir = tmp_path / ".amplifier"
        amp_dir.mkdir()
        source_file = amp_dir / "x.yaml"
        source_file.write_text("includes: lsp-python\n")

        cfg = self._make_config(require_evidence=True)
        hook = self._make_hook(config=cfg, search_dirs=[tmp_path])

        result = await hook.on_session_start("session:start", {})

        assert result.action == "inject_context"
        assert result.user_message is not None
        assert result.context_injection is not None
        assert "Found in:" in result.context_injection
        assert str(source_file) in result.context_injection
        hook.hooks.emit.assert_called_once()
        call_args = hook.hooks.emit.call_args
        assert call_args[0][0] == "deprecation:warning"


# — Mount Function Tests —
#
# mount() now ONLY validates config and registers nothing (see
# on_session_ready() below for where firing actually gets wired up). These
# tests were adapted from the pre-on_session_ready flow: the hook
# registration and working-dir-capability assertions moved to
# TestOnSessionReady, since that's the phase that now owns them.


class TestMount:
    """Tests for the mount() entry point."""

    @pytest.mark.asyncio
    async def test_does_not_register_any_hook(self):
        """mount() no longer registers a session:start handler -- that is
        deferred to on_session_ready()."""
        coordinator = MagicMock()
        coordinator.hooks = MagicMock()
        coordinator.hooks.register = MagicMock()
        coordinator.get_capability = MagicMock(return_value=None)

        config = {
            "bundle_name": "lsp-python",
            "message": "lsp-python is deprecated",
        }
        await mount(coordinator, config)

        coordinator.hooks.register.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none(self):
        """mount() returns None -- no cleanup callable is needed."""
        coordinator = MagicMock()
        coordinator.hooks = MagicMock()
        coordinator.get_capability = MagicMock(return_value=None)

        config = {
            "bundle_name": "lsp-python",
            "message": "lsp-python is deprecated",
            "replacement": "python-dev",
        }
        result = await mount(coordinator, config)

        assert result is None

    @pytest.mark.asyncio
    async def test_does_not_use_working_dir_capability(self):
        """mount() no longer builds search_dirs -- that's on_session_ready's
        job, once the composed plan is available."""
        coordinator = MagicMock()
        coordinator.hooks = MagicMock()
        coordinator.get_capability = MagicMock(return_value="/some/project")

        config = {
            "bundle_name": "lsp-python",
            "message": "lsp-python is deprecated",
        }
        await mount(coordinator, config)

        coordinator.get_capability.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_on_invalid_config(self):
        """mount() raises ValueError on invalid config."""
        coordinator = MagicMock()
        coordinator.hooks = MagicMock()
        coordinator.get_capability = MagicMock(return_value=None)

        with pytest.raises(ValueError, match="bundle_name"):
            await mount(coordinator, {"message": "deprecated"})


# — Multi-Tombstone Tests —
#
# Foundation composition deep-merges module mounts by module-id: deep_merge
# REPLACES scalars (child-wins) but CONCATENATES lists. Two behaviors that each
# mount hooks-deprecation with a different legacy flat `bundle_name` collapse
# to ONE and a tombstone is silently lost. The `deprecations:` list form fixes
# this by unioning instead of colliding. These tests exercise that fix through
# the public `parse_deprecation_configs` + `mount` + `on_session_ready` surfaces.
#
# Firing is now decided in on_session_ready(), not mount(). mount() is still
# called in these tests (it validates config and must keep raising the same
# ValueErrors), but the handler only gets registered -- and can be dispatched
# -- once on_session_ready() runs against a coordinator whose `.config`
# mirrors the composed mount plan.


def _make_coordinator(
    working_dir: str | None = None,
    hooks_config: dict[str, Any] | None = None,
    declared_hooks: list[str] | None = None,
    declared_tools: list[str] | None = None,
    declared_providers: list[str] | None = None,
):
    """Build a coordinator mock whose `.config` mirrors a composed mount plan.

    `hooks_config`, if given, becomes THIS module's own merged config --
    embedded as the `{"module": "hooks-deprecation", "config": ...}` entry
    in `coordinator.config["hooks"]`, exactly as on_session_ready() expects
    to find it post-compose-merge.

    `declared_hooks` / `declared_tools` / `declared_providers` are OTHER
    module ids present in the composed plan (alongside hooks-deprecation
    itself), used to exercise the composed-detection gate: a tombstone
    fires without filesystem evidence iff its `bundle_name` is among these.
    """
    coordinator = MagicMock()
    coordinator.hooks = MagicMock()
    coordinator.hooks.register = MagicMock()
    coordinator.hooks.emit = AsyncMock()
    coordinator.hooks.list_handlers = MagicMock(return_value={})
    coordinator.get_capability = MagicMock(return_value=working_dir)

    # Records (channel, name, callback) tuples passed to register_contributor()
    # so tests can assert on the observability.events declaration mount() makes
    # for "deprecation:warning" (see TestObservabilityRegistration below).
    register_contributor_calls: list[tuple[str, str, Any]] = []
    coordinator.register_contributor_calls = register_contributor_calls

    def _record_contributor(channel: str, name: str, callback: Any) -> None:
        register_contributor_calls.append((channel, name, callback))

    coordinator.register_contributor = _record_contributor

    hooks_entries: list[dict[str, Any]] = [
        {"module": name} for name in (declared_hooks or [])
    ]
    if hooks_config is not None:
        hooks_entries.append({"module": "hooks-deprecation", "config": hooks_config})

    coordinator.config = {
        "hooks": hooks_entries,
        "tools": [{"module": name} for name in (declared_tools or [])],
        "providers": [{"module": name} for name in (declared_providers or [])],
    }
    return coordinator


async def _register_and_dispatch(coordinator):
    """Run on_session_ready() (registers the handler), then invoke it.

    Replaces the old `_dispatch_session_start`, which invoked the handler
    mount() used to register directly. Registration now happens in
    on_session_ready() instead.
    """
    await on_session_ready(coordinator)
    call_args = coordinator.hooks.register.call_args
    handler = call_args[0][1]
    return await handler("session:start", {})


class TestParseDeprecationConfigs:
    """Tests for the parse_deprecation_configs() normalizer."""

    def test_legacy_flat_produces_one_tombstone(self):
        """Legacy flat form (unchanged) produces exactly one tombstone."""
        configs = parse_deprecation_configs(
            {"bundle_name": "hooks-redaction", "message": "retired"}
        )
        assert len(configs) == 1
        assert configs[0].bundle_name == "hooks-redaction"

    def test_list_form_produces_n_tombstones(self):
        """`deprecations:` list form produces one tombstone per entry."""
        configs = parse_deprecation_configs(
            {
                "deprecations": [
                    {"bundle_name": "a", "message": "m1"},
                    {"bundle_name": "b", "message": "m2"},
                ]
            }
        )
        assert [c.bundle_name for c in configs] == ["a", "b"]

    def test_both_present_produces_flat_plus_list_tombstones(self):
        """Flat fields AND `deprecations:` entries both become tombstones.

        This is the shape a composed bundle sees after deep_merge unions two
        behaviors that each mount this module with a different tombstone.
        """
        configs = parse_deprecation_configs(
            {
                "bundle_name": "hooks-redaction",
                "message": "r",
                "deprecations": [{"bundle_name": "hooks-logging", "message": "l"}],
            }
        )
        assert [c.bundle_name for c in configs] == ["hooks-redaction", "hooks-logging"]

    def test_empty_config_raises_value_error(self):
        """Neither bundle_name nor a non-empty deprecations list -> ValueError."""
        with pytest.raises(ValueError, match="bundle_name.*deprecations"):
            parse_deprecation_configs({})

    def test_empty_deprecations_list_raises_value_error(self):
        """An empty `deprecations: []` list (and no flat bundle_name) still raises."""
        with pytest.raises(ValueError):
            parse_deprecation_configs({"deprecations": []})


class TestMultiTombstoneMount:
    """Tests for mount() + on_session_ready() with multiple tombstones
    (list form and union)."""

    @pytest.mark.asyncio
    async def test_list_form_both_fire(self):
        """Two tombstones in `deprecations:` both fire in one combined dispatch."""
        config = {
            "deprecations": [
                {"bundle_name": "a", "message": "message-a", "require_evidence": False},
                {"bundle_name": "b", "message": "message-b", "require_evidence": False},
            ]
        }
        coordinator = _make_coordinator(hooks_config=config)
        await mount(coordinator, config)  # validates only; registers nothing

        result = await _register_and_dispatch(coordinator)

        assert result.action == "inject_context"
        assert result.context_injection is not None
        assert "DEPRECATION WARNING: a" in result.context_injection
        assert "DEPRECATION WARNING: b" in result.context_injection
        assert result.user_message is not None
        assert "a" in result.user_message
        assert "b" in result.user_message

        assert coordinator.hooks.emit.await_count == 2
        emitted_bundles = {
            call.args[1]["bundle_name"]
            for call in coordinator.hooks.emit.await_args_list
        }
        assert emitted_bundles == {"a", "b"}

    @pytest.mark.asyncio
    async def test_union_of_flat_and_list_both_fire(self):
        """Flat bundle_name + deprecations list (the post-merge shape) both fire.

        Mirrors what deep_merge produces when redaction.yaml's legacy flat
        tombstone and logging.yaml's `deprecations:` tombstone are unioned.
        """
        config = {
            "bundle_name": "hooks-redaction",
            "message": "hooks-redaction is retired",
            "require_evidence": False,
            "deprecations": [
                {
                    "bundle_name": "hooks-logging",
                    "message": "hooks-logging is demoted",
                    "require_evidence": False,
                }
            ],
        }
        coordinator = _make_coordinator(hooks_config=config)
        await mount(coordinator, config)

        result = await _register_and_dispatch(coordinator)

        assert result.action == "inject_context"
        assert "hooks-redaction" in result.context_injection
        assert "hooks-logging" in result.context_injection

        assert coordinator.hooks.emit.await_count == 2
        emitted_bundles = {
            call.args[1]["bundle_name"]
            for call in coordinator.hooks.emit.await_args_list
        }
        assert emitted_bundles == {"hooks-redaction", "hooks-logging"}

    @pytest.mark.asyncio
    async def test_flat_plus_list_logs_coexistence_warning(self, caplog):
        """Scalar bundle_name composed alongside a deprecations: list warns.

        The flat form under composition is at clobber risk (deep-merge keeps
        only one scalar bundle_name), so on_session_ready() logs a migration
        warning naming the at-risk flat tombstone -- warning on the CAUSE
        (flat form present with a list), since the dropped tombstone from an
        actual collision is already gone before the hook runs.
        """
        config = {
            "bundle_name": "hooks-redaction",
            "message": "hooks-redaction is retired",
            "require_evidence": False,
            "deprecations": [
                {
                    "bundle_name": "hooks-logging",
                    "message": "demoted",
                    "require_evidence": False,
                }
            ],
        }
        coordinator = _make_coordinator(hooks_config=config)
        with caplog.at_level(
            logging.WARNING, logger="amplifier_module_hooks_deprecation"
        ):
            await on_session_ready(coordinator)
        warnings = [
            r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any("composed alongside a 'deprecations:' list" in m for m in warnings)
        assert any("hooks-redaction" in m for m in warnings)

    @pytest.mark.asyncio
    async def test_lone_flat_form_does_not_warn(self, caplog):
        """A single flat tombstone (no list) is safe -> no coexistence warning."""
        config = {
            "bundle_name": "hooks-redaction",
            "message": "retired",
            "require_evidence": False,
        }
        coordinator = _make_coordinator(hooks_config=config)
        with caplog.at_level(
            logging.WARNING, logger="amplifier_module_hooks_deprecation"
        ):
            await on_session_ready(coordinator)
        warnings = [
            r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert not any(
            "composed alongside a 'deprecations:' list" in m for m in warnings
        )

    @pytest.mark.asyncio
    async def test_list_only_form_does_not_warn(self, caplog):
        """List form with no flat scalar -> no coexistence warning."""
        config = {
            "deprecations": [
                {"bundle_name": "a", "message": "m", "require_evidence": False}
            ]
        }
        coordinator = _make_coordinator(hooks_config=config)
        with caplog.at_level(
            logging.WARNING, logger="amplifier_module_hooks_deprecation"
        ):
            await on_session_ready(coordinator)
        warnings = [
            r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert not any(
            "composed alongside a 'deprecations:' list" in m for m in warnings
        )

    @pytest.mark.asyncio
    async def test_require_evidence_gates_independently_per_tombstone(
        self, tmp_path, monkeypatch
    ):
        """Each tombstone's require_evidence gate is evaluated independently.

        Evidence is authored for only ONE of the two bundle_names, and
        neither bundle is present in the composed plan (no declared_hooks
        given); only the evidenced tombstone should fire, and only its
        event should be emitted.

        on_session_ready() always adds Path.cwd() and Path.home() to
        search_dirs in addition to the working_dir capability, so both are
        pinned to the same isolated tmp_path here -- otherwise real files
        in the actual cwd/home (outside this test's control) could
        coincidentally reference one of these bundle names and make the
        gating non-deterministic.
        """
        amp_dir = tmp_path / ".amplifier"
        amp_dir.mkdir()
        (amp_dir / "settings.yaml").write_text(
            "includes: hooks-redaction\n", encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))

        config = {
            "bundle_name": "hooks-redaction",
            "message": "hooks-redaction is retired",
            "require_evidence": True,
            "deprecations": [
                {
                    "bundle_name": "hooks-logging",
                    "message": "hooks-logging is demoted",
                    "require_evidence": True,
                }
            ],
        }
        coordinator = _make_coordinator(working_dir=str(tmp_path), hooks_config=config)
        await mount(coordinator, config)

        result = await _register_and_dispatch(coordinator)

        assert result.action == "inject_context"
        assert "hooks-redaction" in result.context_injection
        assert "hooks-logging" not in result.context_injection

        coordinator.hooks.emit.assert_called_once()
        call_args = coordinator.hooks.emit.call_args
        assert call_args[0][1]["bundle_name"] == "hooks-redaction"

    @pytest.mark.asyncio
    async def test_single_flat_config_is_pass_through_identical_to_direct_hook(self):
        """Backward compat: mount() + on_session_ready() with ONE flat
        tombstone must be observably identical to using DeprecationHook
        directly (today's legacy behavior).

        Both paths use the same search dirs (Path.cwd() + Path.home(), since
        get_capability returns None in both) so the source-file scan -- and
        therefore the full result -- is deterministically identical. Neither
        path declares any other composed module, so `composed` stays False
        on both sides too.
        """
        cfg_dict = {"bundle_name": "lsp-python", "message": "lsp-python is deprecated"}
        same_search_dirs = [Path.cwd(), Path.home()]

        direct_hooks_mock = MagicMock()
        direct_hooks_mock.emit = AsyncMock()
        direct_hook = DeprecationHook(
            DeprecationConfig.from_dict(cfg_dict),
            direct_hooks_mock,
            search_dirs=same_search_dirs,
        )
        direct_result = await direct_hook.on_session_start("session:start", {})

        coordinator = _make_coordinator(working_dir=None, hooks_config=cfg_dict)
        await mount(coordinator, cfg_dict)
        mounted_result = await _register_and_dispatch(coordinator)

        assert mounted_result.action == direct_result.action
        assert mounted_result.context_injection == direct_result.context_injection
        assert mounted_result.user_message == direct_result.user_message
        assert mounted_result.user_message_level == direct_result.user_message_level
        assert mounted_result.user_message_source == direct_result.user_message_source

    @pytest.mark.asyncio
    async def test_single_flat_config_fires_once_per_session_through_mount(self):
        """Backward compat: single-tombstone mount() + on_session_ready()
        still fires once per session (mirrors
        TestDeprecationHook.test_fires_once_per_session, but through the
        full mount() + on_session_ready() + combine_hook_results() path)."""
        config = {"bundle_name": "lsp-python", "message": "lsp-python is deprecated"}
        coordinator = _make_coordinator(hooks_config=config)
        await mount(coordinator, config)
        await on_session_ready(coordinator)

        call_args = coordinator.hooks.register.call_args
        handler = call_args[0][1]

        result1 = await handler("session:start", {})
        assert result1.action == "inject_context"

        result2 = await handler("session:start", {})
        assert result2.action == "continue"

    @pytest.mark.asyncio
    async def test_empty_config_raises_value_error_through_mount(self):
        """mount() propagates parse_deprecation_configs' ValueError for {}."""
        coordinator = _make_coordinator()

        with pytest.raises(ValueError, match="bundle_name"):
            await mount(coordinator, {})


# — on_session_ready() Tests —
#
# Covers the registration mechanics on_session_ready() now owns: recovering
# this module's own merged config from the composed plan, the defensive
# no-op paths (no own entry, already registered, registration raises), and
# the composed-detection gate itself (the additive bypass of
# require_evidence when a tombstone's bundle is confirmed present in the
# composed plan).


class TestOnSessionReady:
    """Tests for on_session_ready()'s registration mechanics."""

    @pytest.mark.asyncio
    async def test_no_own_config_entry_registers_nothing(self):
        """If coordinator.config['hooks'] has no hooks-deprecation entry,
        this module wasn't actually mounted for this session -- return
        without registering anything."""
        coordinator = _make_coordinator()  # no hooks_config -> no own entry

        await on_session_ready(coordinator)

        coordinator.hooks.register.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_registration_if_already_registered(self):
        """Defensive guard: if list_handlers() already reports a
        'deprecation' handler on session:start, on_session_ready() does not
        register again."""
        config = {"bundle_name": "lsp-python", "message": "deprecated"}
        coordinator = _make_coordinator(hooks_config=config)
        coordinator.hooks.list_handlers = MagicMock(
            return_value={"session:start": ["deprecation"]}
        )

        await on_session_ready(coordinator)

        coordinator.hooks.register.assert_not_called()

    @pytest.mark.asyncio
    async def test_registration_exception_is_caught_not_propagated(self):
        """An exception raised while registering the handler is caught and
        logged, not propagated -- a deprecation notice must never be the
        reason session initialization fails."""
        config = {"bundle_name": "lsp-python", "message": "deprecated"}
        coordinator = _make_coordinator(hooks_config=config)
        coordinator.hooks.register = MagicMock(side_effect=RuntimeError("boom"))

        await on_session_ready(coordinator)  # must not raise

    @pytest.mark.asyncio
    async def test_registers_session_start_handler_named_deprecation(self):
        """on_session_ready() registers exactly one 'session:start' handler
        named 'deprecation', same contract mount() used to fulfill."""
        config = {"bundle_name": "lsp-python", "message": "deprecated"}
        coordinator = _make_coordinator(hooks_config=config)

        await on_session_ready(coordinator)

        coordinator.hooks.register.assert_called_once()
        call_args = coordinator.hooks.register.call_args
        assert call_args[0][0] == "session:start"
        assert call_args[1]["name"] == "deprecation"
        assert call_args[1]["priority"] == 10


class TestComposedDetection:
    """Tests for the additive composed-detection gate: a tombstone whose
    bundle_name is confirmed present in the ACTUAL composed mount plan
    fires even with zero filesystem evidence. Nothing that fired under the
    old evidence-only gate stops firing (see the (c) legacy case below)."""

    @pytest.mark.asyncio
    async def test_composed_bundle_in_hooks_fires_without_evidence(
        self, tmp_path, monkeypatch
    ):
        """(a) require_evidence=True + bundle IS in coordinator.config['hooks']
        -> fires even with NO filesystem source_files."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        config = {
            "bundle_name": "lsp-python",
            "message": "lsp-python is deprecated",
            "require_evidence": True,
        }
        coordinator = _make_coordinator(
            working_dir=str(tmp_path),
            hooks_config=config,
            declared_hooks=["lsp-python"],
        )

        result = await _register_and_dispatch(coordinator)

        assert result.action == "inject_context"
        assert "lsp-python" in result.context_injection
        coordinator.hooks.emit.assert_called_once()

    @pytest.mark.asyncio
    async def test_composed_bundle_in_tools_fires_without_evidence(
        self, tmp_path, monkeypatch
    ):
        """(a) variant: the composed signal also comes from `tools`, not
        just `hooks`."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        config = {
            "bundle_name": "tool-legacy",
            "message": "tool-legacy is deprecated",
            "require_evidence": True,
        }
        coordinator = _make_coordinator(
            working_dir=str(tmp_path),
            hooks_config=config,
            declared_tools=["tool-legacy"],
        )

        result = await _register_and_dispatch(coordinator)

        assert result.action == "inject_context"
        coordinator.hooks.emit.assert_called_once()

    @pytest.mark.asyncio
    async def test_composed_bundle_in_providers_fires_without_evidence(
        self, tmp_path, monkeypatch
    ):
        """(a) variant: the composed signal also comes from `providers`."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        config = {
            "bundle_name": "provider-legacy",
            "message": "provider-legacy is deprecated",
            "require_evidence": True,
        }
        coordinator = _make_coordinator(
            working_dir=str(tmp_path),
            hooks_config=config,
            declared_providers=["provider-legacy"],
        )

        result = await _register_and_dispatch(coordinator)

        assert result.action == "inject_context"
        coordinator.hooks.emit.assert_called_once()

    @pytest.mark.asyncio
    async def test_not_composed_and_no_evidence_stays_silent(
        self, tmp_path, monkeypatch
    ):
        """(b) require_evidence=True + module NOT composed + NO source
        files -> stays silent."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        config = {
            "bundle_name": "lsp-python",
            "message": "lsp-python is deprecated",
            "require_evidence": True,
        }
        coordinator = _make_coordinator(working_dir=str(tmp_path), hooks_config=config)

        result = await _register_and_dispatch(coordinator)

        assert result.action == "continue"
        assert result.context_injection is None
        assert result.user_message is None
        coordinator.hooks.emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_require_evidence_false_fires_regardless(self, tmp_path, monkeypatch):
        """(c) require_evidence=False fires regardless of composed status
        or filesystem evidence (legacy, always-fire behavior)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        config = {
            "bundle_name": "lsp-python",
            "message": "lsp-python is deprecated",
            "require_evidence": False,
        }
        coordinator = _make_coordinator(working_dir=str(tmp_path), hooks_config=config)

        result = await _register_and_dispatch(coordinator)

        assert result.action == "inject_context"
        coordinator.hooks.emit.assert_called_once()

    @pytest.mark.asyncio
    async def test_union_only_composed_tombstone_fires(self, tmp_path, monkeypatch):
        """(d) P2 union case: a merged config carries a flat tombstone
        (hooks-redaction, require_evidence=True) AND a `deprecations:` list
        tombstone (hooks-logging, require_evidence=True). Only
        hooks-logging is present in the composed plan's `hooks` list; only
        it should fire."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        config = {
            "bundle_name": "hooks-redaction",
            "message": "hooks-redaction is retired",
            "require_evidence": True,
            "deprecations": [
                {
                    "bundle_name": "hooks-logging",
                    "message": "hooks-logging is demoted",
                    "require_evidence": True,
                }
            ],
        }
        coordinator = _make_coordinator(
            working_dir=str(tmp_path),
            hooks_config=config,
            declared_hooks=["hooks-logging"],  # hooks-redaction NOT declared
        )

        result = await _register_and_dispatch(coordinator)

        assert result.action == "inject_context"
        assert "hooks-logging" in result.context_injection
        assert "hooks-redaction" not in result.context_injection

        coordinator.hooks.emit.assert_called_once()
        call_args = coordinator.hooks.emit.call_args
        assert call_args[0][1]["bundle_name"] == "hooks-logging"


# — Observability Contribution Channel Tests —
#
# hooks-deprecation EMITS "deprecation:warning" via self.hooks.emit(...) but
# must also DECLARE it on the "observability.events" contribution channel so
# the session capture hooks (hooks-logging + hook-context-intelligence), which
# auto-discover recordable events via coordinator.collect_contributions(
# "observability.events") at their own on_session_ready(), know to record it.
# This is a module-owned declaration (template: tool-delegate's mount()); see
# core:docs/specs/CONTRIBUTION_CHANNELS.md.


class TestObservabilityRegistration:
    """Tests for mount()'s observability.events contribution registration."""

    @pytest.mark.asyncio
    async def test_mount_registers_deprecation_warning_event(self):
        """mount() registers exactly one observability.events contributor,
        named 'hooks-deprecation', whose callback returns
        ['deprecation:warning']."""
        config = {"bundle_name": "lsp-python", "message": "lsp-python is deprecated"}
        coordinator = _make_coordinator(hooks_config=config)

        await mount(coordinator, config)

        assert len(coordinator.register_contributor_calls) == 1
        channel, name, callback = coordinator.register_contributor_calls[0]
        assert channel == "observability.events"
        assert name == "hooks-deprecation"
        assert callback() == ["deprecation:warning"]

    @pytest.mark.asyncio
    async def test_registration_happens_even_without_evidence_requirement(self):
        """The registration is unconditional -- it does not depend on any
        tombstone's require_evidence setting or on multi-tombstone shape."""
        config = {
            "deprecations": [
                {"bundle_name": "a", "message": "message-a"},
                {"bundle_name": "b", "message": "message-b"},
            ]
        }
        coordinator = _make_coordinator(hooks_config=config)

        await mount(coordinator, config)

        assert len(coordinator.register_contributor_calls) == 1
        channel, name, callback = coordinator.register_contributor_calls[0]
        assert channel == "observability.events"
        assert name == "hooks-deprecation"
        assert callback() == ["deprecation:warning"]

    @pytest.mark.asyncio
    async def test_invalid_config_does_not_register_contributor(self):
        """mount() raises before registering the contributor when config is
        invalid -- validation still happens first, unchanged."""
        coordinator = _make_coordinator()

        with pytest.raises(ValueError, match="bundle_name"):
            await mount(coordinator, {})

        assert coordinator.register_contributor_calls == []
