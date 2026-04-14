"""Tests for the deprecation hook module."""

from datetime import date, timedelta
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


# — Mount Function Tests —


class TestMount:
    """Tests for the mount() entry point."""

    @pytest.mark.asyncio
    async def test_registers_session_start_hook(self):
        """mount() registers a handler on 'session:start'."""
        coordinator = MagicMock()
        coordinator.hooks = MagicMock()
        coordinator.hooks.register = MagicMock()
        coordinator.get_capability = MagicMock(return_value=None)

        config = {
            "bundle_name": "lsp-python",
            "message": "lsp-python is deprecated",
        }
        await mount(coordinator, config)

        coordinator.hooks.register.assert_called_once()
        call_args = coordinator.hooks.register.call_args
        assert call_args[0][0] == "session:start"
        assert call_args[1]["name"] == "deprecation"

    @pytest.mark.asyncio
    async def test_returns_module_metadata(self):
        """mount() returns proper module metadata dict."""
        coordinator = MagicMock()
        coordinator.hooks = MagicMock()
        coordinator.hooks.register = MagicMock()
        coordinator.get_capability = MagicMock(return_value=None)

        config = {
            "bundle_name": "lsp-python",
            "message": "lsp-python is deprecated",
            "replacement": "python-dev",
        }
        result = await mount(coordinator, config)

        assert result["name"] == "hooks-deprecation"
        assert "version" in result
        assert result["config"]["bundle_name"] == "lsp-python"

    @pytest.mark.asyncio
    async def test_uses_working_dir_capability(self):
        """mount() uses session.working_dir capability for source scanning."""
        coordinator = MagicMock()
        coordinator.hooks = MagicMock()
        coordinator.hooks.register = MagicMock()
        coordinator.get_capability = MagicMock(return_value="/some/project")

        config = {
            "bundle_name": "lsp-python",
            "message": "lsp-python is deprecated",
        }
        await mount(coordinator, config)

        coordinator.get_capability.assert_called_with("session.working_dir")

    @pytest.mark.asyncio
    async def test_raises_on_invalid_config(self):
        """mount() raises ValueError on invalid config."""
        coordinator = MagicMock()
        coordinator.hooks = MagicMock()
        coordinator.get_capability = MagicMock(return_value=None)

        with pytest.raises(ValueError, match="bundle_name"):
            await mount(coordinator, {"message": "deprecated"})
