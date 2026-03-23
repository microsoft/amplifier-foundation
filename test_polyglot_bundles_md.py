"""Test that context/POLYGLOT_BUNDLES.md exists with correct content per spec."""
import os
import re

CONTEXT_FILE = os.path.join(
    os.path.dirname(__file__), "context", "POLYGLOT_BUNDLES.md"
)


def test_file_exists():
    """POLYGLOT_BUNDLES.md must exist in the context/ directory."""
    assert os.path.isfile(CONTEXT_FILE), f"File not found at {CONTEXT_FILE}"


def test_file_approximately_50_lines():
    """File should be approximately 50 lines (within ±15 lines)."""
    with open(CONTEXT_FILE) as f:
        lines = f.readlines()
    line_count = len(lines)
    assert 35 <= line_count <= 65, (
        f"Expected ~50 lines (35-65), got {line_count}"
    )


def test_top_level_heading():
    """File must have '# Polyglot Bundle Patterns' as top-level heading."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    assert "# Polyglot Bundle Patterns" in content, (
        "Must contain '# Polyglot Bundle Patterns' heading"
    )


def test_description_mentions_amplifier_toml():
    """Description must mention amplifier.toml for module self-description."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    assert "amplifier.toml" in content, "Description must mention amplifier.toml"


def test_description_mentions_different_languages():
    """Description must mention different/multiple languages."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    assert "language" in content.lower(), "Description must mention languages"


def test_how_it_works_section():
    """File must have a '## How It Works' section."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    assert "## How It Works" in content, "Must contain '## How It Works' section"


def test_how_it_works_foundation_transport_agnostic():
    """How It Works must mention Foundation resolves sources (transport-agnostic)."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    assert "transport" in content.lower(), "Must mention transport-agnostic resolution"
    assert "foundation" in content.lower(), "Must mention Foundation"


def test_how_it_works_core_reads_amplifier_toml():
    """How It Works must mention Core reads amplifier.toml for transport type."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    assert "core" in content.lower(), "Must mention Core reading amplifier.toml"


def test_how_it_works_host_consumption_strategy():
    """How It Works must mention Host decides consumption strategy."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    assert "host" in content.lower(), "Must mention Host consumption strategy"


def test_module_activation_section():
    """File must have a '## Module Activation' section."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    assert "## Module Activation" in content, (
        "Must contain '## Module Activation' section"
    )


def test_module_activation_download_clone():
    """Module Activation must mention download/clone source as first step."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    content_lower = content.lower()
    assert "download" in content_lower or "clone" in content_lower, (
        "Must mention download/clone source step"
    )


def test_module_activation_skip_pip_install():
    """Module Activation must mention skipping uv pip install for rust/wasm/grpc."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    assert "pip install" in content.lower() or "uv pip" in content.lower(), (
        "Must mention skipping pip install step"
    )


def test_module_activation_rust_wasm_grpc():
    """Module Activation must mention rust/wasm/grpc transport types."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    content_lower = content.lower()
    assert "rust" in content_lower, "Must mention rust transport"
    assert "wasm" in content_lower, "Must mention wasm transport"
    assert "grpc" in content_lower, "Must mention grpc transport"


def test_module_activation_python_or_absent():
    """Module Activation must mention python or absent transport proceeds normally."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    content_lower = content.lower()
    assert "python" in content_lower, "Must mention python transport"
    assert "absent" in content_lower or "missing" in content_lower or "normal" in content_lower, (
        "Must mention absent transport or normal flow"
    )


def test_clone_integrity_section():
    """File must have a '## Clone Integrity' section."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    assert "## Clone Integrity" in content, (
        "Must contain '## Clone Integrity' section"
    )


def test_clone_integrity_verify_function():
    """Clone Integrity must mention _verify_clone_integrity function."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    assert "_verify_clone_integrity" in content, (
        "Must mention _verify_clone_integrity function"
    )


def test_clone_integrity_python_markers():
    """Clone Integrity must mention pyproject.toml/setup.py/setup.cfg for Python."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    assert "pyproject.toml" in content, "Must mention pyproject.toml"
    assert "setup.py" in content, "Must mention setup.py"
    assert "setup.cfg" in content, "Must mention setup.cfg"


def test_clone_integrity_bundle_markers():
    """Clone Integrity must mention bundle.md/bundle.yaml for bundles."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    assert "bundle.md" in content, "Must mention bundle.md"
    assert "bundle.yaml" in content, "Must mention bundle.yaml"


def test_clone_integrity_non_python_marker():
    """Clone Integrity must mention amplifier.toml for non-Python modules."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    assert "amplifier.toml" in content, "Must mention amplifier.toml for non-Python"


def test_bundle_structure_section():
    """File must have a '## Bundle Structure' section for mixed-language modules."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    assert "## Bundle Structure" in content, (
        "Must contain '## Bundle Structure' section"
    )


def test_bundle_structure_python_tool_directory():
    """Bundle Structure must show python-tool/ directory with pyproject.toml."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    assert "python-tool" in content, "Must show python-tool/ directory"
    assert "pyproject.toml" in content, "Must mention pyproject.toml in bundle structure"


def test_bundle_structure_rust_provider_directory():
    """Bundle Structure must show rust-provider/ directory."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    assert "rust-provider" in content, "Must show rust-provider/ directory"


def test_bundle_structure_cargo_toml():
    """Bundle Structure must show Cargo.toml in the rust-provider."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    assert "Cargo.toml" in content, "Must mention Cargo.toml for rust-provider"


def test_bundle_structure_providers_and_context():
    """Bundle Structure must show providers/ and context/ directories."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    assert "providers/" in content, "Must show providers/ directory"
    assert "context/" in content, "Must show context/ directory"


def test_bundle_structure_foundation_treats_independently():
    """Bundle Structure note must state Foundation treats each module independently."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    content_lower = content.lower()
    assert "independently" in content_lower, (
        "Must note Foundation treats each module directory independently"
    )


def test_no_changelog_language():
    """File must not contain changelog language."""
    with open(CONTEXT_FILE) as f:
        content = f.read()
    forbidden = re.compile(r"added|new as of|v1\.|changelog|addendum", re.IGNORECASE)
    match = forbidden.search(content)
    assert match is None, (
        f"Found forbidden changelog language: '{match.group()}'"
    )


if __name__ == "__main__":
    import sys

    tests = [
        test_file_exists,
        test_file_approximately_50_lines,
        test_top_level_heading,
        test_description_mentions_amplifier_toml,
        test_description_mentions_different_languages,
        test_how_it_works_section,
        test_how_it_works_foundation_transport_agnostic,
        test_how_it_works_core_reads_amplifier_toml,
        test_how_it_works_host_consumption_strategy,
        test_module_activation_section,
        test_module_activation_download_clone,
        test_module_activation_skip_pip_install,
        test_module_activation_rust_wasm_grpc,
        test_module_activation_python_or_absent,
        test_clone_integrity_section,
        test_clone_integrity_verify_function,
        test_clone_integrity_python_markers,
        test_clone_integrity_bundle_markers,
        test_clone_integrity_non_python_marker,
        test_bundle_structure_section,
        test_bundle_structure_python_tool_directory,
        test_bundle_structure_rust_provider_directory,
        test_bundle_structure_cargo_toml,
        test_bundle_structure_providers_and_context,
        test_bundle_structure_foundation_treats_independently,
        test_no_changelog_language,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS: {t.__name__}")
        except AssertionError as e:
            print(f"  FAIL: {t.__name__}: {e}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(failed)
