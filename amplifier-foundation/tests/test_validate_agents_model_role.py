"""Tests for model_role validation logic used in validate-agents recipe.

The validate-agents recipe embeds Python code that validates agent frontmatter.
These tests verify the model_role validation logic independently so we can
be confident the recipe's embedded code works correctly.
"""

from pathlib import Path

import pytest
import yaml


# ── Constants (must match what's in the recipe) ──────────────────────────

VALID_MODEL_ROLES = {
    "coding",
    "ui-coding",
    "vision",
    "security-audit",
    "reasoning",
    "critique",
    "creative",
    "writing",
    "research",
    "image-gen",
    "critical-ops",
    "fast",
    "general",
}

DEPRECATED_ROLES = {
    "agentic": "Remove or replace with 'general', 'fast', or 'critical-ops'",
    "coding-image": "Renamed to 'vision'",
    "planning": "Renamed to 'reasoning'",
}


def validate_model_role(frontmatter: dict, meta: dict) -> dict:
    """Validate model_role from agent frontmatter.

    Checks both top-level and meta-level model_role.
    Returns a dict with has_model_role, model_role_value, model_role_issues.
    """
    result = {
        "has_model_role": False,
        "model_role_value": None,
        "model_role_issues": [],
    }

    # model_role can be top-level or inside meta
    raw = frontmatter.get("model_role") or meta.get("model_role")

    if raw is None:
        result["model_role_issues"].append(
            "WARNING: No model_role declared — model routing will use session default. "
            "Consider adding model_role for explicit routing."
        )
        return result

    result["has_model_role"] = True
    result["model_role_value"] = raw

    # Normalize to list
    if isinstance(raw, str):
        model_role_list = [raw]
    elif isinstance(raw, list):
        model_role_list = raw
    else:
        result["model_role_issues"].append(
            f"ERROR: model_role must be a string or list, got {type(raw).__name__}"
        )
        return result

    # Validate each role
    for role in model_role_list:
        if not isinstance(role, str):
            result["model_role_issues"].append(
                f"ERROR: model_role entries must be strings, got {type(role).__name__}: {role}"
            )
            continue

        if role in DEPRECATED_ROLES:
            suggestion = DEPRECATED_ROLES[role]
            result["model_role_issues"].append(
                f"ERROR: model_role '{role}' is deprecated — {suggestion}"
            )
        elif role not in VALID_MODEL_ROLES:
            valid_list = ", ".join(sorted(VALID_MODEL_ROLES))
            result["model_role_issues"].append(
                f"ERROR: model_role '{role}' is not a valid role. "
                f"Valid roles: {valid_list}"
            )

    # Check fallback chain ends with general or fast
    str_roles = [r for r in model_role_list if isinstance(r, str)]
    if len(str_roles) > 1 and str_roles[-1] not in ("general", "fast"):
        result["model_role_issues"].append(
            "WARNING: model_role fallback chain does not end with 'general' or 'fast'. "
            "Consider adding 'general' or 'fast' as the last entry for reliable fallback."
        )

    return result


# ── Tests ────────────────────────────────────────────────────────────────


class TestModelRoleValidation:
    """Tests for model_role validation logic."""

    def test_missing_model_role_returns_warning(self) -> None:
        """Agent with no model_role gets a warning (not error)."""
        result = validate_model_role({"meta": {"name": "test"}}, {"name": "test"})
        assert result["has_model_role"] is False
        assert result["model_role_value"] is None
        assert len(result["model_role_issues"]) == 1
        assert "WARNING" in result["model_role_issues"][0]
        assert "No model_role declared" in result["model_role_issues"][0]

    def test_valid_single_role_string(self) -> None:
        """Single valid model_role string passes cleanly."""
        result = validate_model_role({"model_role": "coding"}, {})
        assert result["has_model_role"] is True
        assert result["model_role_value"] == "coding"
        assert result["model_role_issues"] == []

    def test_valid_role_list(self) -> None:
        """Valid model_role list ending with general passes."""
        result = validate_model_role({"model_role": ["coding", "general"]}, {})
        assert result["has_model_role"] is True
        assert result["model_role_value"] == ["coding", "general"]
        assert result["model_role_issues"] == []

    def test_invalid_role_name_returns_error(self) -> None:
        """Invalid role name produces an ERROR."""
        result = validate_model_role({"model_role": "nonexistent"}, {})
        assert result["has_model_role"] is True
        assert len(result["model_role_issues"]) == 1
        assert "ERROR" in result["model_role_issues"][0]
        assert "'nonexistent' is not a valid role" in result["model_role_issues"][0]

    def test_deprecated_role_returns_error_with_suggestion(self) -> None:
        """Deprecated role name produces ERROR with 'did you mean?' suggestion."""
        result = validate_model_role({"model_role": "agentic"}, {})
        assert result["has_model_role"] is True
        assert len(result["model_role_issues"]) == 1
        assert "ERROR" in result["model_role_issues"][0]
        assert "deprecated" in result["model_role_issues"][0]
        assert "general" in result["model_role_issues"][0]

    def test_deprecated_coding_image(self) -> None:
        """coding-image is deprecated, suggests vision."""
        result = validate_model_role({"model_role": "coding-image"}, {})
        assert any("vision" in i for i in result["model_role_issues"])

    def test_deprecated_planning(self) -> None:
        """planning is deprecated, suggests reasoning."""
        result = validate_model_role({"model_role": "planning"}, {})
        assert any("reasoning" in i for i in result["model_role_issues"])

    def test_fallback_chain_not_ending_with_general_or_fast(self) -> None:
        """Fallback chain not ending with general/fast produces a warning."""
        result = validate_model_role({"model_role": ["coding", "reasoning"]}, {})
        assert result["has_model_role"] is True
        # Should have the fallback warning
        warnings = [i for i in result["model_role_issues"] if "WARNING" in i]
        assert len(warnings) == 1
        assert "fallback chain" in warnings[0]

    def test_fallback_chain_ending_with_fast_is_ok(self) -> None:
        """Fallback chain ending with fast is fine."""
        result = validate_model_role({"model_role": ["coding", "fast"]}, {})
        fallback_warnings = [
            i for i in result["model_role_issues"] if "fallback chain" in i
        ]
        assert fallback_warnings == []

    def test_single_role_no_fallback_warning(self) -> None:
        """Single role (not a chain) does NOT get fallback chain warning."""
        result = validate_model_role({"model_role": "coding"}, {})
        assert result["model_role_issues"] == []

    def test_model_role_in_meta_section(self) -> None:
        """model_role in meta section is also picked up."""
        result = validate_model_role({}, {"model_role": "fast"})
        assert result["has_model_role"] is True
        assert result["model_role_value"] == "fast"
        assert result["model_role_issues"] == []

    def test_top_level_takes_precedence_over_meta(self) -> None:
        """Top-level model_role takes precedence over meta."""
        result = validate_model_role({"model_role": "coding"}, {"model_role": "fast"})
        assert result["model_role_value"] == "coding"

    def test_all_valid_roles_accepted(self) -> None:
        """Every role in VALID_MODEL_ROLES is accepted."""
        for role in VALID_MODEL_ROLES:
            result = validate_model_role({"model_role": role}, {})
            errors = [i for i in result["model_role_issues"] if "ERROR" in i]
            assert errors == [], f"Role '{role}' should be valid but got: {errors}"

    def test_invalid_type_returns_error(self) -> None:
        """Non-string, non-list model_role returns an error."""
        result = validate_model_role({"model_role": 42}, {})
        assert result["has_model_role"] is True
        assert any("must be a string or list" in i for i in result["model_role_issues"])

    def test_mixed_valid_and_invalid_roles_in_list(self) -> None:
        """List with both valid and invalid roles reports error only for invalid."""
        result = validate_model_role({"model_role": ["coding", "bogus", "general"]}, {})
        assert result["has_model_role"] is True
        errors = [i for i in result["model_role_issues"] if "ERROR" in i]
        assert len(errors) == 1
        assert "'bogus'" in errors[0]


class TestRecipeYAMLValidity:
    """Verify the recipe file is valid YAML after our changes."""

    def test_recipe_is_valid_yaml(self) -> None:
        """validate-agents.yaml must be parseable as YAML."""
        recipe_path = Path(__file__).parent.parent / "recipes" / "validate-agents.yaml"
        if not recipe_path.exists():
            pytest.skip("Recipe file not found")
        content = recipe_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        assert isinstance(data, dict)
        assert data.get("name") == "validate-agents"

    def test_recipe_contains_model_role_constants(self) -> None:
        """Recipe's structural-validation step contains VALID_MODEL_ROLES."""
        recipe_path = Path(__file__).parent.parent / "recipes" / "validate-agents.yaml"
        if not recipe_path.exists():
            pytest.skip("Recipe file not found")
        content = recipe_path.read_text(encoding="utf-8")
        assert "VALID_MODEL_ROLES" in content
        assert "DEPRECATED_ROLES" in content

    def test_recipe_contains_model_role_report_section(self) -> None:
        """Recipe's synthesize-report step mentions model role coverage."""
        recipe_path = Path(__file__).parent.parent / "recipes" / "validate-agents.yaml"
        if not recipe_path.exists():
            pytest.skip("Recipe file not found")
        content = recipe_path.read_text(encoding="utf-8")
        assert "Model Role" in content
