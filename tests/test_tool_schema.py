"""Tests for tool_schema.py - module tool schema token estimation."""

from pathlib import Path


from amplifier_foundation.bundle_docs.tool_schema import estimate_module_tool_tokens

MODULES_DIR = Path(__file__).resolve().parent.parent / "modules"
TOOL_DELEGATE_DIR = MODULES_DIR / "tool-delegate"
HOOKS_PROGRESS_MONITOR_DIR = MODULES_DIR / "hooks-progress-monitor"


class TestEstimateModuleToolTokens:
    """Tests for estimate_module_tool_tokens()."""

    # ── 1. Non-existent directory ─────────────────────────────────────────────

    def test_returns_none_for_nonexistent_module(self, tmp_path: Path) -> None:
        result = estimate_module_tool_tokens(tmp_path / "nonexistent-module")
        assert result is None

    # ── 2. Empty __init__.py ──────────────────────────────────────────────────

    def test_returns_none_for_empty_module(self, tmp_path: Path) -> None:
        pkg_dir = tmp_path / "amplifier_module_empty"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text("")
        result = estimate_module_tool_tokens(tmp_path)
        assert result is None

    # ── 3. Synthetic single-tool module ──────────────────────────────────────

    def test_extracts_tool_count_and_tokens(self, tmp_path: Path) -> None:
        pkg_dir = tmp_path / "amplifier_module_test"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text(
            "class MyTool:\n"
            '    name = "my_tool"\n'
            '    description = "A test tool that does things."\n'
            "\n"
            "    @property\n"
            "    def input_schema(self):\n"
            "        return {\n"
            '            "type": "object",\n'
            '            "properties": {\n'
            '                "query": {\n'
            '                    "type": "string",\n'
            '                    "description": "The query string",\n'
            "                }\n"
            "            },\n"
            '            "required": ["query"],\n'
            "        }\n"
        )
        result = estimate_module_tool_tokens(tmp_path)
        assert result is not None
        assert result["tool_count"] == 1
        assert result["total_tokens"] > 0
        assert len(result["tools"]) == 1
        tool = result["tools"][0]
        assert tool["name"] == "my_tool"
        assert tool["schema_tokens"] > 0
        assert tool["total_tokens"] > 0

    # ── 4. Hook class (no input_schema) ──────────────────────────────────────

    def test_hook_module_returns_none(self, tmp_path: Path) -> None:
        pkg_dir = tmp_path / "amplifier_module_hook"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text(
            "class MyHook:\n"
            "    async def handle(self, event, data):\n"
            "        pass\n"
            "\n"
            "async def mount(coordinator, config=None):\n"
            "    pass\n"
        )
        result = estimate_module_tool_tokens(tmp_path)
        assert result is None

    # ── 5. Real tool-delegate module ──────────────────────────────────────────

    def test_real_tool_delegate_module(self) -> None:
        result = estimate_module_tool_tokens(TOOL_DELEGATE_DIR)
        assert result is not None
        assert result["tool_count"] >= 1
        assert result["total_tokens"] > 50
        assert len(result["tools"]) >= 1
        tool = result["tools"][0]
        assert tool["name"] == "delegate"
        assert tool["schema_tokens"] > 0

    # ── 6. Real hook module → None ────────────────────────────────────────────

    def test_real_hook_module_returns_none(self) -> None:
        result = estimate_module_tool_tokens(HOOKS_PROGRESS_MONITOR_DIR)
        assert result is None

    # ── 7. Multiple tools in one module ──────────────────────────────────────

    def test_multiple_tools_in_one_module(self, tmp_path: Path) -> None:
        pkg_dir = tmp_path / "amplifier_module_multi"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text(
            "class ToolA:\n"
            '    name = "tool_a"\n'
            '    description = "Tool A description."\n'
            "\n"
            "    @property\n"
            "    def input_schema(self):\n"
            '        return {"type": "object", "properties": {"x": {"type": "string"}}}\n'
            "\n"
            "class ToolB:\n"
            '    name = "tool_b"\n'
            '    description = "Tool B description."\n'
            "\n"
            "    @property\n"
            "    def input_schema(self):\n"
            '        return {"type": "object", "properties": {"y": {"type": "integer"}}}\n'
        )
        result = estimate_module_tool_tokens(tmp_path)
        assert result is not None
        assert result["tool_count"] == 2
        assert len(result["tools"]) == 2
        names = {t["name"] for t in result["tools"]}
        assert names == {"tool_a", "tool_b"}
        assert result["total_tokens"] == sum(t["total_tokens"] for t in result["tools"])

    # ── 8. Malformed source ───────────────────────────────────────────────────

    def test_malformed_source_returns_none(self, tmp_path: Path) -> None:
        pkg_dir = tmp_path / "amplifier_module_bad"
        pkg_dir.mkdir()
        # Has class + input_schema keyword but unclosed dict → extraction fails
        (pkg_dir / "__init__.py").write_text(
            "class Bad:\n"
            '    name = "bad"\n'
            "    def input_schema(self):\n"
            "        return {{{invalid syntax here\n"
        )
        result = estimate_module_tool_tokens(tmp_path)
        assert result is None
