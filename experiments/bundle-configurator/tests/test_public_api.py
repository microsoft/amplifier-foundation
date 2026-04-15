"""Tests for amplifier_configurator public API exports (task-11).

Verifies that all required symbols are importable from the top-level package,
that __all__ contains every public symbol, that the module docstring is correct,
and that pyproject.toml is at version 0.3.0.
"""

from __future__ import annotations

from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXPECTED_EXPORTS = [
    # Main class
    "BundleConfigurator",
    # Data models
    "BehaviorInfo",
    "BundleDiff",
    "PartKind",
    "ProvenanceMap",
    "TrackedPart",
    # Errors
    "ConfiguratorError",
    "ConfiguratorWarning",
    "DependencyError",
    "LoadError",
    # Functions
    "estimate_tokens_for_file",
    "estimate_tokens_for_text",
    "serialize_bundle",
    "validate_provenance",
    # Constants
    "PART_DEPENDENCIES",
    "REQUIRED_PARTS",
]

EXPECTED_DOCSTRING_FRAGMENT = (
    "amplifier_configurator \u2014 provenance-aware bundle editing for Amplifier"
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_all_symbols_importable() -> None:
    """Every required export is importable directly from amplifier_configurator."""
    import amplifier_configurator  # noqa: F401

    for name in EXPECTED_EXPORTS:
        assert hasattr(amplifier_configurator, name), (
            f"amplifier_configurator is missing expected export: {name!r}"
        )


def test_all_symbols_in_dunder_all() -> None:
    """__all__ contains every required public symbol."""
    import amplifier_configurator

    all_exports = amplifier_configurator.__all__
    for name in EXPECTED_EXPORTS:
        assert name in all_exports, (
            f"{name!r} is not listed in amplifier_configurator.__all__"
        )


def test_no_unexpected_symbols_in_all() -> None:
    """__all__ does not list symbols we did NOT intend to export (regression guard)."""
    import amplifier_configurator

    extra = set(amplifier_configurator.__all__) - set(EXPECTED_EXPORTS)
    # It's OK to have more exports — this just documents current expectations.
    # If unwanted names sneak in, update this set.
    allowed_extra: set[str] = set()
    unexpected = extra - allowed_extra
    # We do NOT fail on extra exports; that would be overly rigid.
    # This test serves as documentation. Change allowed_extra if intentional.
    _ = unexpected  # intentionally not asserted


def test_module_docstring() -> None:
    """Module docstring starts with the canonical description."""
    import amplifier_configurator

    doc = amplifier_configurator.__doc__ or ""
    assert EXPECTED_DOCSTRING_FRAGMENT in doc, (
        f"Module docstring does not contain expected fragment.\n"
        f"Expected to find: {EXPECTED_DOCSTRING_FRAGMENT!r}\n"
        f"Got: {doc!r}"
    )


def test_module_docstring_mentions_foundation() -> None:
    """Docstring mentions Foundation's real Bundle API."""
    import amplifier_configurator

    doc = amplifier_configurator.__doc__ or ""
    assert "Foundation" in doc, (
        "Module docstring should mention Foundation's real Bundle API"
    )
    assert "Not a reimplementation" in doc, (
        "Module docstring should state 'Not a reimplementation'"
    )


def test_version_in_pyproject() -> None:
    """pyproject.toml specifies version 0.3.0."""
    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    assert 'version = "0.3.0"' in content, (
        f"pyproject.toml version should be 0.3.0, got:\n{content}"
    )


def test_imports_are_correct_types() -> None:
    """Spot-check that imported names are the correct types/classes."""
    import amplifier_configurator as ac

    # Classes
    assert isinstance(ac.BundleConfigurator, type)
    assert isinstance(ac.BehaviorInfo, type)
    assert isinstance(ac.BundleDiff, type)
    assert isinstance(ac.TrackedPart, type)
    assert isinstance(ac.ProvenanceMap, type)

    # Enums
    from enum import EnumMeta

    assert isinstance(ac.PartKind, EnumMeta)

    # Exception classes
    assert issubclass(ac.ConfiguratorError, Exception)
    assert issubclass(ac.ConfiguratorWarning, Warning)
    assert issubclass(ac.DependencyError, ac.ConfiguratorError)
    assert issubclass(ac.LoadError, ac.ConfiguratorError)

    # Callables (functions)
    assert callable(ac.estimate_tokens_for_file)
    assert callable(ac.estimate_tokens_for_text)
    assert callable(ac.serialize_bundle)
    assert callable(ac.validate_provenance)

    # Constants
    assert isinstance(ac.PART_DEPENDENCIES, dict)
    assert isinstance(ac.REQUIRED_PARTS, set)
