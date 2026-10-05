"""Large validator results must travel as data, not as a bash -c argument."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
RECIPE = REPO_ROOT / "recipes/validate-bundle-repo.yaml"
SPOOLED = {
    "repo-discovery": "discovery_results",
    "validate-all-bundles": "individual_validation",
    "tool-placement-analysis": "tool_placement_results",
}
POSIX_RECIPE_EXECUTION = pytest.mark.skipif(
    os.name != "posix",
    reason="Executes Bash recipe steps and verifies POSIX file permissions; Windows ACLs are not qualified.",
)


def _render(template: str, context: dict) -> str:
    """Match recipe substitution for strings and JSON-valued placeholders."""
    def replace(match: re.Match) -> str:
        value = context
        for part in match.group(1).split("."):
            value = value[part]
        return value if isinstance(value, str) else json.dumps(value)

    return re.sub(r"\{\{([^{}]+)\}\}", replace, template)


def _steps() -> dict:
    return {step["id"]: step for step in yaml.safe_load(RECIPE.read_text(encoding="utf-8"))["steps"]}


def _run(step: dict, context: dict, cwd: Path, *, scratch: Path | None) -> dict:
    command = _render(step["command"], context)
    # This checks actual argument size, rather than increasing any runner limit.
    assert len(command.encode("utf-8")) < 98_000
    env = dict(os.environ, AMPLIFIER_PYTHON=sys.executable, PYTHONPATH=str(REPO_ROOT))
    env.pop("AMPLIFIER_RECIPE_SCRATCH_DIR", None)
    if scratch is not None:
        env["AMPLIFIER_RECIPE_SCRATCH_DIR"] = str(scratch)
    env.update({name: _render(value, context) for name, value in step.get("env", {}).items()})
    proc = subprocess.run(
        ["bash", "-c", command], cwd=cwd, env=env, text=True,
        capture_output=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    if step["id"] in SPOOLED:
        payload = Path(result["_payload_file"])
        assert stat.S_IMODE(payload.stat().st_mode) == 0o600
        assert json.loads(payload.read_text(encoding="utf-8")) == {
            key: value for key, value in result.items() if key != "_payload_file"
        }
        assert payload.is_relative_to(cwd)
    context[step["output"]] = result
    return result


def _classification_defaults(context: dict, step: dict) -> None:
    for name in re.findall(r"\{\{([^{}]+)\}\}", step["command"]):
        if name not in context:
            context[name] = {
                "passed": True, "skipped": True, "errors": [], "warnings": [],
            }
    context["packaging_check"] = {"passed": True}


@POSIX_RECIPE_EXECUTION
def test_large_payloads_execute_producers_and_classifier_losslessly(tmp_path: Path) -> None:
    steps = _steps()
    repo = tmp_path / "repo"
    behaviors = repo / "behaviors"
    behaviors.mkdir(parents=True)
    scratch = tmp_path / "run-scratch"
    scratch.mkdir(mode=0o700)
    # All three payloads exceed a Linux 128 KiB single-argument ceiling.
    # No network: the individual validator loads local declarations only.
    for number in range(800):
        name = f"fixture-{number:04d}-" + "x" * 70
        (behaviors / f"{name}.yaml").write_text(
            f"bundle:\n  name: {name}\n  version: 1.0.0\n"
            f"  description: Fixture {number}\n"
            f"tools:\n  - module: tool-fixture-{number:04d}\n"
            f"  - module: tool-root-{number:04d}\n",
            encoding="utf-8",
        )
    root_tools = "".join(f"  - module: tool-root-{n:04d}\n" for n in range(800))
    (repo / "bundle.yaml").write_text(
        "bundle:\n  name: fixture\n  version: 1.0.0\n  description: Fixture\n"
        f"tools:\n{root_tools}", encoding="utf-8",
    )
    # An error at the end must survive classification, not disappear in a prefix.
    (behaviors / "zz-broken.yaml").write_text("bundle: [invalid\n", encoding="utf-8")
    context = {
        "repo_path": str(repo),
        "env_check": {"capabilities": {"foundation_available": True},
                      "validation_mode": "full", "summary": {}},
    }
    for step_id, variable in SPOOLED.items():
        result = _run(steps[step_id], context, tmp_path, scratch=scratch)
        assert Path(result["_payload_file"]).stat().st_size > 128 * 1024, variable

    discovery = context["discovery_results"]
    individual = context["individual_validation"]
    tools = context["tool_placement_results"]
    assert discovery["total_count"] == 802
    assert individual["summary"]["total"] == 802
    assert individual["summary"]["failed"] >= 1
    assert len(tools["tool_inventory"]) == 1600
    assert len(tools["suggestions"]) == 800
    _classification_defaults(context, steps["quality-classification"])
    classified = _run(steps["quality-classification"], context, tmp_path, scratch=scratch)
    assert classified["summary"]["total"] == 802
    assert classified["quality_level"] == "critical"
    assert any(bundle["path"].endswith("zz-broken.yaml")
               and bundle["quality"] == "critical" for bundle in classified["bundles"])
    assert len(classified["tool_placement_issues"]) == len(tools["suggestions"])
    assert classified["tool_placement_issues"][-1]["tool"] == tools["suggestions"][-1]["tool"]
    assert classified["tool_placement_issues"][-1]["message"] == tools["suggestions"][-1]["message"]

    # The renderer no longer puts any of the large results in a bash argument.
    legacy_size = len(steps["quality-classification"]["command"].encode()) + sum(
        len(json.dumps(context[name]).encode()) for name in SPOOLED.values()
    )
    assert legacy_size > 128 * 1024


@POSIX_RECIPE_EXECUTION
def test_payload_files_are_unique_in_workspace_without_runtime_scratch(tmp_path: Path) -> None:
    context = {"repo_path": str(tmp_path)}
    step = _steps()["repo-discovery"]
    first = _run(step, context, tmp_path, scratch=None)
    second = _run(step, context, tmp_path, scratch=None)
    assert first["_payload_file"] != second["_payload_file"]
    for result in (first, second):
        parent = Path(result["_payload_file"]).parent
        assert parent.parent == tmp_path
        assert stat.S_IMODE(parent.stat().st_mode) == 0o700


@pytest.mark.parametrize("contents", [None, "{broken json"])
@POSIX_RECIPE_EXECUTION
def test_classifier_refuses_missing_or_corrupt_payloads(tmp_path: Path, contents: str | None) -> None:
    context = {"repo_path": str(tmp_path)}
    steps = _steps()
    discovered = _run(steps["repo-discovery"], context, tmp_path, scratch=tmp_path)
    context["individual_validation"] = {
        "_payload_file": discovered["_payload_file"],
    }
    payload = tmp_path / "unavailable.json"
    if contents is not None:
        payload.write_text(contents)
    context["tool_placement_results"] = {"_payload_file": str(payload)}
    step = steps["quality-classification"]
    _classification_defaults(context, step)
    env = dict(os.environ, AMPLIFIER_PYTHON=sys.executable)
    env.update({name: _render(value, context) for name, value in step["env"].items()})
    proc = subprocess.run(
        ["bash", "-c", _render(step["command"], context)], env=env,
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode != 0
    assert not proc.stdout


def test_every_bash_consumer_of_spooled_outputs_receives_paths_only() -> None:
    for step in _steps().values():
        if step.get("type") != "bash":
            continue
        for variable in SPOOLED.values():
            assert "{{" + variable + "}}" not in step["command"], step["id"]


def test_recipe_static_check_with_legacy_default_encoding(monkeypatch) -> None:
    """Exercise the real UTF-8 recipe under a simulated legacy Windows default."""
    read_text = Path.read_text

    def legacy_read_text(path, encoding=None, errors=None, **kwargs):
        if path == RECIPE and encoding is None:
            encoding = "cp1252"
        return read_text(path, encoding=encoding, errors=errors, **kwargs)

    monkeypatch.setattr(Path, "read_text", legacy_read_text)
    assert set(SPOOLED) <= _steps().keys()
    test_every_bash_consumer_of_spooled_outputs_receives_paths_only()