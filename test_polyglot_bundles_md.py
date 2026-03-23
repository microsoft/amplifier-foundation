"""Test that context/POLYGLOT_BUNDLES.md exists with correct content per spec."""

import os
import re

import pytest

CONTEXT_FILE = os.path.join(os.path.dirname(__file__), "context", "POLYGLOT_BUNDLES.md")


@pytest.fixture(scope="module")
def md_content():
    with open(CONTEXT_FILE) as f:
        return f.read()


def test_file_exists():
    """POLYGLOT_BUNDLES.md must exist in the context/ directory."""
    assert os.path.isfile(CONTEXT_FILE), f"File not found at {CONTEXT_FILE}"


def test_file_approximately_50_lines(md_content):
    """File should be approximately 50 lines (within ±15 lines)."""
    line_count = len(md_content.splitlines())
    assert 35 <= line_count <= 65, f"Expected ~50 lines (35-65), got {line_count}"


def test_top_level_heading(md_content):
    """File must have '# Polyglot Bundle Patterns' as top-level heading."""
    assert "# Polyglot Bundle Patterns" in md_content, (
        "Must contain '# Polyglot Bundle Patterns' heading"
    )


def test_description_mentions_amplifier_toml(md_content):
    """Description must mention amplifier.toml for module self-description."""
    assert "amplifier.toml" in md_content, "Description must mention amplifier.toml"


def test_description_mentions_different_languages(md_content):
    """Description must mention different/multiple languages."""
    assert "language" in md_content.lower(), "Description must mention languages"


def test_how_it_works_section(md_content):
    """File must have a '## How It Works' section."""
    assert "## How It Works" in md_content, "Must contain '## How It Works' section"


def test_how_it_works_foundation_transport_agnostic(md_content):
    """How It Works must mention Foundation resolves sources (transport-agnostic)."""
    assert "transport" in md_content.lower(), (
        "Must mention transport-agnostic resolution"
    )
    assert "foundation" in md_content.lower(), "Must mention Foundation"


def test_how_it_works_core_reads_amplifier_toml(md_content):
    """How It Works must mention Core reads amplifier.toml for transport type."""
    assert "core" in md_content.lower(), "Must mention Core reading amplifier.toml"


def test_how_it_works_host_consumption_strategy(md_content):
    """How It Works must mention Host decides consumption strategy."""
    assert "host" in md_content.lower(), "Must mention Host consumption strategy"


def test_module_activation_section(md_content):
    """File must have a '## Module Activation' section."""
    assert "## Module Activation" in md_content, (
        "Must contain '## Module Activation' section"
    )


def test_module_activation_download_clone(md_content):
    """Module Activation must mention download/clone source as first step."""
    content_lower = md_content.lower()
    assert "download" in content_lower or "clone" in content_lower, (
        "Must mention download/clone source step"
    )


def test_module_activation_skip_pip_install(md_content):
    """Module Activation must mention skipping uv pip install for rust/wasm/grpc."""
    assert "pip install" in md_content.lower() or "uv pip" in md_content.lower(), (
        "Must mention skipping pip install step"
    )


def test_module_activation_rust_wasm_grpc(md_content):
    """Module Activation must mention rust/wasm/grpc transport types."""
    content_lower = md_content.lower()
    assert "rust" in content_lower, "Must mention rust transport"
    assert "wasm" in content_lower, "Must mention wasm transport"
    assert "grpc" in content_lower, "Must mention grpc transport"


def test_module_activation_python_or_absent(md_content):
    """Module Activation must mention python or absent transport proceeds normally."""
    content_lower = md_content.lower()
    assert "python" in content_lower, "Must mention python transport"
    assert (
        "absent" in content_lower
        or "missing" in content_lower
        or "normal" in content_lower
    ), "Must mention absent transport or normal flow"


def test_clone_integrity_section(md_content):
    """File must have a '## Clone Integrity' section."""
    assert "## Clone Integrity" in md_content, (
        "Must contain '## Clone Integrity' section"
    )


def test_clone_integrity_verify_function(md_content):
    """Clone Integrity must mention _verify_clone_integrity function."""
    assert "_verify_clone_integrity" in md_content, (
        "Must mention _verify_clone_integrity function"
    )


def test_clone_integrity_python_markers(md_content):
    """Clone Integrity must mention pyproject.toml/setup.py/setup.cfg for Python."""
    assert "pyproject.toml" in md_content, "Must mention pyproject.toml"
    assert "setup.py" in md_content, "Must mention setup.py"
    assert "setup.cfg" in md_content, "Must mention setup.cfg"


def test_clone_integrity_bundle_markers(md_content):
    """Clone Integrity must mention bundle.md/bundle.yaml for bundles."""
    assert "bundle.md" in md_content, "Must mention bundle.md"
    assert "bundle.yaml" in md_content, "Must mention bundle.yaml"


def test_clone_integrity_non_python_marker(md_content):
    """Clone Integrity must mention amplifier.toml for non-Python modules."""
    assert "amplifier.toml" in md_content, "Must mention amplifier.toml for non-Python"


def test_bundle_structure_section(md_content):
    """File must have a '## Bundle Structure' section for mixed-language modules."""
    assert "## Bundle Structure" in md_content, (
        "Must contain '## Bundle Structure' section"
    )


def test_bundle_structure_python_tool_directory(md_content):
    """Bundle Structure must show python-tool/ directory with pyproject.toml."""
    assert "python-tool" in md_content, "Must show python-tool/ directory"
    assert "pyproject.toml" in md_content, (
        "Must mention pyproject.toml in bundle structure"
    )


def test_bundle_structure_rust_provider_directory(md_content):
    """Bundle Structure must show rust-provider/ directory."""
    assert "rust-provider" in md_content, "Must show rust-provider/ directory"


def test_bundle_structure_cargo_toml(md_content):
    """Bundle Structure must show Cargo.toml in the rust-provider."""
    assert "Cargo.toml" in md_content, "Must mention Cargo.toml for rust-provider"


def test_bundle_structure_providers_and_context(md_content):
    """Bundle Structure must show providers/ and context/ directories."""
    assert "providers/" in md_content, "Must show providers/ directory"
    assert "context/" in md_content, "Must show context/ directory"


def test_bundle_structure_foundation_treats_independently(md_content):
    """Bundle Structure note must state Foundation treats each module independently."""
    assert "independently" in md_content.lower(), (
        "Must note Foundation treats each module directory independently"
    )


def test_no_changelog_language(md_content):
    """File must not contain changelog language."""
    forbidden = re.compile(r"added|new as of|v1\.|changelog|addendum", re.IGNORECASE)
    match = forbidden.search(md_content)
    assert match is None, f"Found forbidden changelog language: '{match.group()}'"
