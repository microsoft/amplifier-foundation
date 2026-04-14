"""Tests for scripts/generate_event_dot.py DOT generation tool."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

# Load generate_event_dot.py as a module (it's a script, not a package)
_script_path = (
    Path(__file__).resolve().parent.parent / "scripts" / "generate_event_dot.py"
)
_spec = importlib.util.spec_from_file_location("generate_event_dot", _script_path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load {_script_path}")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

find_python_files = _mod.find_python_files
get_module_name = _mod.get_module_name
scan_module = _mod.scan_module
generate_dot = _mod.generate_dot
generate_json = _mod.generate_json
is_canonical_event = _mod.is_canonical_event

# Fixtures directory
FIXTURES_DIR = (
    Path(__file__).resolve().parent.parent / "test-fixtures" / "generate_event_dot"
)


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    """Run generate_event_dot.py as a subprocess with the given arguments."""
    return subprocess.run(
        [sys.executable, str(_script_path), *args],
        capture_output=True,
        text=True,
        timeout=15,
    )


# ---------------------------------------------------------------------------
# TestGetModuleName
# ---------------------------------------------------------------------------


class TestGetModuleName:
    """Tests for get_module_name — derives a human-readable module name from path."""

    def test_file_in_named_dir_uses_parent(self) -> None:
        """A file inside a named module dir yields the dir name as module name."""
        path = Path("modules/tool-delegate/mount.py")
        assert get_module_name(path) == "tool-delegate"

    def test_file_in_nested_dir_uses_immediate_parent(self) -> None:
        """Uses immediate parent directory name, not grandparent."""
        path = Path("modules/tool-recipes/subpkg/handler.py")
        assert get_module_name(path) == "subpkg"

    def test_file_at_root_uses_stem(self) -> None:
        """A top-level file (no meaningful parent) uses the file stem."""
        path = Path("module.py")
        assert get_module_name(path) == "module"

    def test_absolute_path_uses_parent_dir(self, tmp_path: Path) -> None:
        """Absolute path inside a module dir yields directory name."""
        module_dir = tmp_path / "tool-delegate"
        module_dir.mkdir()
        f = module_dir / "mount.py"
        f.write_text("x = 1\n")
        assert get_module_name(f) == "tool-delegate"

    def test_single_component_directory(self) -> None:
        """File with a single-level parent uses parent name."""
        path = Path("tool-recipes/main.py")
        assert get_module_name(path) == "tool-recipes"


# ---------------------------------------------------------------------------
# TestScanModule
# ---------------------------------------------------------------------------


class TestScanModule:
    """Tests for scan_module — extracts emitted and registered events from a file."""

    def test_returns_module_name(self) -> None:
        """scan_module returns the correct module name from the file path."""
        path = FIXTURES_DIR / "module_with_registered.py"
        result = scan_module(path)
        assert result["module"] == "generate_event_dot"

    def test_returns_emitted_events(self) -> None:
        """scan_module extracts emitted event names."""
        path = FIXTURES_DIR / "module_with_registered.py"
        result = scan_module(path)
        assert "delegate:agent_spawned" in result["emitted"]
        assert "delegate:agent_completed" in result["emitted"]

    def test_returns_registered_events(self) -> None:
        """scan_module extracts registered event names."""
        path = FIXTURES_DIR / "module_with_registered.py"
        result = scan_module(path)
        assert "delegate:agent_spawned" in result["registered"]
        assert "delegate:agent_completed" in result["registered"]

    def test_emitted_not_registered_included(self) -> None:
        """scan_module captures events emitted but not registered."""
        path = FIXTURES_DIR / "module_with_unregistered.py"
        result = scan_module(path)
        assert "recipe:unregistered" in result["emitted"]
        assert "recipe:unregistered" not in result["registered"]

    def test_canonical_events_in_emitted(self) -> None:
        """scan_module includes canonical events in emitted (for graph completeness)."""
        path = FIXTURES_DIR / "module_with_canonical.py"
        result = scan_module(path)
        assert "session:start" in result["emitted"]
        assert "llm:request" in result["emitted"]

    def test_module_name_uses_parent_dir(self, tmp_path: Path) -> None:
        """Module name is derived from parent directory, not file name."""
        mod_dir = tmp_path / "my-module"
        mod_dir.mkdir()
        f = mod_dir / "handler.py"
        f.write_text('await hooks.emit("my:event", {})\n')
        result = scan_module(f)
        assert result["module"] == "my-module"


# ---------------------------------------------------------------------------
# TestGenerateDot
# ---------------------------------------------------------------------------


class TestGenerateDot:
    """Tests for generate_dot — produces a DOT graph from module scan data."""

    def _make_data(
        self,
        module: str,
        emitted: dict[str, int],
        registered: set[str],
    ) -> list[dict]:
        return [{"module": module, "emitted": emitted, "registered": registered}]

    def test_output_is_digraph(self) -> None:
        """DOT output opens with 'digraph'."""
        data = self._make_data("tool-delegate", {}, set())
        dot = generate_dot(data)
        assert "digraph" in dot

    def test_contains_module_node(self) -> None:
        """DOT output contains a box node for the module."""
        data = self._make_data("tool-delegate", {}, set())
        dot = generate_dot(data)
        assert '"tool-delegate"' in dot
        assert "shape=box" in dot

    def test_module_node_filled_lightblue(self) -> None:
        """Module nodes are filled lightblue."""
        data = self._make_data("tool-delegate", {}, set())
        dot = generate_dot(data)
        assert "lightblue" in dot

    def test_contains_event_node(self) -> None:
        """DOT output contains an oval node for each event."""
        data = self._make_data(
            "tool-delegate",
            {"delegate:agent_spawned": 1},
            {"delegate:agent_spawned"},
        )
        dot = generate_dot(data)
        assert '"delegate:agent_spawned"' in dot
        assert "shape=oval" in dot

    def test_registered_event_is_lightgreen(self) -> None:
        """Registered (known) events get a lightgreen fill."""
        data = self._make_data(
            "tool-delegate",
            {"delegate:agent_spawned": 1},
            {"delegate:agent_spawned"},
        )
        dot = generate_dot(data)
        assert "lightgreen" in dot

    def test_unregistered_event_is_red(self) -> None:
        """Events emitted but not registered and not canonical get a red fill."""
        data = self._make_data(
            "tool-recipes",
            {"recipe:missing": 1},
            set(),
        )
        dot = generate_dot(data)
        # Should indicate a warning/red color for unregistered non-canonical events
        assert "red" in dot.lower() or "orange" in dot.lower()

    def test_canonical_event_different_color(self) -> None:
        """Canonical (amplifier_core) events have a distinct color from registered/unregistered."""
        data = self._make_data(
            "some-module",
            {"session:start": 1},
            set(),
        )
        dot = generate_dot(data)
        # Canonical events should not be red (they don't need registration)
        # and should have a distinct style — lightyellow is the expected color
        assert "lightyellow" in dot

    def test_contains_edge_module_to_event(self) -> None:
        """DOT output has an edge from module to emitted event."""
        data = self._make_data(
            "tool-delegate",
            {"delegate:agent_spawned": 1},
            {"delegate:agent_spawned"},
        )
        dot = generate_dot(data)
        assert '"tool-delegate" -> "delegate:agent_spawned"' in dot

    def test_multiple_modules(self) -> None:
        """DOT output handles multiple modules correctly."""
        data = [
            {
                "module": "tool-delegate",
                "emitted": {"delegate:agent_spawned": 1},
                "registered": {"delegate:agent_spawned"},
            },
            {
                "module": "tool-recipes",
                "emitted": {"recipe:start": 1},
                "registered": {"recipe:start"},
            },
        ]
        dot = generate_dot(data)
        assert '"tool-delegate"' in dot
        assert '"tool-recipes"' in dot
        assert '"delegate:agent_spawned"' in dot
        assert '"recipe:start"' in dot
        assert '"tool-delegate" -> "delegate:agent_spawned"' in dot
        assert '"tool-recipes" -> "recipe:start"' in dot

    def test_deduplicates_shared_events(self) -> None:
        """An event emitted by multiple modules appears only once as a node."""
        data = [
            {
                "module": "module-a",
                "emitted": {"shared:event": 1},
                "registered": {"shared:event"},
            },
            {
                "module": "module-b",
                "emitted": {"shared:event": 1},
                "registered": {"shared:event"},
            },
        ]
        dot = generate_dot(data)
        # Count occurrences of the event node definition
        # A node definition has [shape=oval...], edges just use the name
        node_definitions = [
            line
            for line in dot.splitlines()
            if '"shared:event"' in line and "shape=oval" in line
        ]
        assert len(node_definitions) == 1

    def test_rankdir_lr(self) -> None:
        """DOT output uses left-to-right layout."""
        data = self._make_data("my-module", {}, set())
        dot = generate_dot(data)
        assert "rankdir=LR" in dot

    def test_empty_data_produces_valid_digraph(self) -> None:
        """Empty data still produces a valid (empty) digraph."""
        dot = generate_dot([])
        assert "digraph" in dot
        assert "{" in dot
        assert "}" in dot


# ---------------------------------------------------------------------------
# TestGenerateJson
# ---------------------------------------------------------------------------


class TestGenerateJson:
    """Tests for generate_json — produces JSON output from module scan data."""

    def _make_data(
        self,
        module: str,
        emitted: dict[str, int],
        registered: set[str],
    ) -> list[dict]:
        return [{"module": module, "emitted": emitted, "registered": registered}]

    def test_returns_valid_json(self) -> None:
        """generate_json returns valid JSON string."""
        data = self._make_data("tool-delegate", {}, set())
        result = json.loads(generate_json(data))
        assert isinstance(result, dict)

    def test_contains_modules_key(self) -> None:
        """JSON output has a 'modules' top-level key."""
        data = self._make_data("tool-delegate", {}, set())
        result = json.loads(generate_json(data))
        assert "modules" in result

    def test_module_entry_has_name(self) -> None:
        """Each module entry has a 'module' field."""
        data = self._make_data("tool-delegate", {}, set())
        result = json.loads(generate_json(data))
        assert result["modules"][0]["module"] == "tool-delegate"

    def test_module_entry_has_emitted(self) -> None:
        """Each module entry has an 'emitted' list."""
        data = self._make_data("tool-delegate", {"delegate:agent_spawned": 1}, set())
        result = json.loads(generate_json(data))
        assert "emitted" in result["modules"][0]
        assert "delegate:agent_spawned" in result["modules"][0]["emitted"]

    def test_module_entry_has_registered(self) -> None:
        """Each module entry has a 'registered' list."""
        data = self._make_data(
            "tool-delegate",
            {"delegate:agent_spawned": 1},
            {"delegate:agent_spawned"},
        )
        result = json.loads(generate_json(data))
        assert "registered" in result["modules"][0]
        assert "delegate:agent_spawned" in result["modules"][0]["registered"]

    def test_multiple_modules_in_json(self) -> None:
        """JSON output includes all modules."""
        data = [
            {
                "module": "tool-delegate",
                "emitted": {"delegate:agent_spawned": 1},
                "registered": {"delegate:agent_spawned"},
            },
            {
                "module": "tool-recipes",
                "emitted": {"recipe:start": 1},
                "registered": {"recipe:start"},
            },
        ]
        result = json.loads(generate_json(data))
        module_names = [m["module"] for m in result["modules"]]
        assert "tool-delegate" in module_names
        assert "tool-recipes" in module_names

    def test_registered_is_sorted_list(self) -> None:
        """Registered events are output as a sorted list (deterministic)."""
        data = self._make_data(
            "tool-delegate",
            {"delegate:z": 1, "delegate:a": 2},
            {"delegate:z", "delegate:a"},
        )
        result = json.loads(generate_json(data))
        registered = result["modules"][0]["registered"]
        assert registered == sorted(registered)

    def test_emitted_is_sorted_list(self) -> None:
        """Emitted events are output as a sorted list (deterministic)."""
        data = self._make_data(
            "tool-delegate",
            {"delegate:z": 1, "delegate:a": 2},
            set(),
        )
        result = json.loads(generate_json(data))
        emitted = result["modules"][0]["emitted"]
        assert emitted == sorted(emitted)


# ---------------------------------------------------------------------------
# TestIsCanonicalEvent
# ---------------------------------------------------------------------------


class TestIsCanonicalEvent:
    """Tests for is_canonical_event — same logic as lint_observability."""

    def test_session_is_canonical(self) -> None:
        assert is_canonical_event("session:start") is True

    def test_llm_is_canonical(self) -> None:
        assert is_canonical_event("llm:request") is True

    def test_delegate_is_not_canonical(self) -> None:
        assert is_canonical_event("delegate:agent_spawned") is False

    def test_recipe_is_not_canonical(self) -> None:
        assert is_canonical_event("recipe:start") is False


# ---------------------------------------------------------------------------
# TestCLI — subprocess-based end-to-end tests
# ---------------------------------------------------------------------------


class TestCLI:
    """Subprocess-based CLI tests for generate_event_dot.py."""

    def test_dot_format_default(self) -> None:
        """Default output (no --format) is DOT format with digraph."""
        result = _run_cli(str(FIXTURES_DIR / "module_with_registered.py"))
        assert result.returncode == 0
        assert "digraph" in result.stdout

    def test_dot_format_explicit(self) -> None:
        """--format dot produces DOT output."""
        result = _run_cli(
            str(FIXTURES_DIR / "module_with_registered.py"), "--format", "dot"
        )
        assert result.returncode == 0
        assert "digraph" in result.stdout

    def test_json_format_flag(self) -> None:
        """--format json produces valid JSON output."""
        result = _run_cli(
            str(FIXTURES_DIR / "module_with_registered.py"), "--format", "json"
        )
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        assert "modules" in parsed

    def test_json_contains_module(self) -> None:
        """JSON output from CLI contains the module name."""
        result = _run_cli(
            str(FIXTURES_DIR / "module_with_registered.py"), "--format", "json"
        )
        parsed = json.loads(result.stdout)
        module_names = [m["module"] for m in parsed["modules"]]
        assert "generate_event_dot" in module_names

    def test_dot_output_has_module_node(self) -> None:
        """DOT output from CLI contains the module as a box node."""
        result = _run_cli(str(FIXTURES_DIR / "module_with_registered.py"))
        assert "shape=box" in result.stdout

    def test_dot_output_has_event_nodes(self) -> None:
        """DOT output from CLI contains event oval nodes."""
        result = _run_cli(str(FIXTURES_DIR / "module_with_registered.py"))
        assert "delegate:agent_spawned" in result.stdout
        assert "shape=oval" in result.stdout

    def test_directory_scan_works(self) -> None:
        """Accepts a directory path and scans all .py files."""
        result = _run_cli(str(FIXTURES_DIR))
        assert result.returncode == 0
        assert "digraph" in result.stdout

    def test_no_args_exits_nonzero(self) -> None:
        """Running with no arguments exits with a non-zero code."""
        result = _run_cli()
        assert result.returncode != 0

    def test_module_name_from_custom_dir(self, tmp_path: Path) -> None:
        """CLI derives module name from parent directory name."""
        mod_dir = tmp_path / "my-custom-module"
        mod_dir.mkdir()
        f = mod_dir / "handler.py"
        f.write_text(
            "async def mount(coordinator, config=None):\n"
            '    coordinator.register_capability("observability.events", ["my:event"])\n'
            "\n"
            "async def handle(hooks):\n"
            '    await hooks.emit("my:event", {})\n'
        )
        result = _run_cli(str(f))
        assert result.returncode == 0
        assert "my-custom-module" in result.stdout

    def test_json_unregistered_events_present(self) -> None:
        """JSON output includes unregistered events in the emitted list."""
        result = _run_cli(
            str(FIXTURES_DIR / "module_with_unregistered.py"), "--format", "json"
        )
        parsed = json.loads(result.stdout)
        all_emitted = [e for m in parsed["modules"] for e in m["emitted"]]
        assert "recipe:unregistered" in all_emitted
