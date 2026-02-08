"""Tests for Bundle validator."""

from pathlib import Path

import pytest
from amplifier_foundation.bundle import Bundle
from amplifier_foundation.exceptions import BundleValidationError
from amplifier_foundation.validator import (
    BundleValidator,
    ValidationResult,
    validate_bundle,
    validate_bundle_completeness,
    validate_bundle_completeness_or_raise,
    validate_bundle_or_raise,
)


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_initial_state(self) -> None:
        """Starts valid with no errors or warnings."""
        result = ValidationResult()
        assert result.valid is True
        assert result.errors == []
        assert result.warnings == []

    def test_add_error_marks_invalid(self) -> None:
        """Adding error sets valid to False."""
        result = ValidationResult()
        result.add_error("test error")
        assert result.valid is False
        assert "test error" in result.errors

    def test_add_warning_keeps_valid(self) -> None:
        """Adding warning keeps valid True."""
        result = ValidationResult()
        result.add_warning("test warning")
        assert result.valid is True
        assert "test warning" in result.warnings


class TestBundleValidator:
    """Tests for BundleValidator class."""

    def test_validate_minimal_bundle(self) -> None:
        """Minimal bundle with name is valid."""
        bundle = Bundle(name="test")
        validator = BundleValidator()
        result = validator.validate(bundle)
        assert result.valid is True

    def test_validate_missing_name(self) -> None:
        """Bundle without name is invalid."""
        bundle = Bundle(name="")
        validator = BundleValidator()
        result = validator.validate(bundle)
        assert result.valid is False
        assert any("name" in e for e in result.errors)

    def test_validate_module_entry_missing_module(self) -> None:
        """Module entry without 'module' field is invalid."""
        bundle = Bundle(name="test", providers=[{"config": {}}])
        validator = BundleValidator()
        result = validator.validate(bundle)
        assert result.valid is False
        assert any("module" in e.lower() for e in result.errors)

    def test_validate_module_entry_invalid_config(self) -> None:
        """Module entry with non-dict config is invalid."""
        bundle = Bundle(
            name="test", providers=[{"module": "provider-test", "config": "string"}]
        )
        validator = BundleValidator()
        result = validator.validate(bundle)
        assert result.valid is False
        assert any("config" in e.lower() for e in result.errors)


class TestCompletenessValidation:
    """Tests for mount plan completeness validation."""

    def test_complete_bundle_is_valid(self) -> None:
        """Bundle with session and provider is complete."""
        bundle = Bundle(
            name="complete",
            session={"orchestrator": "loop-basic", "context": "context-simple"},
            providers=[{"module": "provider-anthropic"}],
        )
        validator = BundleValidator()
        result = validator.validate_completeness(bundle)
        assert result.valid is True
        assert result.errors == []

    def test_missing_session_is_incomplete(self) -> None:
        """Bundle without session is incomplete."""
        bundle = Bundle(
            name="no-session",
            providers=[{"module": "provider-anthropic"}],
        )
        validator = BundleValidator()
        result = validator.validate_completeness(bundle)
        assert result.valid is False
        assert any("session" in e.lower() for e in result.errors)

    def test_missing_orchestrator_is_incomplete(self) -> None:
        """Bundle without orchestrator is incomplete."""
        bundle = Bundle(
            name="no-orchestrator",
            session={"context": "context-simple"},
            providers=[{"module": "provider-anthropic"}],
        )
        validator = BundleValidator()
        result = validator.validate_completeness(bundle)
        assert result.valid is False
        assert any("orchestrator" in e.lower() for e in result.errors)

    def test_missing_context_is_incomplete(self) -> None:
        """Bundle without context manager is incomplete."""
        bundle = Bundle(
            name="no-context",
            session={"orchestrator": "loop-basic"},
            providers=[{"module": "provider-anthropic"}],
        )
        validator = BundleValidator()
        result = validator.validate_completeness(bundle)
        assert result.valid is False
        assert any("context" in e.lower() for e in result.errors)

    def test_missing_providers_is_incomplete(self) -> None:
        """Bundle without providers is incomplete."""
        bundle = Bundle(
            name="no-providers",
            session={"orchestrator": "loop-basic", "context": "context-simple"},
            providers=[],
        )
        validator = BundleValidator()
        result = validator.validate_completeness(bundle)
        assert result.valid is False
        assert any("provider" in e.lower() for e in result.errors)

    def test_partial_bundle_is_expected_incomplete(self) -> None:
        """Partial bundles (like providers/) are expectedly incomplete."""
        # Provider-only bundle - this is valid as a bundle but incomplete for mounting
        provider_bundle = Bundle(
            name="anthropic-opus",
            providers=[
                {"module": "provider-anthropic", "config": {"model": "claude-opus-4-6"}}
            ],
        )
        validator = BundleValidator()

        # Basic validation passes
        basic_result = validator.validate(provider_bundle)
        assert basic_result.valid is True

        # Completeness validation fails (as expected)
        completeness_result = validator.validate_completeness(provider_bundle)
        assert completeness_result.valid is False

    def test_validate_completeness_or_raise_raises(self) -> None:
        """validate_completeness_or_raise raises on incomplete bundle."""
        bundle = Bundle(name="incomplete")
        validator = BundleValidator()
        with pytest.raises(BundleValidationError) as exc_info:
            validator.validate_completeness_or_raise(bundle)
        assert "incomplete for mounting" in str(exc_info.value).lower()


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    def test_validate_bundle(self) -> None:
        """validate_bundle convenience function works."""
        bundle = Bundle(name="test")
        result = validate_bundle(bundle)
        assert result.valid is True

    def test_validate_bundle_or_raise(self) -> None:
        """validate_bundle_or_raise raises on invalid bundle."""
        bundle = Bundle(name="")
        with pytest.raises(BundleValidationError):
            validate_bundle_or_raise(bundle)

    def test_validate_bundle_completeness(self) -> None:
        """validate_bundle_completeness convenience function works."""
        bundle = Bundle(
            name="complete",
            session={"orchestrator": "loop-basic", "context": "context-simple"},
            providers=[{"module": "provider-test"}],
        )
        result = validate_bundle_completeness(bundle)
        assert result.valid is True

    def test_validate_bundle_completeness_or_raise(self) -> None:
        """validate_bundle_completeness_or_raise raises on incomplete."""
        bundle = Bundle(name="incomplete")
        with pytest.raises(BundleValidationError):
            validate_bundle_completeness_or_raise(bundle)


class TestStrictMode:
    """Tests for strict mode in BundleValidator."""

    def test_strict_defaults_to_false(self) -> None:
        """BundleValidator strict parameter defaults to False."""
        validator = BundleValidator()
        assert validator._strict is False

    def test_strict_can_be_set_to_true(self) -> None:
        """BundleValidator strict parameter can be set to True."""
        validator = BundleValidator(strict=True)
        assert validator._strict is True

    def test_non_strict_missing_context_path_is_warning(self, tmp_path: Path) -> None:
        """Non-strict mode: missing context path produces a warning, not an error."""
        bundle = Bundle(
            name="test",
            base_path=tmp_path,
            context={"missing": tmp_path / "nonexistent.md"},
        )
        validator = BundleValidator()
        result = validator.validate(bundle)
        assert result.valid is True
        assert any("nonexistent" in w for w in result.warnings)
        assert result.errors == []

    def test_strict_missing_context_path_is_error(self, tmp_path: Path) -> None:
        """Strict mode: missing context path produces an error."""
        bundle = Bundle(
            name="test",
            base_path=tmp_path,
            context={"missing": tmp_path / "nonexistent.md"},
        )
        validator = BundleValidator(strict=True)
        result = validator.validate(bundle)
        assert result.valid is False
        assert any("nonexistent" in e for e in result.errors)

    def test_strict_existing_context_path_no_error(self, tmp_path: Path) -> None:
        """Strict mode: existing context path produces no error."""
        context_file = tmp_path / "exists.md"
        context_file.write_text("# Context")

        bundle = Bundle(
            name="test",
            base_path=tmp_path,
            context={"exists": context_file},
        )
        validator = BundleValidator(strict=True)
        result = validator.validate(bundle)
        assert result.valid is True
        assert result.errors == []
        assert result.warnings == []
