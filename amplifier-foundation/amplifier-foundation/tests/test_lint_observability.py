"""Tests for scripts/lint_observability.py observability lint tool."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

# Load lint_observability.py as a module (it's a script, not a package)
_script_path = (
    Path(__file__).resolve().parent.parent / "scripts" / "lint_observability.py"
)
_spec = importlib.util.spec_from_file_location("lint_observability", _script_path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load {_script_path}")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

find_python_files = _mod.find_python_files
scan_fire_and_forget = _mod.scan_fire_and_forget
scan_emitted_events = _mod.scan_emitted_events
scan_registered_events = _mod.scan_registered_events
is_canonical_event = _mod.is_canonical_event
lint_file = _mod.lint_file

# Fixtures directory
FIXTURES_DIR = (
    Path(__file__).resolve().parent.parent / "test-fixtures" / "lint_observability"
)


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    """Run lint_observability.py as a subprocess with the given arguments."""
    return subprocess.run(
        [sys.executable, str(_script_path), *args],
        capture_output=True,
        text=True,
        timeout=15,
    )


# ---------------------------------------------------------------------------
# TestIsCanonicalEvent
# ---------------------------------------------------------------------------


class TestIsCanonicalEvent:
    """Tests for is_canonical_event — identifies built-in amplifier_core events."""

    def test_session_prefix_is_canonical(self) -> None:
        """session:* events are canonical."""
        assert is_canonical_event("session:start") is True

    def test_llm_prefix_is_canonical(self) -> None:
        """llm:* events are canonical."""
        assert is_canonical_event("llm:request") is True

    def test_provider_prefix_is_canonical(self) -> None:
        """provider:* events are canonical."""
        assert is_canonical_event("provider:selected") is True

    def test_tool_prefix_is_canonical(self) -> None:
        """tool:* events are canonical."""
        assert is_canonical_event("tool:result") is True

    def test_execution_prefix_is_canonical(self) -> None:
        """execution:* events are canonical."""
        assert is_canonical_event("execution:complete") is True

    def test_orchestrator_prefix_is_canonical(self) -> None:
        """orchestrator:* events are canonical."""
        assert is_canonical_event("orchestrator:turn_start") is True

    def test_custom_prefix_is_not_canonical(self) -> None:
        """custom:* events are NOT canonical."""
        assert is_canonical_event("custom:event") is False

    def test_delegate_prefix_is_not_canonical(self) -> None:
        """delegate:* events are NOT canonical — must be registered."""
        assert is_canonical_event("delegate:agent_spawned") is False

    def test_deprecation_prefix_is_not_canonical(self) -> None:
        """deprecation:* events are NOT canonical — must be registered."""
        assert is_canonical_event("deprecation:warning") is False

    def test_recipe_prefix_is_not_canonical(self) -> None:
        """recipe:* events are NOT canonical — must be registered."""
        assert is_canonical_event("recipe:start") is False


# ---------------------------------------------------------------------------
# TestScanFireAndForget
# ---------------------------------------------------------------------------


class TestScanFireAndForget:
    """Tests for scan_fire_and_forget — detects asyncio.create_task(hooks.emit(...))."""

    def test_detects_same_line_pattern(self) -> None:
        """Finds asyncio.create_task(hooks.emit(...)) on a single line."""
        content = 'asyncio.create_task(hooks.emit("custom:event", {}))\n'
        issues = scan_fire_and_forget(content, Path("test.py"))
        assert len(issues) == 1
        severity, line_no, message = issues[0]
        assert severity == "WARNING"
        assert line_no == 1
        assert "fire-and-forget" in message

    def test_detects_self_hooks_variant(self) -> None:
        """Finds asyncio.create_task(self.hooks.emit(...)) — attribute access variant."""
        content = 'asyncio.create_task(self.hooks.emit("custom:event", {}))\n'
        issues = scan_fire_and_forget(content, Path("test.py"))
        assert len(issues) == 1
        assert issues[0][0] == "WARNING"

    def test_detects_multiline_pattern(self) -> None:
        """Finds fire-and-forget when create_task and emit are on adjacent lines."""
        content = (
            "asyncio.create_task(\n"
            '    hooks.emit("custom:event", {"data": "value"})\n'
            ")\n"
        )
        issues = scan_fire_and_forget(content, Path("test.py"))
        assert len(issues) == 1
        assert issues[0][0] == "WARNING"

    def test_no_false_positive_awaited_emit(self) -> None:
        """Does NOT flag properly awaited hooks.emit calls."""
        content = 'await hooks.emit("custom:event", {})\n'
        issues = scan_fire_and_forget(content, Path("test.py"))
        assert issues == []

    def test_no_false_positive_create_task_without_emit(self) -> None:
        """Does NOT flag asyncio.create_task used without hooks.emit."""
        content = "asyncio.create_task(some_other_coroutine())\n"
        issues = scan_fire_and_forget(content, Path("test.py"))
        assert issues == []

    def test_reports_correct_line_number(self) -> None:
        """Reports the line number where asyncio.create_task appears."""
        content = (
            '# line 1\n# line 2\nasyncio.create_task(hooks.emit("custom:event", {}))\n'
        )
        issues = scan_fire_and_forget(content, Path("test.py"))
        assert len(issues) == 1
        assert issues[0][1] == 3  # Line 3


# ---------------------------------------------------------------------------
# TestScanEmittedEvents
# ---------------------------------------------------------------------------


class TestScanEmittedEvents:
    """Tests for scan_emitted_events — extracts event names from hooks.emit() calls."""

    def test_extracts_event_name_from_emit(self) -> None:
        """Extracts event name from hooks.emit("event:name", ...)."""
        content = 'await hooks.emit("custom:ready", {})\n'
        emitted = scan_emitted_events(content)
        assert "custom:ready" in emitted

    def test_extracts_multiple_events(self) -> None:
        """Extracts all event names from multiple hooks.emit calls."""
        content = (
            'await hooks.emit("custom:start", {})\n'
            'await hooks.emit("custom:done", {})\n'
        )
        emitted = scan_emitted_events(content)
        assert "custom:start" in emitted
        assert "custom:done" in emitted

    def test_extracts_from_self_hooks(self) -> None:
        """Extracts event name from self.hooks.emit(...) pattern."""
        content = 'await self.hooks.emit("deprecation:warning", {"bundle": "old"})\n'
        emitted = scan_emitted_events(content)
        assert "deprecation:warning" in emitted

    def test_returns_line_number(self) -> None:
        """Returns the line number where each event is first emitted."""
        content = '# comment\nawait hooks.emit("custom:event", {})\n'
        emitted = scan_emitted_events(content)
        assert emitted.get("custom:event") == 2

    def test_empty_file_returns_empty(self) -> None:
        """Empty file returns empty dict."""
        emitted = scan_emitted_events("")
        assert emitted == {}

    def test_no_emit_calls_returns_empty(self) -> None:
        """File with no emit calls returns empty dict."""
        content = "def foo():\n    return 42\n"
        emitted = scan_emitted_events(content)
        assert emitted == {}


# ---------------------------------------------------------------------------
# TestScanRegisteredEvents
# ---------------------------------------------------------------------------


class TestScanRegisteredEvents:
    """Tests for scan_registered_events — extracts events from registration calls."""

    def test_extracts_inline_list(self) -> None:
        """Extracts events from register_capability with inline list."""
        content = (
            "async def mount(coordinator, config=None):\n"
            "    coordinator.register_capability(\n"
            '        "observability.events", ["custom:event", "custom:done"]\n'
            "    )\n"
        )
        registered = scan_registered_events(content)
        assert "custom:event" in registered
        assert "custom:done" in registered

    def test_extracts_variable_extend_pattern(self) -> None:
        """Extracts events from the variable extend+register pattern."""
        content = (
            "async def mount(coordinator, config=None):\n"
            '    obs = coordinator.get_capability("observability.events") or []\n'
            "    obs.extend([\n"
            '        "delegate:agent_spawned",\n'
            '        "delegate:agent_completed",\n'
            "    ])\n"
            '    coordinator.register_capability("observability.events", obs)\n'
        )
        registered = scan_registered_events(content)
        assert "delegate:agent_spawned" in registered
        assert "delegate:agent_completed" in registered

    def test_register_contributor_also_works(self) -> None:
        """Extracts events from register_contributor as well as register_capability."""
        content = (
            "async def mount(coordinator, config=None):\n"
            "    coordinator.register_contributor(\n"
            '        "observability.events", ["custom:event"]\n'
            "    )\n"
        )
        registered = scan_registered_events(content)
        assert "custom:event" in registered

    def test_no_registration_returns_empty(self) -> None:
        """Returns empty set when no observability.events registration found."""
        content = 'async def handle(hooks):\n    await hooks.emit("custom:event", {})\n'
        registered = scan_registered_events(content)
        assert registered == set()

    def test_observability_events_key_not_included(self) -> None:
        """The 'observability.events' key itself is not included as a registered event."""
        content = (
            "    coordinator.register_capability(\n"
            '        "observability.events", ["custom:event"]\n'
            "    )\n"
        )
        registered = scan_registered_events(content)
        assert "observability.events" not in registered


# ---------------------------------------------------------------------------
# TestLintFile — using real fixture files
# ---------------------------------------------------------------------------


class TestLintFile:
    """Tests for lint_file — end-to-end linting of fixture files."""

    def test_fire_and_forget_fixture_produces_warning(self) -> None:
        """fire_and_forget.py fixture produces at least one WARNING."""
        path = FIXTURES_DIR / "fire_and_forget.py"
        issues = lint_file(path)
        warnings = [i for i in issues if i[0] == "WARNING"]
        assert len(warnings) >= 1
        assert any("fire-and-forget" in msg for _, _, msg in warnings)

    def test_unregistered_event_fixture_produces_error(self) -> None:
        """unregistered_event.py fixture produces an ERROR for the unregistered event."""
        path = FIXTURES_DIR / "unregistered_event.py"
        issues = lint_file(path)
        errors = [i for i in issues if i[0] == "ERROR"]
        assert len(errors) >= 1
        assert any("custom:unregistered" in msg for _, _, msg in errors)

    def test_unregistered_event_does_not_flag_registered(self) -> None:
        """unregistered_event.py: the registered 'custom:ready' is NOT flagged."""
        path = FIXTURES_DIR / "unregistered_event.py"
        issues = lint_file(path)
        errors = [i for i in issues if i[0] == "ERROR"]
        # None of the errors should mention custom:ready (that one IS registered)
        assert not any("custom:ready" in msg for _, _, msg in errors)

    def test_clean_fixture_has_no_issues(self) -> None:
        """clean.py fixture produces no issues."""
        path = FIXTURES_DIR / "clean.py"
        issues = lint_file(path)
        assert issues == []

    def test_canonical_events_fixture_has_no_issues(self) -> None:
        """canonical_events.py fixture produces no errors (canonical events don't need registration)."""
        path = FIXTURES_DIR / "canonical_events.py"
        issues = lint_file(path)
        errors = [i for i in issues if i[0] == "ERROR"]
        assert errors == []

    def test_variable_registered_fixture_has_no_errors(self) -> None:
        """variable_registered.py fixture (extend+register pattern) produces no errors."""
        path = FIXTURES_DIR / "variable_registered.py"
        issues = lint_file(path)
        errors = [i for i in issues if i[0] == "ERROR"]
        assert errors == []

    def test_nonexistent_file_returns_error(self) -> None:
        """Returns an error for a file that cannot be read."""
        path = Path("/nonexistent/path/to/file.py")
        issues = lint_file(path)
        assert len(issues) == 1
        assert issues[0][0] == "ERROR"
        assert "Could not read file" in issues[0][2]


# ---------------------------------------------------------------------------
# TestFindPythonFiles
# ---------------------------------------------------------------------------


class TestFindPythonFiles:
    """Tests for find_python_files — collects .py files from paths."""

    def test_finds_file_directly(self, tmp_path: Path) -> None:
        """A .py file path is returned as-is."""
        f = tmp_path / "module.py"
        f.write_text("x = 1\n")
        result = find_python_files([str(f)])
        assert f in result

    def test_finds_py_files_in_directory(self, tmp_path: Path) -> None:
        """Finds all .py files recursively in a directory."""
        (tmp_path / "a.py").write_text("x = 1\n")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "b.py").write_text("y = 2\n")
        result = find_python_files([str(tmp_path)])
        paths = [p.name for p in result]
        assert "a.py" in paths
        assert "b.py" in paths

    def test_ignores_non_py_files(self, tmp_path: Path) -> None:
        """Non-.py files are not included."""
        (tmp_path / "README.md").write_text("# docs\n")
        (tmp_path / "data.json").write_text("{}\n")
        (tmp_path / "module.py").write_text("x = 1\n")
        result = find_python_files([str(tmp_path)])
        assert all(p.suffix == ".py" for p in result)

    def test_multiple_paths(self, tmp_path: Path) -> None:
        """Accepts multiple paths."""
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("x = 1\n")
        f2.write_text("y = 2\n")
        result = find_python_files([str(f1), str(f2)])
        assert f1 in result
        assert f2 in result


# ---------------------------------------------------------------------------
# TestCLI — subprocess-based end-to-end tests
# ---------------------------------------------------------------------------


class TestCLI:
    """Subprocess-based CLI tests for lint_observability.py."""

    def test_clean_file_exits_0(self) -> None:
        """--lint on a clean file exits 0."""
        result = _run_cli(str(FIXTURES_DIR / "clean.py"))
        assert result.returncode == 0

    def test_canonical_events_exits_0(self) -> None:
        """Canonical-only events file exits 0 (no errors)."""
        result = _run_cli(str(FIXTURES_DIR / "canonical_events.py"))
        assert result.returncode == 0

    def test_fire_and_forget_exits_0_warning_only(self) -> None:
        """Fire-and-forget warnings do NOT cause non-zero exit (warnings are ok)."""
        # fire_and_forget.py has a registered event so no ERROR; only WARNING
        result = _run_cli(str(FIXTURES_DIR / "fire_and_forget.py"))
        assert result.returncode == 0
        assert "WARNING" in result.stdout

    def test_unregistered_event_exits_1(self) -> None:
        """Unregistered event ERROR causes exit code 1."""
        result = _run_cli(str(FIXTURES_DIR / "unregistered_event.py"))
        assert result.returncode == 1
        assert "ERROR" in result.stdout

    def test_directory_scan(self) -> None:
        """Accepts directory path and scans all .py files."""
        result = _run_cli(str(FIXTURES_DIR))
        # The fixture directory contains both errors and warnings
        # Just verify it ran and produced output
        assert result.returncode in (0, 1)
        assert len(result.stdout) > 0

    def test_variable_registered_exits_0(self) -> None:
        """variable_registered.py (extend+register pattern) exits 0."""
        result = _run_cli(str(FIXTURES_DIR / "variable_registered.py"))
        assert result.returncode == 0

    def test_no_args_exits_nonzero(self) -> None:
        """Running with no arguments exits with a non-zero code."""
        result = _run_cli()
        assert result.returncode != 0

    def test_output_format_warning(self) -> None:
        """WARNING output includes file path, line number, and message."""
        result = _run_cli(str(FIXTURES_DIR / "fire_and_forget.py"))
        # Should have format: "WARNING: path:line — message"
        assert "WARNING:" in result.stdout
        assert "fire-and-forget" in result.stdout

    def test_output_format_error(self) -> None:
        """ERROR output includes file path and event names."""
        result = _run_cli(str(FIXTURES_DIR / "unregistered_event.py"))
        assert "ERROR:" in result.stdout
        assert "custom:unregistered" in result.stdout

    def test_inline_content_fire_and_forget(self, tmp_path: Path) -> None:
        """Fire-and-forget in a temp file is detected.

        Uses a canonical event (session:*) so only a WARNING is produced — no
        ERROR for unregistered event, which would confuse the exit code.
        """
        f = tmp_path / "module.py"
        f.write_text(
            "import asyncio\n\n"
            "async def handle(hooks):\n"
            '    asyncio.create_task(hooks.emit("session:ready", {}))\n'
        )
        result = _run_cli(str(f))
        assert "WARNING" in result.stdout
        assert result.returncode == 0  # warning only, no errors

    def test_inline_content_unregistered_error(self, tmp_path: Path) -> None:
        """Unregistered event in a temp file produces error and exit 1."""
        f = tmp_path / "module.py"
        f.write_text(
            "async def mount(coordinator, config=None):\n"
            '    coordinator.register_capability("observability.events", ["ns:ok"])\n'
            "\n"
            "async def handle(hooks):\n"
            '    await hooks.emit("ns:ok", {})\n'
            '    await hooks.emit("ns:missing", {})\n'
        )
        result = _run_cli(str(f))
        assert result.returncode == 1
        assert "ns:missing" in result.stdout

    def test_inline_content_clean(self, tmp_path: Path) -> None:
        """Clean file with registered events exits 0."""
        f = tmp_path / "module.py"
        f.write_text(
            "async def mount(coordinator, config=None):\n"
            '    coordinator.register_capability("observability.events", ["ns:event"])\n'
            "\n"
            "async def handle(hooks):\n"
            '    await hooks.emit("ns:event", {})\n'
        )
        result = _run_cli(str(f))
        assert result.returncode == 0
        assert "ERROR" not in result.stdout
