"""Integration tests for validate-bundle-repo.yaml v3.3.0 changes.

Verifies:
- Valid YAML structure
- Presence and correctness of new context variables
- Presence and correctness of new steps (validate-all-flags, validate-recipes,
  set-default-recipe-validation)
- quality-classification depends_on includes new steps
- synthesize-report prompt includes Recipe Validation block
- version metadata is up-to-date
"""

from pathlib import Path

import pytest
import yaml

RECIPE_PATH = Path(__file__).parent.parent / "recipes" / "validate-bundle-repo.yaml"


@pytest.fixture(scope="module")
def recipe_data():
    """Load and parse the recipe YAML once for all tests."""
    if not RECIPE_PATH.exists():
        pytest.skip("Recipe file not found")
    content = RECIPE_PATH.read_text(encoding="utf-8")
    return yaml.safe_load(content), content


@pytest.fixture(scope="module")
def steps_by_id(recipe_data):
    """Build a dict of steps keyed by id for easy lookup."""
    data, _ = recipe_data
    return {step["id"]: step for step in data.get("steps", []) if "id" in step}


# ── YAML validity ─────────────────────────────────────────────────────────────


class TestYAMLValidity:
    def test_recipe_is_valid_yaml(self, recipe_data):
        """Recipe must parse as a valid YAML dict."""
        data, _ = recipe_data
        assert isinstance(data, dict), "Top-level must be a dict"

    def test_recipe_has_name(self, recipe_data):
        """Recipe must have a name field."""
        data, _ = recipe_data
        assert "name" in data

    def test_recipe_has_version(self, recipe_data):
        """Recipe must have a version field."""
        data, _ = recipe_data
        assert "version" in data

    def test_recipe_has_steps(self, recipe_data):
        """Recipe must have a non-empty steps list."""
        data, _ = recipe_data
        assert isinstance(data.get("steps"), list)
        assert len(data["steps"]) > 0


# ── Version metadata ──────────────────────────────────────────────────────────


class TestVersionMetadata:
    def test_version_is_3_3_0(self, recipe_data):
        """version field must be '3.3.0' to reflect v3.3.0 changes."""
        data, _ = recipe_data
        assert data["version"] == "3.3.0", (
            f"Expected version '3.3.0', got '{data['version']}'. "
            "The version field must be bumped when significant changes are made."
        )

    def test_header_comment_references_v3_3_0(self, recipe_data):
        """File header comment must reference v3.3.0."""
        _, content = recipe_data
        # The first few lines should mention v3.3.0
        header_lines = content.split("\n")[:5]
        header = "\n".join(header_lines)
        assert "3.3.0" in header, (
            "Header comment must be updated to reference v3.3.0. "
            f"Found header:\n{header}"
        )

    def test_changelog_has_v3_3_0_entry(self, recipe_data):
        """Changelog section must contain a v3.3.0 entry."""
        _, content = recipe_data
        assert "v3.3.0" in content, (
            "Changelog must contain a v3.3.0 entry describing the recipe validation "
            "integration changes."
        )


# ── New context variables ─────────────────────────────────────────────────────


class TestNewContextVariables:
    def test_context_has_validate_recipes(self, recipe_data):
        """Context must include validate_recipes variable."""
        data, _ = recipe_data
        ctx = data.get("context", {})
        assert "validate_recipes" in ctx, (
            "Context must have validate_recipes variable (opt-in recipe validation flag)"
        )

    def test_validate_recipes_default_is_false(self, recipe_data):
        """validate_recipes default must be 'false'."""
        data, _ = recipe_data
        ctx = data.get("context", {})
        assert ctx.get("validate_recipes") == "false"

    def test_context_has_validate_all(self, recipe_data):
        """Context must include validate_all convenience flag."""
        data, _ = recipe_data
        ctx = data.get("context", {})
        assert "validate_all" in ctx, "Context must have validate_all convenience flag"

    def test_validate_all_default_is_false(self, recipe_data):
        """validate_all default must be 'false'."""
        data, _ = recipe_data
        ctx = data.get("context", {})
        assert ctx.get("validate_all") == "false"

    def test_context_has_recipes_dir(self, recipe_data):
        """Context must include recipes_dir variable."""
        data, _ = recipe_data
        ctx = data.get("context", {})
        assert "recipes_dir" in ctx, (
            "Context must have recipes_dir for sub-recipe invocation"
        )

    def test_recipes_dir_default_is_recipes(self, recipe_data):
        """recipes_dir default must be 'recipes'."""
        data, _ = recipe_data
        ctx = data.get("context", {})
        assert ctx.get("recipes_dir") == "recipes"

    def test_context_has_known_agents(self, recipe_data):
        """Context must include known_agents variable."""
        data, _ = recipe_data
        ctx = data.get("context", {})
        assert "known_agents" in ctx, (
            "Context must have known_agents for recipe validation"
        )

    def test_known_agents_default_is_empty_string(self, recipe_data):
        """known_agents default must be empty string."""
        data, _ = recipe_data
        ctx = data.get("context", {})
        # Default is '' (empty string) - YAML may load as None or ''
        val = ctx.get("known_agents")
        assert val == "" or val is None, (
            f"known_agents default should be '' or None, got {val!r}"
        )


# ── validate-all-flags step ───────────────────────────────────────────────────


class TestValidateAllFlagsStep:
    def test_validate_all_flags_step_exists(self, steps_by_id):
        """validate-all-flags step must be present."""
        assert "validate-all-flags" in steps_by_id, (
            "Step 'validate-all-flags' not found in recipe steps"
        )

    def test_validate_all_flags_is_bash(self, steps_by_id):
        """validate-all-flags must be a bash step."""
        step = steps_by_id.get("validate-all-flags", {})
        assert step.get("type") == "bash"

    def test_validate_all_flags_output_is_validation_flags(self, steps_by_id):
        """validate-all-flags output must be 'validation_flags'."""
        step = steps_by_id.get("validate-all-flags", {})
        assert step.get("output") == "validation_flags"

    def test_validate_all_flags_parse_json(self, steps_by_id):
        """validate-all-flags must have parse_json: true."""
        step = steps_by_id.get("validate-all-flags", {})
        assert step.get("parse_json") is True

    def test_validate_all_flags_depends_on_environment_check(self, steps_by_id):
        """validate-all-flags must depend on environment-check."""
        step = steps_by_id.get("validate-all-flags", {})
        depends = step.get("depends_on", [])
        assert "environment-check" in depends


# ── validate-recipes step ─────────────────────────────────────────────────────


class TestValidateRecipesStep:
    def test_validate_recipes_step_exists(self, steps_by_id):
        """validate-recipes step must be present."""
        assert "validate-recipes" in steps_by_id, (
            "Step 'validate-recipes' not found in recipe steps"
        )

    def test_validate_recipes_is_recipe_type(self, steps_by_id):
        """validate-recipes must be type 'recipe'."""
        step = steps_by_id.get("validate-recipes", {})
        assert step.get("type") == "recipe"

    def test_validate_recipes_has_condition(self, steps_by_id):
        """validate-recipes must have a condition for opt-in behavior."""
        step = steps_by_id.get("validate-recipes", {})
        assert "condition" in step, "validate-recipes must have a condition (opt-in)"
        assert "validate_recipes" in step["condition"]

    def test_validate_recipes_on_error_continue(self, steps_by_id):
        """validate-recipes must have on_error: continue."""
        step = steps_by_id.get("validate-recipes", {})
        assert step.get("on_error") == "continue"

    def test_validate_recipes_output_is_recipe_validation(self, steps_by_id):
        """validate-recipes output must be 'recipe_validation'."""
        step = steps_by_id.get("validate-recipes", {})
        assert step.get("output") == "recipe_validation"

    def test_validate_recipes_depends_on_repo_discovery(self, steps_by_id):
        """validate-recipes must depend on repo-discovery."""
        step = steps_by_id.get("validate-recipes", {})
        depends = step.get("depends_on", [])
        assert "repo-discovery" in depends

    def test_validate_recipes_depends_on_validate_all_flags(self, steps_by_id):
        """validate-recipes must depend on validate-all-flags.

        This is required because the condition reads validation_flags.validate_recipes,
        which is the output of validate-all-flags. Without this dependency, the recipe
        engine could evaluate the condition before validate-all-flags completes.
        """
        step = steps_by_id.get("validate-recipes", {})
        depends = step.get("depends_on", [])
        assert "validate-all-flags" in depends, (
            "validate-recipes depends_on is missing 'validate-all-flags'. "
            "The condition {{validation_flags.validate_recipes}} consumes the output "
            "of validate-all-flags; the dependency must be explicit."
        )

    def test_validate_recipes_context_passes_repo_path(self, steps_by_id):
        """validate-recipes context must pass repo_path."""
        step = steps_by_id.get("validate-recipes", {})
        ctx = step.get("context", {})
        assert "repo_path" in ctx

    def test_validate_recipes_context_passes_recipes_dir(self, steps_by_id):
        """validate-recipes context must pass recipes_dir."""
        step = steps_by_id.get("validate-recipes", {})
        ctx = step.get("context", {})
        assert "recipes_dir" in ctx

    def test_validate_recipes_context_passes_known_agents(self, steps_by_id):
        """validate-recipes context must pass known_agents."""
        step = steps_by_id.get("validate-recipes", {})
        ctx = step.get("context", {})
        assert "known_agents" in ctx


# ── set-default-recipe-validation step ───────────────────────────────────────


class TestSetDefaultRecipeValidationStep:
    def test_set_default_step_exists(self, steps_by_id):
        """set-default-recipe-validation step must be present."""
        assert "set-default-recipe-validation" in steps_by_id, (
            "Step 'set-default-recipe-validation' not found in recipe steps"
        )

    def test_set_default_is_bash(self, steps_by_id):
        """set-default-recipe-validation must be a bash step."""
        step = steps_by_id.get("set-default-recipe-validation", {})
        assert step.get("type") == "bash"

    def test_set_default_output_is_recipe_validation(self, steps_by_id):
        """set-default-recipe-validation output must be 'recipe_validation'."""
        step = steps_by_id.get("set-default-recipe-validation", {})
        assert step.get("output") == "recipe_validation"

    def test_set_default_parse_json(self, steps_by_id):
        """set-default-recipe-validation must have parse_json: true."""
        step = steps_by_id.get("set-default-recipe-validation", {})
        assert step.get("parse_json") is True

    def test_set_default_depends_on_validate_all_flags(self, steps_by_id):
        """set-default-recipe-validation must depend on validate-all-flags."""
        step = steps_by_id.get("set-default-recipe-validation", {})
        depends = step.get("depends_on", [])
        assert "validate-all-flags" in depends


# ── quality-classification depends_on ────────────────────────────────────────


class TestQualityClassificationDependsOn:
    def test_quality_classification_exists(self, steps_by_id):
        """quality-classification step must be present."""
        assert "quality-classification" in steps_by_id

    def test_quality_classification_depends_on_validate_recipes(self, steps_by_id):
        """quality-classification must depend on validate-recipes."""
        step = steps_by_id.get("quality-classification", {})
        depends = step.get("depends_on", [])
        assert "validate-recipes" in depends, (
            "quality-classification depends_on must include 'validate-recipes'"
        )

    def test_quality_classification_depends_on_set_default_recipe_validation(
        self, steps_by_id
    ):
        """quality-classification must depend on set-default-recipe-validation."""
        step = steps_by_id.get("quality-classification", {})
        depends = step.get("depends_on", [])
        assert "set-default-recipe-validation" in depends, (
            "quality-classification depends_on must include 'set-default-recipe-validation'"
        )


# ── synthesize-report prompt ──────────────────────────────────────────────────


class TestSynthesizeReportPrompt:
    def test_synthesize_report_exists(self, steps_by_id):
        """synthesize-report step must be present."""
        assert "synthesize-report" in steps_by_id

    def test_synthesize_report_includes_recipe_validation_block(self, recipe_data):
        """synthesize-report prompt must include Recipe Validation section."""
        _, content = recipe_data
        assert "Recipe Validation" in content, (
            "synthesize-report prompt must include a Recipe Validation section"
        )

    def test_synthesize_report_depends_on_set_default_recipe_validation(
        self, steps_by_id
    ):
        """synthesize-report must depend on set-default-recipe-validation."""
        step = steps_by_id.get("synthesize-report", {})
        depends = step.get("depends_on", [])
        assert "set-default-recipe-validation" in depends, (
            "synthesize-report depends_on must include 'set-default-recipe-validation'"
        )
