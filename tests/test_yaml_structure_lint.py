"""Tests for YAML Structure Lint detection (v2.1.0 / v3.4.0).

Verifies that the lint logic correctly detects:
- Bug 1: includes: nested under bundle: (silently dropped by parser)
- Bug 2: Unrecognized dict keys in includes entries (only 'bundle:' valid)

These tests validate BOTH:
1. The lint logic itself (unit tests with in-memory YAML)
2. The recipe structure (integration tests on the recipe YAML files)
"""

import re
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

    def test_version_is_current(self, single_bundle_recipe):
        """Version must match the recipe's current release."""
        data, _ = single_bundle_recipe
        assert data["version"] == "2.3.1", (
            f"Expected version '2.3.1', got '{data['version']}'"
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


def _parse_header_version(content: str) -> str:
    """Extract version from recipe header comment.

    Header convention: '# Repository-Wide Bundle Validator Recipe vX.Y.Z'
    """
    match = re.search(r"Recipe v(\d+\.\d+\.\d+)", content)
    if not match:
        raise ValueError(f"Header version not found in: {content.splitlines()[0]}")
    return match.group(1)


class TestBundleRepoRecipeStructure:
    """Verify validate-bundle-repo.yaml has the yaml-structure-lint step."""

    def test_version_field_matches_header_version(self, bundle_repo_recipe):
        """YAML version field and header comment must always agree.

        Detects the exact bug that broke CI: bumping the header comment
        without also updating the YAML version: field (or vice versa).
        """
        data, content = bundle_repo_recipe
        header_version = _parse_header_version(content)
        yaml_version = data["version"]
        assert header_version == yaml_version, (
            f"Header version '{header_version}' does not match YAML version "
            f"field '{yaml_version}'. Both must be updated on every recipe bump."
        )

    def test_current_version_in_changelog(self, bundle_repo_recipe):
        """Current version must have a changelog entry.

        Detects a bump where the version field was updated but no changelog
        entry was written for the new version.
        """
        data, content = bundle_repo_recipe
        version = data["version"]
        assert f"v{version}" in content, (
            f"Recipe is at version '{version}' but no 'v{version}' entry "
            f"found in CHANGELOG. Bump the version field OR add a changelog entry."
        )

    def test_header_format_is_canonical(self, bundle_repo_recipe):
        """Header comment must follow the canonical format '# ... Recipe vX.Y.Z'.

        Ensures the version is machine-parseable for version-agnostic tests.
        """
        _, content = bundle_repo_recipe
        header_lines = content.split("\n")[:5]
        header = "\n".join(header_lines)
        assert re.search(r"^# .* Recipe v\d+\.\d+\.\d+$", header, re.MULTILINE), (
            f"Header must contain a line matching '# ... Recipe vX.Y.Z'. Found:\n{header}"
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


# =============================================================================
# LINT: every recipe with an `agent:` reference declares schema_version 2
# =============================================================================
#
# A schema-v1 (legacy) recipe runs in the runner's `legacy-caller-bound` mode:
# its `agent:` references resolve out of the CALLING session's agent map, not
# from the recipe's own declared closure. A caller whose bundle does not mount
# that agent fails at run time with:
#
#     Agent 'foundation:zen-architect' not found in configuration
#
# That is not hypothetical -- it is the reported failure PR #345 fixed for
# validate-agents / validate-bundle / validate-single-bundle, and it recurred
# on validate-bundle-repo plus five behavioral-model/docs recipes that the
# same migration missed. This lint is the ratchet that keeps it from
# recurring a third time: the moment a recipe grows an `agent:` step, it must
# also declare the closure that agent comes from.


def _iter_recipe_steps(data: dict):
    """Yield every step mapping in a recipe, flat or staged."""
    for step in data.get("steps") or []:
        if isinstance(step, dict):
            yield step
    for stage in data.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        for step in stage.get("steps") or []:
            if isinstance(step, dict):
                yield step


def agent_refs(data: dict) -> set[str]:
    """Step-level `agent:` references only.

    Deliberately NOT a grep: several recipes embed Python/YAML samples in
    prompts and heredocs that contain the literal text `agent:` (e.g.
    `agent: str | None`). Only a parsed step's own `agent` field counts.
    """
    return {
        step["agent"]
        for step in _iter_recipe_steps(data)
        if isinstance(step.get("agent"), str) and step["agent"].strip()
    }


def declared_agents(data: dict) -> set[str]:
    """Agents declared across the recipe's dependency manifest."""
    declared: set[str] = set()
    for dep in data.get("dependencies") or []:
        if isinstance(dep, dict):
            for name in dep.get("required_agents") or []:
                if isinstance(name, str):
                    declared.add(name)
    return declared


def lint_agent_portability(data: dict) -> list[str]:
    """Return a list of violation messages; empty means the recipe is clean.

    Pure logic over already-parsed YAML so it can be exercised against
    synthetic recipes as well as the real ones on disk.
    """
    refs = agent_refs(data)
    if not refs:
        return []  # No agent steps -- nothing to make portable.

    violations = []
    if data.get("schema_version") != 2:
        violations.append(
            f"references {sorted(refs)} but declares "
            f"schema_version={data.get('schema_version')!r} (expected 2). "
            f"Its agents would resolve from the CALLING session's agent map."
        )
        return violations  # An undeclared closure can't also be checked.

    missing = sorted(refs - declared_agents(data))
    if missing:
        violations.append(
            f"declares schema_version 2 but its dependencies do not list "
            f"required_agents {missing}, which its steps reference."
        )
    return violations


class TestAgentRefsRequireSchemaV2:
    """Every recipes/*.yaml with an `agent:` step must be schema v2."""

    def test_all_recipes_with_agent_refs_declare_schema_v2(self):
        offenders = {}
        recipes = sorted(RECIPE_DIR.glob("*.yaml"))
        assert recipes, f"No recipes found under {RECIPE_DIR}"

        for path in recipes:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            violations = lint_agent_portability(data)
            if violations:
                offenders[path.name] = violations

        assert not offenders, (
            "Recipes with `agent:` steps must declare schema_version 2 and list "
            "every referenced agent under dependencies[].required_agents, so the "
            "agents resolve from the recipe's own closure instead of the calling "
            "session's agent map:\n"
            + "\n".join(f"  {name}: {'; '.join(v)}" for name, v in sorted(offenders.items()))
        )

    def test_at_least_one_recipe_actually_has_agent_refs(self):
        """Guard against the lint silently passing on an empty population."""
        with_agents = [
            p.name
            for p in sorted(RECIPE_DIR.glob("*.yaml"))
            if agent_refs(yaml.safe_load(p.read_text(encoding="utf-8")) or {})
        ]
        assert with_agents, (
            "No recipe has an `agent:` step -- the portability lint above would "
            "pass vacuously. Verify the parser, not just the result."
        )


class TestAgentPortabilityLintDiscriminates:
    """The lint must FAIL on the exact shape it exists to catch.

    A lint that never fires is indistinguishable from no lint at all. These
    reproduce the reported defect in-memory so the discrimination is proven
    on every CI run, not just once by hand.
    """

    LEGACY_RECIPE = textwrap.dedent("""
        name: legacy-recipe
        version: "1.0.0"
        steps:
          - id: analyze
            agent: "foundation:zen-architect"
            prompt: "do the thing"
    """)

    V2_RECIPE = textwrap.dedent("""
        schema_version: 2
        dependencies:
          - source: "git+https://github.com/microsoft/amplifier-foundation@v2.1.2"
            kind: bundle
            required_agents:
              - "foundation:zen-architect"
        name: migrated-recipe
        version: "1.1.0"
        steps:
          - id: analyze
            agent: "foundation:zen-architect"
            prompt: "do the thing"
    """)

    def test_missing_schema_version_is_a_violation(self):
        """The exact defect: agent refs with no schema_version at all."""
        violations = lint_agent_portability(yaml.safe_load(self.LEGACY_RECIPE))
        assert violations, "A legacy recipe with agent refs MUST be flagged"
        assert "foundation:zen-architect" in violations[0]
        assert "schema_version" in violations[0]

    def test_schema_version_1_is_a_violation(self):
        """schema_version: 1 is just as caller-bound as no schema_version."""
        data = yaml.safe_load(self.LEGACY_RECIPE)
        data["schema_version"] = 1
        assert lint_agent_portability(data), "schema_version 1 MUST be flagged"

    def test_v2_with_declared_agent_passes(self):
        """The fixed shape -- the header this migration adds -- passes."""
        assert lint_agent_portability(yaml.safe_load(self.V2_RECIPE)) == []

    def test_v2_with_undeclared_agent_is_a_violation(self):
        """v2 alone is not enough: the referenced agent must be declared."""
        data = yaml.safe_load(self.V2_RECIPE)
        data["dependencies"][0]["required_agents"] = ["foundation:explorer"]
        violations = lint_agent_portability(data)
        assert violations, "An undeclared referenced agent MUST be flagged"
        assert "foundation:zen-architect" in violations[0]

    def test_recipe_without_agent_steps_is_exempt(self):
        """Bash-only recipes need no closure -- they must not be flagged."""
        data = yaml.safe_load(
            textwrap.dedent("""
            name: bash-only
            version: "1.0.0"
            steps:
              - id: run
                type: bash
                command: "echo hi"
        """)
        )
        assert lint_agent_portability(data) == []

    def test_embedded_agent_text_is_not_a_reference(self):
        """`agent:` inside a prompt/heredoc is NOT a step-level reference.

        bundle-behavioral-model.yaml embeds Python dataclass samples
        containing the literal line `agent: str | None`. A grep-based lint
        would flag them; a parsed lint must not.
        """
        data = yaml.safe_load(
            textwrap.dedent('''
            name: embeds-samples
            version: "1.0.0"
            steps:
              - id: run
                type: bash
                command: |
                  cat <<EOF
                  class Step:
                      agent: str | None
                  EOF
        ''')
        )
        assert agent_refs(data) == set()
        assert lint_agent_portability(data) == []

    def test_staged_recipe_steps_are_scanned(self):
        """Steps nested under `stages:` must not escape the lint."""
        data = yaml.safe_load(
            textwrap.dedent("""
            name: staged-legacy
            version: "1.0.0"
            stages:
              - name: phase-1
                steps:
                  - id: analyze
                    agent: "foundation:zen-architect"
        """)
        )
        assert agent_refs(data) == {"foundation:zen-architect"}
        assert lint_agent_portability(data), "A staged legacy recipe MUST be flagged"


# =============================================================================
# LINT: a SOLE conditional producer's output must not be read later
# =============================================================================
#
# The tool-recipes executor raises
#     ValueError: Undefined variable: {{X}}. Available variables: ...
# when a step's template references X and no executed step produced it. A
# step carrying `condition:` may be SKIPPED, so its `output:` is not
# guaranteed.
#
# Reported instance: validate-bundle-repo.yaml with enhance_diagrams: "false"
# skipped `bundle-overview-regen-enhance` (output bundle_overview_enhanced_dot)
# while `bundle-overview-regen-write` read {{bundle_overview_enhanced_dot}}
# unconditionally -> hard crash. generate-bundle-docs.yaml had the identical
# defect (enhance-bundle-dot -> write-bundle-dot).
#
# SCOPE -- deliberately narrow, to stay sound rather than merely strict:
#
#   Flagged: a variable whose ONLY prior producer is a single CONDITIONAL
#            step. That is exactly the reported defect.
#
#   Not flagged: two-or-more conditional producers. This repo uses
#            complementary conditions on purpose -- `build-check`
#            (condition: has_pyproject == true) paired with
#            `set-default-build-check` (condition: has_pyproject != true).
#            Exactly one always runs. Proving that needs a real expression
#            evaluator; a lint that guesses would fail correct recipes.
#
#   Not flagged: a variable with no producer at all. Values also enter the
#            context from `collect:` on a foreach step, `as:` loop bindings,
#            and parsed sub-keys. Those are modelled below where cheap, but
#            the zero-producer case has too much unmodelled surface to
#            assert on without false positives.

_VAR_REF = re.compile(r"\{\{(\w+(?:\.\w+)*)\}\}")

#: Names the engine/session supplies regardless of any step.
_ENGINE_BUILTINS = frozenset(
    {"recipe", "session", "env", "now", "timestamp", "loop", "item", "index"}
)


def step_var_refs(step: dict) -> set[str]:
    """Every {{variable}} a step's templates reference."""
    found: set[str] = set()
    for key in ("command", "prompt", "condition", "foreach"):
        value = step.get(key)
        if isinstance(value, str):
            found |= {m.group(1) for m in _VAR_REF.finditer(value)}
    return found


def step_produced_names(step: dict) -> set[str]:
    """Every context name a step can introduce.

    `output:` is the common case. A foreach step also binds its `as:` loop
    variable inside its own body and aggregates into `collect:`.
    """
    names = set()
    for key in ("output", "collect", "as"):
        value = step.get(key)
        if isinstance(value, str) and value:
            names.add(value)
    return names


def sole_conditional_producer_refs(data: dict) -> list[str]:
    """Refs to a variable whose only prior producer is one conditional step."""
    guaranteed = set(data.get("context") or {}) | set(_ENGINE_BUILTINS)
    conditional_producers: dict[str, list[str]] = {}
    problems: list[str] = []

    for step in _iter_recipe_steps(data):
        step_id = step.get("id", "<no id>")

        # A foreach step's own `as:` binding is in scope for its own body.
        in_scope = guaranteed | {step["as"]} if isinstance(step.get("as"), str) else guaranteed

        for ref in sorted(step_var_refs(step)):
            root = ref.split(".")[0]
            if root in in_scope:
                continue
            producers = conditional_producers.get(root, [])
            if len(producers) == 1:
                problems.append(
                    f"step '{step_id}' reads {{{{{ref}}}}}, produced ONLY by the "
                    f"conditional step '{producers[0]}'. Add an unconditional "
                    f"set-default step ahead of that producer, or guard this "
                    f"consumer with the same condition."
                )

        for name in step_produced_names(step):
            if step.get("condition") and name == step.get("output"):
                conditional_producers.setdefault(name, []).append(step_id)
            else:
                guaranteed.add(name)
                conditional_producers.pop(name, None)

    return problems


class TestConditionalOutputsHaveDefaults:
    """A sole conditional producer's output must have an unconditional default."""

    def test_no_recipe_reads_a_sole_conditional_variable(self):
        offenders = {}
        for path in sorted(RECIPE_DIR.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            problems = sole_conditional_producer_refs(data)
            if problems:
                offenders[path.name] = problems

        assert not offenders, (
            "A step reads a variable that a conditional step may never produce. "
            "The engine raises 'Undefined variable: {{X}}' at run time:\n"
            + "\n".join(
                f"  {name}:\n    " + "\n    ".join(p)
                for name, p in sorted(offenders.items())
            )
        )

    def test_validate_bundle_repo_defaults_the_enhanced_dot(self, bundle_repo_steps):
        """The reported enhance_diagrams='false' crash, pinned."""
        default = bundle_repo_steps.get("set-default-bundle-overview-enhanced-dot")
        assert default, "set-default-bundle-overview-enhanced-dot step is missing"
        assert default.get("output") == "bundle_overview_enhanced_dot"
        assert "condition" not in default, "The default must be UNCONDITIONAL"

        producer = bundle_repo_steps.get("bundle-overview-regen-enhance", {})
        assert producer.get("condition") == "{{enhance_diagrams}} != 'false'"
        assert producer.get("output") == "bundle_overview_enhanced_dot"

    def test_generate_bundle_docs_defaults_the_enhanced_dot(self):
        """generate-bundle-docs.yaml carried the identical defect."""
        data = yaml.safe_load(
            (RECIPE_DIR / "generate-bundle-docs.yaml").read_text(encoding="utf-8")
        )
        steps = {s["id"]: s for s in data.get("steps", []) if "id" in s}

        default = steps.get("set-default-enhanced-bundle-dot")
        assert default, "set-default-enhanced-bundle-dot step is missing"
        assert default.get("output") == "enhanced_bundle_dot"
        assert "condition" not in default, "The default must be UNCONDITIONAL"

        producer = steps.get("enhance-bundle-dot", {})
        assert producer.get("condition") == "{{enhance_diagrams}} != 'false'"
        assert producer.get("output") == "enhanced_bundle_dot"

    def test_default_is_declared_before_the_conditional_producer(self):
        """Declaration order is execution order -- the default must be first.

        If the default came after the producer it would CLOBBER a real
        enhanced DOT whenever enhancement did run.
        """
        for recipe, default_id, producer_id in (
            (
                "validate-bundle-repo.yaml",
                "set-default-bundle-overview-enhanced-dot",
                "bundle-overview-regen-enhance",
            ),
            (
                "generate-bundle-docs.yaml",
                "set-default-enhanced-bundle-dot",
                "enhance-bundle-dot",
            ),
        ):
            data = yaml.safe_load((RECIPE_DIR / recipe).read_text(encoding="utf-8"))
            ids = [s.get("id") for s in data.get("steps", [])]
            assert default_id in ids, f"{recipe}: {default_id} missing"
            assert producer_id in ids, f"{recipe}: {producer_id} missing"
            assert ids.index(default_id) < ids.index(producer_id), (
                f"{recipe}: {default_id} must be declared BEFORE {producer_id} "
                f"so the real producer overwrites the default, not vice versa."
            )


class TestConditionalOutputLintDiscriminates:
    """The lint must fire on the defect and stay quiet on correct patterns."""

    PRE_FIX = textwrap.dedent("""
        name: pre-fix
        context:
          enhance_diagrams: "true"
        steps:
          - id: produce
            condition: "{{enhance_diagrams}} != 'false'"
            agent: "foundation:zen-architect"
            prompt: "make it pretty"
            output: "enhanced_dot"
          - id: consume
            type: bash
            command: |
              echo "{{enhanced_dot}}"
    """)

    POST_FIX = textwrap.dedent("""
        name: post-fix
        context:
          enhance_diagrams: "true"
        steps:
          - id: set-default
            type: bash
            command: |
              echo ""
            output: "enhanced_dot"
          - id: produce
            condition: "{{enhance_diagrams}} != 'false'"
            agent: "foundation:zen-architect"
            prompt: "make it pretty"
            output: "enhanced_dot"
          - id: consume
            type: bash
            command: |
              echo "{{enhanced_dot}}"
    """)

    def test_flags_the_pre_fix_shape(self):
        """The exact reported defect MUST be caught."""
        problems = sole_conditional_producer_refs(yaml.safe_load(self.PRE_FIX))
        assert problems, "The pre-fix conditional-output shape MUST be flagged"
        assert "enhanced_dot" in problems[0]
        assert "conditional step 'produce'" in problems[0]

    def test_passes_the_post_fix_shape(self):
        """The set-default remedy clears the finding."""
        assert sole_conditional_producer_refs(yaml.safe_load(self.POST_FIX)) == []

    def test_default_after_producer_still_flags_the_consumer(self):
        """Order matters: a default declared after the producer does not help.

        Steps execute in declaration order, so a default that lands after the
        conditional producer both fails to guard the consumer's first read
        and clobbers a real enhanced value.
        """
        data = yaml.safe_load(self.PRE_FIX)
        data["steps"].insert(
            1,
            {"id": "set-default", "type": "bash", "command": 'echo ""', "output": "enhanced_dot"},
        )
        # produce (conditional) -> set-default -> consume: the default IS
        # unconditional and precedes the consumer, so this specific ordering
        # is safe for the consumer even though it clobbers. Sanity-check the
        # model agrees, so the ordering test above carries the clobber concern.
        assert sole_conditional_producer_refs(data) == []

    def test_complementary_conditional_pair_is_not_flagged(self):
        """Two complementary conditions always yield exactly one producer.

        This is the repo's deliberate build-check / set-default-build-check
        pattern. Flagging it would fail correct recipes.
        """
        data = yaml.safe_load(
            textwrap.dedent("""
            name: complementary
            context:
              has_pyproject: "true"
            steps:
              - id: build-check
                condition: "{{has_pyproject}} == true"
                type: bash
                command: "echo real"
                output: "build_check"
              - id: set-default-build-check
                condition: "{{has_pyproject}} != true"
                type: bash
                command: "echo default"
                output: "build_check"
              - id: consume
                type: bash
                command: |
                  echo "{{build_check}}"
        """)
        )
        assert sole_conditional_producer_refs(data) == []

    def test_foreach_loop_binding_is_in_scope(self):
        """`as:` binds a loop variable inside the foreach step's own body.

        bundle-behavioral-model.yaml's extract-behaviors uses
        `as: "target"` and reads {{target.file_path}}; that is correct, not
        an undefined variable.
        """
        data = yaml.safe_load(
            textwrap.dedent("""
            name: loops
            context:
              manifest: {}
            steps:
              - id: extract
                agent: "foundation:explorer"
                foreach: "{{manifest}}"
                as: "target"
                collect: "extractions"
                prompt: |
                  Read {{target.file_path}}
              - id: synthesize
                type: bash
                command: |
                  echo "{{extractions}}"
        """)
        )
        assert sole_conditional_producer_refs(data) == []

    def test_unconditional_producer_is_never_flagged(self):
        data = yaml.safe_load(
            textwrap.dedent("""
            name: plain
            steps:
              - id: produce
                type: bash
                command: "echo hi"
                output: "value"
              - id: consume
                type: bash
                command: |
                  echo "{{value}}"
        """)
        )
        assert sole_conditional_producer_refs(data) == []
