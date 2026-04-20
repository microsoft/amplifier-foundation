"""Tests for YAML Structure Lint detection (v2.1.0 / v3.4.0).

Verifies that the lint logic correctly detects:
- Bug 1: includes: nested under bundle: (silently dropped by parser)
- Bug 2: Unrecognized dict keys in includes entries (only 'bundle:' valid)

These tests validate BOTH:
1. The lint logic itself (unit tests with in-memory YAML)
2. The recipe structure (integration tests on the recipe YAML files)
"""

import textwrap
from pathlib import Path

import pytest
import yaml


# =============================================================================
# FIXTURES
# =============================================================================

RECIPE_DIR = Path(__file__).parent.parent / "recipes"
SINGLE_BUNDLE_RECIPE = RECIPE_DIR / "validate-single-bundle.yaml"
BUNDLE_REPO_RECIPE = RECIPE_DIR / "validate-bundle-repo.yaml"


@pytest.fixture(scope="module")
def single_bundle_recipe():
    """Load validate-single-bundle.yaml."""
    if not SINGLE_BUNDLE_RECIPE.exists():
        pytest.skip("validate-single-bundle.yaml not found")
    content = SINGLE_BUNDLE_RECIPE.read_text(encoding="utf-8")
    return yaml.safe_load(content), content


@pytest.fixture(scope="module")
def single_bundle_steps(single_bundle_recipe):
    """Build a dict of steps keyed by id."""
    data, _ = single_bundle_recipe
    return {step["id"]: step for step in data.get("steps", []) if "id" in step}


@pytest.fixture(scope="module")
def bundle_repo_recipe():
    """Load validate-bundle-repo.yaml."""
    if not BUNDLE_REPO_RECIPE.exists():
        pytest.skip("validate-bundle-repo.yaml not found")
    content = BUNDLE_REPO_RECIPE.read_text(encoding="utf-8")
    return yaml.safe_load(content), content


@pytest.fixture(scope="module")
def bundle_repo_steps(bundle_repo_recipe):
    """Build a dict of steps keyed by id."""
    data, _ = bundle_repo_recipe
    return {step["id"]: step for step in data.get("steps", []) if "id" in step}


# =============================================================================
# UNIT TESTS: Lint Logic (in-memory YAML parsing)
# =============================================================================


def lint_yaml_data(yaml_data: dict) -> dict:
    """Pure-logic lint check — same algorithm as the recipe step.

    Returns {"passed": bool, "errors": [...]}
    """
    errors = []

    # Bug 1: includes nested under bundle
    bundle_section = yaml_data.get("bundle")
    if isinstance(bundle_section, dict) and "includes" in bundle_section:
        nested = bundle_section["includes"]
        count = len(nested) if isinstance(nested, list) else 1
        errors.append(
            {
                "type": "nested_includes",
                "count": count,
            }
        )

    # Bug 2: unrecognized dict keys in includes
    top_includes = yaml_data.get("includes", [])
    if isinstance(top_includes, list):
        for idx, inc in enumerate(top_includes):
            if isinstance(inc, dict):
                if "bundle" not in inc:
                    errors.append(
                        {
                            "type": "unrecognized_include_key",
                            "index": idx,
                            "keys": list(inc.keys()),
                        }
                    )

    return {"passed": len(errors) == 0, "errors": errors}


class TestLintLogicBug1NestedIncludes:
    """Bug 1: includes: nested under bundle: instead of top-level."""

    def test_detects_nested_includes(self):
        """Nested includes under bundle: should be flagged as ERROR."""
        data = yaml.safe_load(
            textwrap.dedent("""
            bundle:
              name: test-bundle
              version: "1.0.0"
              includes:
                - bundle: foundation
                - bundle: foundation:behaviors/agents
        """)
        )
        result = lint_yaml_data(data)
        assert not result["passed"]
        assert len(result["errors"]) == 1
        assert result["errors"][0]["type"] == "nested_includes"
        assert result["errors"][0]["count"] == 2

    def test_top_level_includes_pass(self):
        """Top-level includes should NOT be flagged."""
        data = yaml.safe_load(
            textwrap.dedent("""
            bundle:
              name: test-bundle
            includes:
              - bundle: foundation
              - bundle: foundation:behaviors/agents
        """)
        )
        result = lint_yaml_data(data)
        assert result["passed"]
        assert len(result["errors"]) == 0

    def test_no_includes_at_all_passes(self):
        """Bundle with no includes should pass."""
        data = yaml.safe_load(
            textwrap.dedent("""
            bundle:
              name: test-bundle
              version: "1.0.0"
        """)
        )
        result = lint_yaml_data(data)
        assert result["passed"]

    def test_nested_single_include(self):
        """Even a single nested include should be caught."""
        data = yaml.safe_load(
            textwrap.dedent("""
            bundle:
              name: test
              includes:
                - bundle: foundation
        """)
        )
        result = lint_yaml_data(data)
        assert not result["passed"]
        assert result["errors"][0]["count"] == 1

    def test_both_nested_and_top_level(self):
        """If includes exist both nested AND top-level, catch the nested one."""
        data = yaml.safe_load(
            textwrap.dedent("""
            bundle:
              name: test
              includes:
                - bundle: this-is-hidden
            includes:
              - bundle: this-is-visible
        """)
        )
        result = lint_yaml_data(data)
        assert not result["passed"]
        nested_errors = [e for e in result["errors"] if e["type"] == "nested_includes"]
        assert len(nested_errors) == 1


class TestLintLogicBug2UnrecognizedKeys:
    """Bug 2: Unrecognized dict keys in includes entries."""

    def test_detects_behavior_key(self):
        """'behavior:' key should be flagged — only 'bundle:' is valid."""
        data = yaml.safe_load(
            textwrap.dedent("""
            bundle:
              name: test
            includes:
              - behavior: ./behaviors/foo.yaml
        """)
        )
        result = lint_yaml_data(data)
        assert not result["passed"]
        assert result["errors"][0]["type"] == "unrecognized_include_key"
        assert "behavior" in result["errors"][0]["keys"]

    def test_detects_include_key(self):
        """'include:' key should be flagged."""
        data = yaml.safe_load(
            textwrap.dedent("""
            bundle:
              name: test
            includes:
              - include: foundation:behaviors/agents
        """)
        )
        result = lint_yaml_data(data)
        assert not result["passed"]

    def test_bundle_key_passes(self):
        """'bundle:' key should NOT be flagged — it's the valid key."""
        data = yaml.safe_load(
            textwrap.dedent("""
            bundle:
              name: test
            includes:
              - bundle: foundation
              - bundle: foundation:behaviors/agents
              - bundle: git+https://github.com/foo/bar
        """)
        )
        result = lint_yaml_data(data)
        assert result["passed"]

    def test_bare_string_includes_pass(self):
        """Bare string includes should NOT be flagged."""
        data = yaml.safe_load(
            textwrap.dedent("""
            bundle:
              name: test
            includes:
              - foundation
              - dot-graph
        """)
        )
        result = lint_yaml_data(data)
        assert result["passed"]

    def test_mixed_valid_and_invalid(self):
        """Mix of valid and invalid should catch only the invalid ones."""
        data = yaml.safe_load(
            textwrap.dedent("""
            bundle:
              name: test
            includes:
              - bundle: foundation
              - behavior: ./behaviors/foo.yaml
              - bundle: dot-graph:behaviors/dot-graph
              - include: something
        """)
        )
        result = lint_yaml_data(data)
        assert not result["passed"]
        assert len(result["errors"]) == 2
        # First bad entry is at index 1, second at index 3
        assert result["errors"][0]["index"] == 1
        assert result["errors"][1]["index"] == 3

    def test_multiple_unrecognized_keys_in_one_entry(self):
        """Dict with multiple non-bundle keys should be caught."""
        data = yaml.safe_load(
            textwrap.dedent("""
            bundle:
              name: test
            includes:
              - behavior: foo
                path: ./foo.yaml
        """)
        )
        result = lint_yaml_data(data)
        assert not result["passed"]
        # Both 'behavior' and 'path' are unrecognized
        assert "behavior" in result["errors"][0]["keys"]
        assert "path" in result["errors"][0]["keys"]


class TestLintLogicBothBugs:
    """Both bugs present simultaneously."""

    def test_both_bugs_detected(self):
        """Should detect BOTH nested includes AND unrecognized keys."""
        data = yaml.safe_load(
            textwrap.dedent("""
            bundle:
              name: test
              includes:
                - bundle: hidden-foundation
            includes:
              - behavior: ./behaviors/foo.yaml
        """)
        )
        result = lint_yaml_data(data)
        assert not result["passed"]
        types = {e["type"] for e in result["errors"]}
        assert "nested_includes" in types
        assert "unrecognized_include_key" in types


class TestLintLogicEdgeCases:
    """Edge cases that should NOT false-positive."""

    def test_empty_bundle_section(self):
        """Empty bundle: section should not crash."""
        data = yaml.safe_load(
            textwrap.dedent("""
            bundle:
              name: test
        """)
        )
        result = lint_yaml_data(data)
        assert result["passed"]

    def test_bundle_as_string(self):
        """bundle: as a string (not dict) should not crash."""
        data = {"bundle": "foundation", "includes": [{"bundle": "foo"}]}
        result = lint_yaml_data(data)
        assert result["passed"]

    def test_empty_includes_list(self):
        """Empty includes list should pass."""
        data = yaml.safe_load(
            textwrap.dedent("""
            bundle:
              name: test
            includes: []
        """)
        )
        result = lint_yaml_data(data)
        assert result["passed"]

    def test_includes_with_none_entries(self):
        """None entries in includes should not crash."""
        data = {"bundle": {"name": "test"}, "includes": [None, {"bundle": "foo"}]}
        result = lint_yaml_data(data)
        assert result["passed"]  # None is not a dict, so skipped

    def test_no_bundle_section_at_all(self):
        """YAML without a bundle: section should pass (not a bundle file)."""
        data = yaml.safe_load(
            textwrap.dedent("""
            name: some-recipe
            steps:
              - id: step1
        """)
        )
        result = lint_yaml_data(data)
        assert result["passed"]


# =============================================================================
# INTEGRATION TESTS: validate-single-bundle.yaml recipe structure
# =============================================================================


class TestSingleBundleRecipeStructure:
    """Verify validate-single-bundle.yaml has the yaml-structure-lint step."""

    def test_version_is_2_1_0(self, single_bundle_recipe):
        """Version must be bumped to 2.1.0."""
        data, _ = single_bundle_recipe
        assert data["version"] == "2.1.0", (
            f"Expected version '2.1.0', got '{data['version']}'"
        )

    def test_yaml_structure_lint_step_exists(self, single_bundle_steps):
        """yaml-structure-lint step must be present."""
        assert "yaml-structure-lint" in single_bundle_steps

    def test_yaml_structure_lint_is_bash(self, single_bundle_steps):
        """yaml-structure-lint must be a bash step."""
        step = single_bundle_steps.get("yaml-structure-lint", {})
        assert step.get("type") == "bash"

    def test_yaml_structure_lint_output(self, single_bundle_steps):
        """yaml-structure-lint output must be 'structure_lint'."""
        step = single_bundle_steps.get("yaml-structure-lint", {})
        assert step.get("output") == "structure_lint"

    def test_yaml_structure_lint_parse_json(self, single_bundle_steps):
        """yaml-structure-lint must have parse_json: true."""
        step = single_bundle_steps.get("yaml-structure-lint", {})
        assert step.get("parse_json") is True

    def test_yaml_structure_lint_on_error_fail(self, single_bundle_steps):
        """yaml-structure-lint must fail early on errors."""
        step = single_bundle_steps.get("yaml-structure-lint", {})
        assert step.get("on_error") == "fail"

    def test_trace_dependencies_depends_on_lint(self, single_bundle_steps):
        """trace-dependencies must depend on yaml-structure-lint."""
        step = single_bundle_steps.get("trace-dependencies", {})
        depends = step.get("depends_on", [])
        assert "yaml-structure-lint" in depends, (
            "trace-dependencies must depend on yaml-structure-lint "
            "so structural bugs are caught before dependency tracing"
        )

    def test_yaml_structure_lint_is_first_step(self, single_bundle_recipe):
        """yaml-structure-lint must be the first step (runs before everything)."""
        data, _ = single_bundle_recipe
        steps = data.get("steps", [])
        assert len(steps) > 0
        assert steps[0]["id"] == "yaml-structure-lint"

    def test_lint_command_checks_nested_includes(self, single_bundle_steps):
        """Lint command must contain nested_includes detection logic."""
        step = single_bundle_steps.get("yaml-structure-lint", {})
        command = step.get("command", "")
        assert "nested_includes" in command
        assert "bundle" in command  # Checks bundle section

    def test_lint_command_checks_unrecognized_keys(self, single_bundle_steps):
        """Lint command must contain unrecognized key detection logic."""
        step = single_bundle_steps.get("yaml-structure-lint", {})
        command = step.get("command", "")
        assert "unrecognized_include_key" in command

    def test_generate_report_references_structure_lint(self, single_bundle_recipe):
        """generate-report prompt must reference structure_lint results."""
        _, content = single_bundle_recipe
        assert "structure_lint" in content
        assert "YAML Structure Lint" in content

    def test_changelog_has_v2_1_0(self, single_bundle_recipe):
        """Changelog must mention v2.1.0."""
        _, content = single_bundle_recipe
        assert "v2.1.0" in content


# =============================================================================
# INTEGRATION TESTS: validate-bundle-repo.yaml recipe structure
# =============================================================================


class TestBundleRepoRecipeStructure:
    """Verify validate-bundle-repo.yaml has the yaml-structure-lint step."""

    def test_version_is_3_4_0(self, bundle_repo_recipe):
        """Version must be bumped to 3.4.0."""
        data, _ = bundle_repo_recipe
        assert data["version"] == "3.4.0", (
            f"Expected version '3.4.0', got '{data['version']}'"
        )

    def test_yaml_structure_lint_step_exists(self, bundle_repo_steps):
        """yaml-structure-lint step must be present."""
        assert "yaml-structure-lint" in bundle_repo_steps

    def test_yaml_structure_lint_is_bash(self, bundle_repo_steps):
        """yaml-structure-lint must be a bash step."""
        step = bundle_repo_steps.get("yaml-structure-lint", {})
        assert step.get("type") == "bash"

    def test_yaml_structure_lint_output(self, bundle_repo_steps):
        """yaml-structure-lint output must be 'structure_lint'."""
        step = bundle_repo_steps.get("yaml-structure-lint", {})
        assert step.get("output") == "structure_lint"

    def test_yaml_structure_lint_depends_on_discovery(self, bundle_repo_steps):
        """yaml-structure-lint must depend on repo-discovery."""
        step = bundle_repo_steps.get("yaml-structure-lint", {})
        depends = step.get("depends_on", [])
        assert "repo-discovery" in depends

    def test_yaml_structure_lint_on_error_continue(self, bundle_repo_steps):
        """yaml-structure-lint must have on_error: continue (non-blocking)."""
        step = bundle_repo_steps.get("yaml-structure-lint", {})
        assert step.get("on_error") == "continue"

    def test_quality_classification_depends_on_lint(self, bundle_repo_steps):
        """quality-classification must depend on yaml-structure-lint."""
        step = bundle_repo_steps.get("quality-classification", {})
        depends = step.get("depends_on", [])
        assert "yaml-structure-lint" in depends, (
            "quality-classification must depend on yaml-structure-lint"
        )

    def test_quality_classification_parses_structure_lint(self, bundle_repo_recipe):
        """quality-classification command must parse structure_lint."""
        _, content = bundle_repo_recipe
        # The quality-classification step should reference structure_lint
        assert "structure_lint" in content

    def test_synthesize_report_includes_structure_lint(self, bundle_repo_recipe):
        """synthesize-report must reference YAML Structure Lint."""
        _, content = bundle_repo_recipe
        assert "YAML Structure Lint" in content

    def test_changelog_has_v3_4_0(self, bundle_repo_recipe):
        """Changelog must mention v3.4.0."""
        _, content = bundle_repo_recipe
        assert "v3.4.0" in content


# =============================================================================
# REGRESSION TESTS: Parallax Discovery bug
# =============================================================================


class TestParallaxDiscoveryRegression:
    """Regression test for the exact bug from parallax-discovery bundle."""

    def test_parallax_discovery_pattern_detected(self):
        """The exact YAML pattern from parallax-discovery should be caught.

        This was the actual broken YAML that caused 5 agents to silently
        not register. The includes were nested under bundle: instead of
        being at the top level.
        """
        # Simplified version of the actual broken bundle.md frontmatter
        broken_data = yaml.safe_load(
            textwrap.dedent("""
            bundle:
              name: parallax-discovery
              version: "1.0.0"
              description: "Parallax Discovery methodology"
              includes:
                - bundle: foundation
                - bundle: parallax-discovery:behaviors/parallax-discovery
                - bundle: parallax-discovery:behaviors/investigation-agents
                - bundle: parallax-discovery:behaviors/synthesis
                - bundle: parallax-discovery:behaviors/report-gen
        """)
        )
        result = lint_yaml_data(broken_data)
        assert not result["passed"], (
            "The parallax-discovery nested-includes pattern MUST be caught"
        )
        assert result["errors"][0]["type"] == "nested_includes"
        assert result["errors"][0]["count"] == 5

    def test_parallax_discovery_fixed_pattern_passes(self):
        """The fixed pattern (top-level includes) should pass."""
        fixed_data = yaml.safe_load(
            textwrap.dedent("""
            bundle:
              name: parallax-discovery
              version: "1.0.0"
              description: "Parallax Discovery methodology"

            includes:
              - bundle: foundation
              - bundle: parallax-discovery:behaviors/parallax-discovery
              - bundle: parallax-discovery:behaviors/investigation-agents
              - bundle: parallax-discovery:behaviors/synthesis
              - bundle: parallax-discovery:behaviors/report-gen
        """)
        )
        result = lint_yaml_data(fixed_data)
        assert result["passed"], "The fixed parallax-discovery pattern must pass"
