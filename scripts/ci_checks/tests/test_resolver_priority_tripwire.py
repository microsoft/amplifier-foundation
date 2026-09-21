"""Tests for resolver_priority_tripwire.py.

The 4 "must fire pre-fix / must be silent post-fix" regression cases are the
acceptance tests for this tool -- they use the literal, historically-real
code blobs from the fixes each anti-pattern occurrence, copied into
fixtures/ with provenance headers. See fixtures/*.py for source commits.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import resolver_priority_tripwire as tripwire  # noqa: E402
import _yaml_lite  # noqa: E402

FIXTURES = SCRIPT_DIR / "tests" / "fixtures"
DEFAULT_CFG = {
    "id_field_names": tripwire.ID_FIELD_NAMES,
    "type_field_names": tripwire.TYPE_FIELD_NAMES,
    "tiebreak_field_names": tripwire.TIEBREAK_FIELD_NAMES,
    "exclude_globs": tripwire.DEFAULT_EXCLUDE_GLOBS,
}


def _findings(fixture_name: str) -> list[tripwire.Finding]:
    return tripwire.analyze_file(FIXTURES / fixture_name, FIXTURES, DEFAULT_CFG)


# --------------------------------------------------------------------------
# The 4 mandatory historical regression fixtures.
# --------------------------------------------------------------------------
HISTORICAL_PAIRS = [
    # (label, pre-fix fixture, post-fix fixture)
    ("foundation-267", "fixture1_spawn_utils_pre.py", "fixture1_spawn_utils_post.py"),
    ("routing-matrix-31", "fixture2_resolver_pre.py", "fixture2_resolver_post.py"),
    (
        "app-cli-214-provider-manager",
        "fixture3a_provider_manager_pre.py",
        "fixture3a_provider_manager_post.py",
    ),
    ("app-cli-214-routing", "fixture3b_routing_pre.py", "fixture3b_routing_post.py"),
    (
        "app-cli-215-provider-cmd",
        "fixture4_provider_cmd_pre.py",
        "fixture4_provider_cmd_post.py",
    ),
]


@pytest.mark.parametrize(
    "label,pre,post", HISTORICAL_PAIRS, ids=[p[0] for p in HISTORICAL_PAIRS]
)
def test_fires_on_prefix_silent_on_postfix(label, pre, post):
    pre_findings = _findings(pre)
    assert pre_findings, f"{label}: expected a finding on pre-fix blob {pre}, got none"

    post_findings = _findings(post)
    assert not post_findings, (
        f"{label}: expected NO finding on post-fix blob {post} (tie-break present), got {post_findings}"
    )


def test_foundation_267_is_tier1_error_tuple_idiom():
    findings = _findings("fixture1_spawn_utils_pre.py")
    assert any(f.tier == "ERROR" for f in findings)


def test_routing_matrix_31_is_tier1_error_id_and_type_chain():
    findings = _findings("fixture2_resolver_pre.py")
    assert any(f.tier == "ERROR" for f in findings)


def test_app_cli_214_provider_manager_is_type_only_tier2():
    """Single comparison on 'module' only (no id field in the same chain) ->
    Tier 2, WARN unless escalated by tiebreak vocab elsewhere in the file."""
    findings = _findings("fixture3a_provider_manager_pre.py")
    assert findings
    assert all(f.tier in ("WARN", "ERROR") for f in findings)


def test_app_cli_214_routing_is_tier1_error_id_and_type_chain():
    """id/type checks combined in one OR-chain -> Tier 1 ERROR."""
    findings = _findings("fixture3b_routing_pre.py")
    assert any(f.tier == "ERROR" for f in findings)


# --------------------------------------------------------------------------
# Unit-level detection behavior
# --------------------------------------------------------------------------
def test_id_only_comparison_does_not_fire():
    src = """
def find_by_id(items, item_id):
    for item in items:
        if item.get("id") == item_id:
            return item
    return None
"""
    tmp = FIXTURES / "_tmp_id_only.py"
    tmp.write_text(src, encoding="utf-8")
    try:
        findings = tripwire.analyze_file(tmp, FIXTURES, DEFAULT_CFG)
        assert findings == []
    finally:
        tmp.unlink()


def test_min_tiebreak_suppresses_type_only_match():
    src = """
def find_by_type(items, type_name):
    matches = []
    for item in items:
        if item.get("module") == type_name:
            matches.append(item)
    if not matches:
        return None
    return min(matches, key=lambda i: i.get("config", {}).get("priority", 100))
"""
    tmp = FIXTURES / "_tmp_min_suppressed.py"
    tmp.write_text(src, encoding="utf-8")
    try:
        findings = tripwire.analyze_file(tmp, FIXTURES, DEFAULT_CFG)
        assert findings == []
    finally:
        tmp.unlink()


def test_type_only_escalates_to_error_when_tiebreak_vocab_present_elsewhere():
    src = """
def unrelated_priority_helper(x):
    return x.get("priority", 100)


def find_by_type(items, type_name):
    for item in items:
        if item.get("module") == type_name:
            return item
    return None
"""
    tmp = FIXTURES / "_tmp_escalate.py"
    tmp.write_text(src, encoding="utf-8")
    try:
        findings = tripwire.analyze_file(tmp, FIXTURES, DEFAULT_CFG)
        assert findings
        assert all(f.tier == "ERROR" for f in findings if f.function == "find_by_type")
    finally:
        tmp.unlink()


def test_type_only_stays_warn_without_tiebreak_vocab():
    src = """
def find_by_type(items, type_name):
    for item in items:
        if item.get("module") == type_name:
            return item
    return None
"""
    tmp = FIXTURES / "_tmp_warn.py"
    tmp.write_text(src, encoding="utf-8")
    try:
        findings = tripwire.analyze_file(tmp, FIXTURES, DEFAULT_CFG)
        assert findings
        assert all(f.tier == "WARN" for f in findings)
    finally:
        tmp.unlink()


def test_no_parameter_function_not_flagged():
    src = """
def process(items):
    for item in items:
        if item.get("module") == "anthropic":
            return item
    return None
"""
    tmp = FIXTURES / "_tmp_no_param.py"
    tmp.write_text(src, encoding="utf-8")
    try:
        findings = tripwire.analyze_file(tmp, FIXTURES, DEFAULT_CFG)
        assert findings == []
    finally:
        tmp.unlink()


def test_no_for_loop_not_flagged():
    src = """
def check(item, name):
    if item.get("module") == name:
        return item
    return None
"""
    tmp = FIXTURES / "_tmp_no_loop.py"
    tmp.write_text(src, encoding="utf-8")
    try:
        findings = tripwire.analyze_file(tmp, FIXTURES, DEFAULT_CFG)
        assert findings == []
    finally:
        tmp.unlink()


# --------------------------------------------------------------------------
# Config loading
# --------------------------------------------------------------------------
def test_load_simple_yaml_parses_lists_and_scalars():
    text = """
exclude_globs:
  - ".venv/**"
  - "**/tests/**"
tiebreak_field_names: [priority, rank, precedence]
"""
    data = _yaml_lite.load_simple_yaml(text)
    assert data["exclude_globs"] == [".venv/**", "**/tests/**"]
    assert data["tiebreak_field_names"] == ["priority", "rank", "precedence"]


def test_load_config_uses_defaults_when_no_override_file(tmp_path):
    cfg = tripwire.load_config(tmp_path)
    assert cfg["id_field_names"] == tripwire.ID_FIELD_NAMES
    assert cfg["exclude_globs"] == tripwire.DEFAULT_EXCLUDE_GLOBS


def test_load_config_applies_override(tmp_path):
    (tmp_path / ".resolver-priority-tripwire.yaml").write_text(
        'exclude_globs:\n  - "vendor/**"\n', encoding="utf-8"
    )
    cfg = tripwire.load_config(tmp_path)
    assert cfg["exclude_globs"] == ["vendor/**"]


# --------------------------------------------------------------------------
# CLI end-to-end behavior (subprocess, so exit codes are exercised for real)
# --------------------------------------------------------------------------
SCRIPT_PATH = SCRIPT_DIR / "resolver_priority_tripwire.py"


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
    )


def test_cli_all_mode_exits_1_on_unbaselined_error(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "bad.py").write_text(
        (FIXTURES / "fixture1_spawn_utils_pre.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    result = _run_cli("--repo-root", str(repo), "--all")
    assert result.returncode == 1
    assert "ERROR" in result.stdout


def test_cli_all_mode_exits_0_when_only_warnings(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "warn_only.py").write_text(
        (FIXTURES / "fixture3a_provider_manager_pre.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    result = _run_cli("--repo-root", str(repo), "--all")
    # This fixture is type-only (no id field in the same chain) and has no
    # tiebreak vocabulary elsewhere in the standalone file -> WARN, non-blocking.
    assert result.returncode == 0


def test_cli_baseline_suppresses_error_exit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "bad.py").write_text(
        (FIXTURES / "fixture1_spawn_utils_pre.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    findings = tripwire.analyze_file(repo / "bad.py", repo, DEFAULT_CFG)
    assert findings
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps({"baseline": [f.key for f in findings]}), encoding="utf-8"
    )

    result = _run_cli(
        "--repo-root", str(repo), "--all", "--baseline", str(baseline_path)
    )
    assert result.returncode == 0
    assert "BASELINED" in result.stdout


def test_cli_diff_only_scopes_to_changed_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)

    (repo / "clean.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
    subprocess.run(["git", "branch", "-m", "main"], cwd=repo, check=True)

    (repo / "bad.py").write_text(
        (FIXTURES / "fixture1_spawn_utils_pre.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add bad.py"], cwd=repo, check=True)

    result = _run_cli("--repo-root", str(repo), "--diff-only", "--base", "main~1")
    assert result.returncode == 1
    assert "bad.py" in result.stdout


def test_cli_json_out_writes_machine_readable_report(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "bad.py").write_text(
        (FIXTURES / "fixture1_spawn_utils_pre.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    json_path = tmp_path / "report.json"
    _run_cli("--repo-root", str(repo), "--all", "--json-out", str(json_path))
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["summary"]["errors"] >= 1
    assert payload["findings"]
    assert payload["doc_reference"] == tripwire.DOC_REFERENCE


def test_exclude_globs_skip_tests_directory(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_something.py").write_text(
        (FIXTURES / "fixture1_spawn_utils_pre.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    result = _run_cli("--repo-root", str(repo), "--all")
    assert result.returncode == 0
    assert "0 error(s)" in result.stdout
