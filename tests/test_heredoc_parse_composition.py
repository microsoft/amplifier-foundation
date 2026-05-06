"""Tests for git+https:// URL resolution in the parse-composition heredoc.

The parse_bundle_composition code is embedded as a PYEOF heredoc inside
recipes/bundle-behavioral-model.yaml.  These tests extract that Python
source at import time and exec() it so that the functions under test
(resolve_behavior_includes, BehaviorYamlFields, etc.) are exercised
directly from the canonical YAML -- no standalone copy to drift.
"""

from __future__ import annotations

import textwrap
from pathlib import Path


# ---------------------------------------------------------------------------
# Heredoc extraction
# ---------------------------------------------------------------------------

_RECIPE_PATH = Path(__file__).parent.parent / "recipes" / "bundle-behavioral-model.yaml"


def _extract_pyeof_block(yaml_path: Path, occurrence: int = 0) -> str:
    """Extract the *occurrence*-th PYEOF heredoc from *yaml_path*.

    Returns the Python source with common leading whitespace stripped.
    """
    lines = yaml_path.read_text().splitlines(keepends=True)
    blocks: list[str] = []
    in_block = False
    block_lines: list[str] = []
    for line in lines:
        if not in_block:
            if "<< 'PYEOF'" in line:
                in_block = True
                block_lines = []
        elif line.strip() == "PYEOF":
            blocks.append("".join(block_lines))
            in_block = False
        else:
            block_lines.append(line)
    if occurrence >= len(blocks):
        raise ValueError(
            f"Only {len(blocks)} PYEOF blocks in {yaml_path}, requested #{occurrence}"
        )
    return textwrap.dedent(blocks[occurrence])


# ---------------------------------------------------------------------------
# Exec the first PYEOF block (parse-composition step) into a namespace
# ---------------------------------------------------------------------------

_ns: dict = {}
_source = _extract_pyeof_block(_RECIPE_PATH, occurrence=0)
# Compile with a meaningful filename so tracebacks are readable.
_code = compile(_source, f"{_RECIPE_PATH}::PYEOF[0]", "exec")
exec(_code, _ns)  # noqa: S102

# Pull the symbols needed by the tests into module scope.
BehaviorYamlFields = _ns["BehaviorYamlFields"]
resolve_behavior_includes = _ns["resolve_behavior_includes"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGitHttpsUrlResolution:
    """resolve_behavior_includes resolves git+https:// URL refs."""

    def test_resolves_nested_behavior_via_git_url(self, tmp_path: Path) -> None:
        """A git+https:// URL with #subdirectory= resolves a behavior YAML."""
        modes_dir = tmp_path / "modes-bundle"
        behaviors_dir = modes_dir / "behaviors"
        behaviors_dir.mkdir(parents=True)
        (behaviors_dir / "modes.yaml").write_text(
            "bundle:\n  name: modes-behavior\n  description: Mode definitions\n"
        )

        bundles = {
            "my-bundle": {
                "name": "my-bundle",
                "uri": "git+https://github.com/example/my-bundle@main",
                "local_path": str(tmp_path / "my-bundle-dir"),
                "includes": ["amplifier-bundle-modes"],
            },
            "amplifier-bundle-modes": {
                "name": "amplifier-bundle-modes",
                "uri": "git+https://github.com/microsoft/amplifier-bundle-modes@main",
                "local_path": str(modes_dir),
                "includes": [],
            },
        }

        behavior_fields = BehaviorYamlFields(
            name="my-behavior",
            tool_modules=[],
            hook_modules=[],
            context_includes=[],
            agent_includes=[],
            nested_behavior_includes=[
                "git+https://github.com/microsoft/amplifier-bundle-modes@main"
                "#subdirectory=behaviors/modes.yaml"
            ],
        )

        results = resolve_behavior_includes(behavior_fields, bundles, "my-bundle")

        assert len(results) == 1
        assert results[0].component_type == "behavior_yaml"
        assert results[0].behavior_yaml is not None
        assert results[0].behavior_yaml.name == "modes-behavior"

    def test_resolves_agent_include_via_git_url(self, tmp_path: Path) -> None:
        """A git+https:// URL resolves an agent include."""
        bundle_dir = tmp_path / "ext-bundle"
        agents_dir = bundle_dir / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "specialist.md").write_text(
            "---\n"
            "meta:\n"
            "  name: specialist\n"
            "  description: A specialist agent\n"
            "model_role: general\n"
            "tools: []\n"
            "---\n"
            "Specialist instructions.\n"
        )

        bundles = {
            "ext-bundle": {
                "name": "ext-bundle",
                "uri": "git+https://github.com/example/ext-bundle@v2",
                "local_path": str(bundle_dir),
                "includes": [],
            },
        }

        behavior_fields = BehaviorYamlFields(
            name="my-behavior",
            tool_modules=[],
            hook_modules=[],
            context_includes=[],
            agent_includes=[
                "git+https://github.com/example/ext-bundle@v2"
                "#subdirectory=agents/specialist.md"
            ],
            nested_behavior_includes=[],
        )

        results = resolve_behavior_includes(behavior_fields, bundles, "my-bundle")

        assert len(results) == 1
        assert results[0].component_type == "agent"
        assert results[0].agent is not None
        assert results[0].agent.name == "specialist"

    def test_resolves_context_include_via_git_url(self, tmp_path: Path) -> None:
        """A git+https:// URL resolves a context include with eager_mentions."""
        bundle_dir = tmp_path / "ctx-bundle"
        context_dir = bundle_dir / "context"
        context_dir.mkdir(parents=True)
        (context_dir / "guide.md").write_text(
            "# Guide\nSee @foundation:context/common.md for details.\n"
        )

        bundles = {
            "ctx-bundle": {
                "name": "ctx-bundle",
                "uri": "git+https://github.com/example/ctx-bundle@main",
                "local_path": str(bundle_dir),
                "includes": [],
            },
        }

        behavior_fields = BehaviorYamlFields(
            name="my-behavior",
            tool_modules=[],
            hook_modules=[],
            context_includes=[
                "git+https://github.com/example/ctx-bundle@main"
                "#subdirectory=context/guide.md"
            ],
            agent_includes=[],
            nested_behavior_includes=[],
        )

        results = resolve_behavior_includes(behavior_fields, bundles, "my-bundle")

        assert len(results) == 1
        assert results[0].component_type == "context_file"
        assert "@foundation:context/common.md" in results[0].eager_mentions

    def test_git_url_no_matching_bundle_returns_empty(self) -> None:
        """A git+https:// URL matching no bundle URI is silently skipped."""
        bundles = {
            "some-bundle": {
                "name": "some-bundle",
                "uri": "git+https://github.com/example/some-bundle@main",
                "local_path": "/nonexistent",
                "includes": [],
            },
        }

        behavior_fields = BehaviorYamlFields(
            name="my-behavior",
            tool_modules=[],
            hook_modules=[],
            context_includes=[],
            agent_includes=[],
            nested_behavior_includes=[
                "git+https://github.com/example/no-such-bundle@main"
                "#subdirectory=behaviors/foo.yaml"
            ],
        )

        results = resolve_behavior_includes(behavior_fields, bundles, "my-bundle")

        assert results == []

    def test_git_url_without_subdirectory_resolves_to_bundle_root(
        self, tmp_path: Path
    ) -> None:
        """A git+https:// URL without #subdirectory= falls back to the bundle root."""
        bundle_dir = tmp_path / "root-bundle"
        bundle_dir.mkdir()
        # Without a subdirectory the resolver looks for bundle.md/bundle.yaml
        # at the root, then falls back to the directory itself.  Either way
        # the resolution must not raise.
        bundles = {
            "root-bundle": {
                "name": "root-bundle",
                "uri": "git+https://github.com/example/root-bundle@main",
                "local_path": str(bundle_dir),
                "includes": [],
            },
        }

        behavior_fields = BehaviorYamlFields(
            name="my-behavior",
            tool_modules=[],
            hook_modules=[],
            context_includes=[],
            agent_includes=[],
            nested_behavior_includes=[
                "git+https://github.com/example/root-bundle@main"
            ],
        )

        # Should not raise
        results = resolve_behavior_includes(behavior_fields, bundles, "my-bundle")
        assert isinstance(results, list)
