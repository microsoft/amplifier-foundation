"""Tests for GPT-5.4 provider bundle and example updates (task-14)."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Provider YAML tests
# ---------------------------------------------------------------------------


def test_openai_gpt_yaml_default_model():
    """providers/openai-gpt.yaml must have default_model: gpt-5.4."""
    path = REPO_ROOT / "providers" / "openai-gpt.yaml"
    data = yaml.safe_load(path.read_text())
    provider_config = data["providers"][0]["config"]
    assert provider_config["default_model"] == "gpt-5.4"


def test_openai_gpt_5_yaml_default_model():
    """providers/openai-gpt-5.yaml must have default_model: gpt-5.4."""
    path = REPO_ROOT / "providers" / "openai-gpt-5.yaml"
    data = yaml.safe_load(path.read_text())
    provider_config = data["providers"][0]["config"]
    assert provider_config["default_model"] == "gpt-5.4"


def test_openai_gpt_codex_yaml_default_model():
    """providers/openai-gpt-codex.yaml must have default_model: gpt-5.4."""
    path = REPO_ROOT / "providers" / "openai-gpt-codex.yaml"
    data = yaml.safe_load(path.read_text())
    provider_config = data["providers"][0]["config"]
    assert provider_config["default_model"] == "gpt-5.4"


# ---------------------------------------------------------------------------
# Example 18 – custom hooks pricing dict
# ---------------------------------------------------------------------------


def test_example18_pricing_has_gpt54():
    """Example 18 PRICING dict must include gpt-5.4 with correct pricing."""
    path = REPO_ROOT / "examples" / "18_custom_hooks.py"
    content = path.read_text()
    assert '"gpt-5.4"' in content
    assert '"input": 2.50' in content or "'input': 2.50" in content


def test_example18_pricing_has_gpt54_pro():
    """Example 18 PRICING dict must include gpt-5.4-pro with correct pricing."""
    path = REPO_ROOT / "examples" / "18_custom_hooks.py"
    content = path.read_text()
    assert '"gpt-5.4-pro"' in content
    assert '"input": 30.00' in content or "'input': 30.00" in content


def test_example18_no_stale_gpt52():
    """Example 18 must not contain gpt-5.2."""
    path = REPO_ROOT / "examples" / "18_custom_hooks.py"
    content = path.read_text()
    assert "gpt-5.2" not in content


# ---------------------------------------------------------------------------
# Example 22 – custom orchestrator routing
# ---------------------------------------------------------------------------


def test_example22_docstring_updated():
    """Example 22 docstring must reference GPT-5.4 and GPT-5-mini."""
    path = REPO_ROOT / "examples" / "22_custom_orchestrator_routing.py"
    content = path.read_text()
    assert "GPT-5.4" in content
    assert "GPT-5-mini" in content


def test_example22_mini_model_updated():
    """Example 22 must use gpt-5-mini for mini_model config."""
    path = REPO_ROOT / "examples" / "22_custom_orchestrator_routing.py"
    content = path.read_text()
    assert '"mini_model": "gpt-5-mini"' in content


def test_example22_codex_model_updated():
    """Example 22 must use gpt-5.4 for codex_model config."""
    path = REPO_ROOT / "examples" / "22_custom_orchestrator_routing.py"
    content = path.read_text()
    assert '"codex_model": "gpt-5.4"' in content


def test_example22_no_stale_gpt52():
    """Example 22 must not contain gpt-5.2."""
    path = REPO_ROOT / "examples" / "22_custom_orchestrator_routing.py"
    content = path.read_text()
    assert "gpt-5.2" not in content


def test_example22_no_stale_gpt51_codex():
    """Example 22 must not contain gpt-5.1-codex."""
    path = REPO_ROOT / "examples" / "22_custom_orchestrator_routing.py"
    content = path.read_text()
    assert "gpt-5.1-codex" not in content


# ---------------------------------------------------------------------------
# Cross-file stale string check
# ---------------------------------------------------------------------------


def test_no_stale_strings_in_modified_files():
    """No stale gpt-5.1-codex or gpt-5.2 references in any modified files."""
    stale_patterns = ["gpt-5.1-codex", "gpt-5.2"]
    files_to_check = [
        REPO_ROOT / "providers" / "openai-gpt.yaml",
        REPO_ROOT / "providers" / "openai-gpt-5.yaml",
        REPO_ROOT / "providers" / "openai-gpt-codex.yaml",
        REPO_ROOT / "examples" / "18_custom_hooks.py",
        REPO_ROOT / "examples" / "22_custom_orchestrator_routing.py",
    ]
    violations = []
    for fpath in files_to_check:
        content = fpath.read_text()
        for pattern in stale_patterns:
            if pattern in content:
                violations.append(f"{fpath.name} contains '{pattern}'")
    assert violations == [], f"Stale strings found: {violations}"


# ---------------------------------------------------------------------------
# Router-orchestrator module defaults
# ---------------------------------------------------------------------------

ROUTER_INIT = (
    REPO_ROOT
    / "examples"
    / "modules"
    / "router-orchestrator"
    / "amplifier_module_router_orchestrator"
    / "__init__.py"
)


def test_router_orchestrator_mini_model_default_updated():
    """RoutingOrchestrator default mini_model must be gpt-5-mini, not gpt-5.2."""
    content = ROUTER_INIT.read_text()
    assert '"gpt-5-mini"' in content, "mini_model default should be gpt-5-mini"
    assert '"gpt-5.2"' not in content, "stale gpt-5.2 default must be removed"


def test_router_orchestrator_codex_model_default_updated():
    """RoutingOrchestrator default codex_model must be gpt-5.4, not gpt-5.1-codex."""
    content = ROUTER_INIT.read_text()
    assert '"gpt-5.4"' in content, "codex_model default should be gpt-5.4"
    assert '"gpt-5.1-codex"' not in content, (
        "stale gpt-5.1-codex default must be removed"
    )


def test_router_orchestrator_no_stale_model_strings():
    """router-orchestrator __init__.py must not contain any stale model strings."""
    content = ROUTER_INIT.read_text()
    for stale in ("gpt-5.2", "gpt-5.1-codex"):
        assert stale not in content, (
            f"Stale model string '{stale}' found in router-orchestrator"
        )
