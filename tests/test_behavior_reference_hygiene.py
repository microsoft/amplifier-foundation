"""Tests for Behavior Reference Hygiene checks (v3.5.0).

Verifies that the hygiene logic correctly detects:
- Check A: Cross-repo root-bundle references in behavior includes
  (git+https:// URL without #subdirectory= fragment)
- Check B: Name collisions between a behavior's bundle.name and the root bundle.name

These tests validate BOTH:
1. The check logic itself (unit tests with in-memory data)
2. The recipe structure (integration tests on validate-bundle-repo.yaml)
"""

from pathlib import Path

import pytest
import yaml


# =============================================================================
# FIXTURES
# =============================================================================

RECIPE_DIR = Path(__file__).parent.parent / "recipes"
BUNDLE_REPO_RECIPE = RECIPE_DIR / "validate-bundle-repo.yaml"


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
# UNIT TEST HELPERS
# Mirrors the logic inside the recipe's Python heredoc for local testing
# =============================================================================


def is_bare_git_ref(bundle_ref: str) -> bool:
    """True if ref is a git+https URL WITHOUT a #subdirectory= fragment."""
    if not isinstance(bundle_ref, str):
        return False
    if not bundle_ref.startswith(("git+https://", "git+http://")):
        return False
    if "#subdirectory=" in bundle_ref:
        return False
    # Must have a ref pin (@branch / @tag / @sha)
    if "@" not in bundle_ref.split("://", 1)[-1]:
        return False
    return True


def get_bundle_name(data: dict) -> str | None:
    """Extract bundle.name from parsed YAML dict."""
    bundle_section = data.get("bundle", {})
    if isinstance(bundle_section, dict):
        name = bundle_section.get("name")
        if name:
            return str(name).strip()
    return None


def check_behavior_reference_hygiene(
    root_bundle_name: str | None,
    behavior_includes: list,
    behavior_bundle_name: str | None,
    behavior_name: str = "test-behavior",
) -> dict:
    """Run both checks against a single behavior's data.

    Returns {passed: bool, warnings: [...]}.
    """
    warnings = []

    # Check A: bare git refs
    for inc in behavior_includes:
        if isinstance(inc, dict):
            bundle_ref = inc.get("bundle", "")
        else:
            bundle_ref = str(inc) if inc is not None else ""

        if is_bare_git_ref(bundle_ref):
            warnings.append(
                {
                    "type": "cross_repo_root_bundle_ref",
                    "behavior": behavior_name,
                    "reference": bundle_ref,
                }
            )

    # Check B: name collision
    if (
        behavior_bundle_name
        and root_bundle_name
        and behavior_bundle_name == root_bundle_name
    ):
        warnings.append(
            {
                "type": "name_collision_with_root",
                "behavior": behavior_name,
                "name": behavior_bundle_name,
            }
        )

    return {"passed": len(warnings) == 0, "warnings": warnings}


# =============================================================================
# UNIT TESTS: Check A — Cross-repo root-bundle references
# =============================================================================


class TestCheckACrossRepoRootBundleRef:
    """Check A: bare git+https:// refs in behavior includes."""

    def test_bare_git_ref_flagged(self):
        """A bare git+https URL with no #subdirectory= should be flagged."""
        includes = [{"bundle": "git+https://github.com/org/amplifier-bundle-foo@main"}]
        result = check_behavior_reference_hygiene(None, includes, None)
        assert not result["passed"]
        assert len(result["warnings"]) == 1
        assert result["warnings"][0]["type"] == "cross_repo_root_bundle_ref"

    def test_bare_git_ref_with_sha_flagged(self):
        """A bare git+https URL pinned to a SHA should also be flagged."""
        includes = [
            {"bundle": "git+https://github.com/org/amplifier-bundle-foo@abc1234"}
        ]
        result = check_behavior_reference_hygiene(None, includes, None)
        assert not result["passed"]
        assert result["warnings"][0]["type"] == "cross_repo_root_bundle_ref"

    def test_ref_with_subdirectory_passes(self):
        """A git+https URL with #subdirectory= should NOT be flagged."""
        includes = [
            {
                "bundle": (
                    "git+https://github.com/org/amplifier-bundle-foo@main"
                    "#subdirectory=behaviors/foo.yaml"
                )
            }
        ]
        result = check_behavior_reference_hygiene(None, includes, None)
        assert result["passed"]
        assert len(result["warnings"]) == 0

    def test_local_bundle_ref_passes(self):
        """A local namespace ref (no git URL) should NOT be flagged."""
        includes = [{"bundle": "foundation"}, {"bundle": "foundation:behaviors/agents"}]
        result = check_behavior_reference_hygiene(None, includes, None)
        assert result["passed"]

    def test_file_uri_ref_passes(self):
        """A file:// URI is not a git+https URL — should NOT be flagged."""
        includes = [{"bundle": "file:///path/to/bundle.md"}]
        result = check_behavior_reference_hygiene(None, includes, None)
        assert result["passed"]

    def test_http_without_git_plus_passes(self):
        """A plain https:// URL (not git+https) should NOT be flagged."""
        includes = [{"bundle": "https://github.com/org/repo@main"}]
        result = check_behavior_reference_hygiene(None, includes, None)
        assert result["passed"]

    def test_bare_git_ref_without_at_sign_passes(self):
        """A git+https URL without an @ ref-pin is not a valid bare ref."""
        includes = [{"bundle": "git+https://github.com/org/repo"}]
        result = check_behavior_reference_hygiene(None, includes, None)
        # No @ pin — is_bare_git_ref returns False (we require a ref pin)
        assert result["passed"]

    def test_multiple_includes_one_bad(self):
        """Mix of good and bad refs — only the bad one flagged."""
        includes = [
            {"bundle": "foundation"},
            {"bundle": "git+https://github.com/org/amplifier-bundle-bar@main"},
            {
                "bundle": (
                    "git+https://github.com/org/amplifier-bundle-baz@main"
                    "#subdirectory=behaviors/baz.yaml"
                )
            },
        ]
        result = check_behavior_reference_hygiene(None, includes, None)
        assert not result["passed"]
        assert len(result["warnings"]) == 1
        assert result["warnings"][0]["type"] == "cross_repo_root_bundle_ref"
        assert "amplifier-bundle-bar@main" in result["warnings"][0]["reference"]

    def test_multiple_bare_refs_all_flagged(self):
        """Multiple bare git refs should all be flagged."""
        includes = [
            {"bundle": "git+https://github.com/org/bundle-a@main"},
            {"bundle": "git+https://github.com/org/bundle-b@main"},
        ]
        result = check_behavior_reference_hygiene(None, includes, None)
        assert not result["passed"]
        assert len(result["warnings"]) == 2

    def test_empty_includes_passes(self):
        """Empty includes list should pass."""
        result = check_behavior_reference_hygiene(None, [], None)
        assert result["passed"]

    def test_none_entry_in_includes_skipped(self):
        """None entries in includes should not crash."""
        result = check_behavior_reference_hygiene(
            None, [None, {"bundle": "foundation"}], None
        )
        assert result["passed"]

    def test_reality_check_bug_pattern_detected(self):
        """Regression: exact pattern from amplifier-bundle-reality-check bug.

        reality-check.yaml previously had:
          - bundle: git+https://github.com/microsoft/amplifier-bundle-terminal-tester@main
        instead of:
          - bundle: git+https://github.com/microsoft/amplifier-bundle-terminal-tester@main
                    #subdirectory=behaviors/terminal-tester.yaml
        """
        buggy_includes = [
            {
                "bundle": (
                    "git+https://github.com/microsoft/"
                    "amplifier-bundle-terminal-tester@main"
                )
            }
        ]
        result = check_behavior_reference_hygiene(None, buggy_includes, None)
        assert not result["passed"], (
            "The reality-check root-bundle-ref pattern MUST be flagged as WARNING"
        )
        assert result["warnings"][0]["type"] == "cross_repo_root_bundle_ref"

    def test_reality_check_fixed_pattern_passes(self):
        """Regression: fixed reality-check.yaml pattern should pass."""
        fixed_includes = [
            {
                "bundle": (
                    "git+https://github.com/microsoft/"
                    "amplifier-bundle-terminal-tester@main"
                    "#subdirectory=behaviors/terminal-tester.yaml"
                )
            }
        ]
        result = check_behavior_reference_hygiene(None, fixed_includes, None)
        assert result["passed"], "The fixed reality-check pattern must pass"


# =============================================================================
# UNIT TESTS: Check B — Name collisions
# =============================================================================


class TestCheckBNameCollision:
    """Check B: behavior bundle.name == root bundle.name."""

    def test_name_collision_flagged(self):
        """Same name on root and behavior should produce a WARNING."""
        result = check_behavior_reference_hygiene(
            root_bundle_name="terminal-tester",
            behavior_includes=[],
            behavior_bundle_name="terminal-tester",
        )
        assert not result["passed"]
        assert len(result["warnings"]) == 1
        assert result["warnings"][0]["type"] == "name_collision_with_root"

    def test_different_names_pass(self):
        """Different root and behavior names should pass."""
        result = check_behavior_reference_hygiene(
            root_bundle_name="terminal-tester",
            behavior_includes=[],
            behavior_bundle_name="terminal-tester-behavior",
        )
        assert result["passed"]

    def test_no_root_name_passes(self):
        """If root bundle has no name, Check B is skipped (no collision possible)."""
        result = check_behavior_reference_hygiene(
            root_bundle_name=None,
            behavior_includes=[],
            behavior_bundle_name="terminal-tester",
        )
        assert result["passed"]

    def test_no_behavior_name_passes(self):
        """If behavior has no bundle.name, Check B is skipped."""
        result = check_behavior_reference_hygiene(
            root_bundle_name="terminal-tester",
            behavior_includes=[],
            behavior_bundle_name=None,
        )
        assert result["passed"]

    def test_both_none_passes(self):
        """Both None → no collision possible."""
        result = check_behavior_reference_hygiene(
            root_bundle_name=None,
            behavior_includes=[],
            behavior_bundle_name=None,
        )
        assert result["passed"]

    def test_terminal_tester_bug_pattern_detected(self):
        """Regression: exact pattern from amplifier-bundle-terminal-tester.

        Both bundle.md (name: terminal-tester) and
        behaviors/terminal-tester.yaml (name: terminal-tester) had the same name,
        causing self-referential BundleState entries.
        """
        result = check_behavior_reference_hygiene(
            root_bundle_name="terminal-tester",
            behavior_includes=[],
            behavior_bundle_name="terminal-tester",
            behavior_name="terminal-tester",
        )
        assert not result["passed"], (
            "The terminal-tester name-collision pattern MUST be flagged as WARNING"
        )
        assert result["warnings"][0]["type"] == "name_collision_with_root"
        assert result["warnings"][0]["name"] == "terminal-tester"


# =============================================================================
# UNIT TESTS: Both checks together
# =============================================================================


class TestBothChecks:
    """Scenarios combining Check A and Check B."""

    def test_both_checks_pass(self):
        """Clean repo: no bare refs, no name collision → 0 warnings."""
        includes = [
            {
                "bundle": (
                    "git+https://github.com/org/bundle-a@main"
                    "#subdirectory=behaviors/a.yaml"
                )
            }
        ]
        result = check_behavior_reference_hygiene(
            root_bundle_name="my-bundle",
            behavior_includes=includes,
            behavior_bundle_name="my-bundle-behavior",
        )
        assert result["passed"]
        assert len(result["warnings"]) == 0

    def test_only_check_a_fails(self):
        """Bare ref but no name collision → 1 warning (type A)."""
        includes = [{"bundle": "git+https://github.com/org/foreign-bundle@main"}]
        result = check_behavior_reference_hygiene(
            root_bundle_name="my-bundle",
            behavior_includes=includes,
            behavior_bundle_name="my-bundle-behavior",
        )
        assert not result["passed"]
        assert len(result["warnings"]) == 1
        assert result["warnings"][0]["type"] == "cross_repo_root_bundle_ref"

    def test_only_check_b_fails(self):
        """Name collision but no bare refs → 1 warning (type B)."""
        includes = [
            {
                "bundle": (
                    "git+https://github.com/org/foreign-bundle@main"
                    "#subdirectory=behaviors/foreign.yaml"
                )
            }
        ]
        result = check_behavior_reference_hygiene(
            root_bundle_name="my-bundle",
            behavior_includes=includes,
            behavior_bundle_name="my-bundle",
        )
        assert not result["passed"]
        assert len(result["warnings"]) == 1
        assert result["warnings"][0]["type"] == "name_collision_with_root"

    def test_both_checks_fail(self):
        """Both bare ref AND name collision → 2 warnings."""
        includes = [{"bundle": "git+https://github.com/org/foreign-bundle@main"}]
        result = check_behavior_reference_hygiene(
            root_bundle_name="my-bundle",
            behavior_includes=includes,
            behavior_bundle_name="my-bundle",
        )
        assert not result["passed"]
        assert len(result["warnings"]) == 2
        types = {w["type"] for w in result["warnings"]}
        assert "cross_repo_root_bundle_ref" in types
        assert "name_collision_with_root" in types


# =============================================================================
# INTEGRATION TESTS: validate-bundle-repo.yaml recipe structure
# =============================================================================


class TestBundleRepoRecipeStructure:
    """Verify validate-bundle-repo.yaml has the behavior-reference-hygiene step."""

    def test_version_is_3_5_0(self, bundle_repo_recipe):
        """Version must be bumped to 3.5.0."""
        data, _ = bundle_repo_recipe
        assert data["version"] == "3.5.0", (
            f"Expected version '3.5.0', got '{data['version']}'"
        )

    def test_behavior_reference_hygiene_step_exists(self, bundle_repo_steps):
        """behavior-reference-hygiene step must be present."""
        assert "behavior-reference-hygiene" in bundle_repo_steps, (
            "Step 'behavior-reference-hygiene' not found in recipe steps"
        )

    def test_behavior_reference_hygiene_is_bash(self, bundle_repo_steps):
        """behavior-reference-hygiene must be a bash step."""
        step = bundle_repo_steps.get("behavior-reference-hygiene", {})
        assert step.get("type") == "bash"

    def test_behavior_reference_hygiene_output(self, bundle_repo_steps):
        """behavior-reference-hygiene output must be 'behavior_reference_hygiene_results'."""
        step = bundle_repo_steps.get("behavior-reference-hygiene", {})
        assert step.get("output") == "behavior_reference_hygiene_results"

    def test_behavior_reference_hygiene_parse_json(self, bundle_repo_steps):
        """behavior-reference-hygiene must have parse_json: true."""
        step = bundle_repo_steps.get("behavior-reference-hygiene", {})
        assert step.get("parse_json") is True

    def test_behavior_reference_hygiene_on_error_continue(self, bundle_repo_steps):
        """behavior-reference-hygiene must have on_error: continue (non-blocking)."""
        step = bundle_repo_steps.get("behavior-reference-hygiene", {})
        assert step.get("on_error") == "continue"

    def test_behavior_reference_hygiene_depends_on_repo_discovery(
        self, bundle_repo_steps
    ):
        """behavior-reference-hygiene must depend on repo-discovery."""
        step = bundle_repo_steps.get("behavior-reference-hygiene", {})
        depends = step.get("depends_on", [])
        assert "repo-discovery" in depends

    def test_quality_classification_depends_on_behavior_reference_hygiene(
        self, bundle_repo_steps
    ):
        """quality-classification must depend on behavior-reference-hygiene."""
        step = bundle_repo_steps.get("quality-classification", {})
        depends = step.get("depends_on", [])
        assert "behavior-reference-hygiene" in depends, (
            "quality-classification depends_on must include 'behavior-reference-hygiene'"
        )

    def test_quality_classification_parses_behavior_ref_hygiene(
        self, bundle_repo_recipe
    ):
        """quality-classification command must reference behavior_reference_hygiene_results."""
        _, content = bundle_repo_recipe
        assert "behavior_reference_hygiene_results" in content
        assert "behavior_ref_hygiene" in content

    def test_quality_classification_accumulates_warnings(self, bundle_repo_recipe):
        """quality-classification must add behavior_reference_hygiene_issues to warning_count."""
        _, content = bundle_repo_recipe
        assert "behavior_reference_hygiene_issues" in content

    def test_hygiene_summary_includes_ref_hygiene(self, bundle_repo_recipe):
        """hygiene_summary must include behavior_ref_hygiene_warnings count."""
        _, content = bundle_repo_recipe
        assert "behavior_ref_hygiene_warnings" in content

    def test_command_checks_bare_git_refs(self, bundle_repo_steps):
        """behavior-reference-hygiene command must contain bare git ref detection."""
        step = bundle_repo_steps.get("behavior-reference-hygiene", {})
        command = step.get("command", "")
        assert "cross_repo_root_bundle_ref" in command
        assert "subdirectory" in command

    def test_command_checks_name_collision(self, bundle_repo_steps):
        """behavior-reference-hygiene command must contain name collision detection."""
        step = bundle_repo_steps.get("behavior-reference-hygiene", {})
        command = step.get("command", "")
        assert "name_collision_with_root" in command

    def test_synthesize_report_references_behavior_ref_hygiene(
        self, bundle_repo_recipe
    ):
        """synthesize-report must reference behavior_reference_hygiene_results."""
        _, content = bundle_repo_recipe
        assert "behavior_reference_hygiene_results" in content

    def test_synthesize_report_has_behavior_ref_hygiene_section(
        self, bundle_repo_recipe
    ):
        """synthesize-report prompt must include Behavior Reference Hygiene section."""
        _, content = bundle_repo_recipe
        assert "Behavior Reference Hygiene" in content

    def test_changelog_has_v3_5_0(self, bundle_repo_recipe):
        """Changelog must mention v3.5.0."""
        _, content = bundle_repo_recipe
        assert "v3.5.0" in content

    def test_header_references_v3_5_0(self, bundle_repo_recipe):
        """File header comment must reference v3.5.0."""
        _, content = bundle_repo_recipe
        header_lines = content.split("\n")[:5]
        header = "\n".join(header_lines)
        assert "3.5.0" in header, f"Header must reference v3.5.0. Found:\n{header}"
