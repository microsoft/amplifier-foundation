"""Execute recipe heredocs: flat manifests are roots; behaviors own no runtime."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
RECIPE_DIR = Path(os.environ.get("FLAT_BUNDLE_RECIPE_DIR", REPO_ROOT / "recipes"))


@lru_cache
def _step_body(recipe_name: str, step_id: str) -> str:
    recipe = yaml.safe_load((RECIPE_DIR / recipe_name).read_text(encoding="utf-8"))
    command = next(step["command"] for step in recipe["steps"] if step["id"] == step_id)
    return command.split("<< 'EOF'\n", 1)[1].rsplit("\nEOF", 1)[0]


def _run(
    step_id: str,
    repo_path: Path,
    *,
    bundle_path: Path | None = None,
    bundle_type: str = "behavior",
    without_yaml: bool = False,
) -> tuple[int, dict]:
    recipe = "validate-single-bundle.yaml" if bundle_path else "validate-bundle-repo.yaml"
    body = _step_body(recipe, step_id)
    if bundle_path:
        body = body.replace("{{bundle_path}}", bundle_path.as_posix())
        body = body.replace("{{bundle_type}}", bundle_type)
    if without_yaml:
        # Exercise the recipe's existing optional-PyYAML branch, not a copy of it.
        body = body.replace("import yaml", "raise ImportError('fixture without PyYAML')")
    env = dict(os.environ, VALIDATE_BUNDLE_REPO_PATH=str(repo_path))
    env["AMPLIFIER_RECIPE_SCRATCH_DIR"] = str(repo_path)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(REPO_ROOT), env.get("PYTHONPATH")]))
    proc = subprocess.run(
        [sys.executable, "-c", body], capture_output=True, text=True, env=env, timeout=30
    )
    assert proc.stdout, proc.stderr
    return proc.returncode, json.loads(proc.stdout)


def _write(path: Path, data: dict, body: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = yaml.safe_dump(data, sort_keys=False)
    if path.suffix == ".md":
        content = f"---\n{content}---\n{body}"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.parametrize("suffix", [".yaml", ".yml", ".md"])
@pytest.mark.parametrize("field", ["orchestrator", "context"])
@pytest.mark.parametrize("value", [{"module": "fixture-runtime"}, None])
def test_behaviors_reject_both_runtime_selectors(
    tmp_path: Path, suffix: str, field: str, value: dict | None
) -> None:
    bundle_path = _write(
        tmp_path / "behaviors" / f"fixture{suffix}",
        {"bundle": {"name": "fixture"}, "session": {field: value}},
    )
    code, repo = _run("behavior-hygiene-validation", tmp_path)
    assert code == 0
    assert repo["behaviors_checked"] == 1
    assert repo["passed"] is False
    assert [error["type"] for error in repo["errors"]] == [f"has_session_{field}"]
    code, single = _run("yaml-structure-lint", tmp_path, bundle_path=bundle_path)
    assert code == 1
    assert single["passed"] is False
    assert [error["type"] for error in single["errors"]] == [f"has_session_{field}"]


@pytest.mark.parametrize("suffix", [".yaml", ".md"])
def test_context_awareness_is_not_a_runtime_selector(tmp_path: Path, suffix: str) -> None:
    _write(tmp_path / "behaviors" / f"fixture{suffix}", {
        "bundle": {"name": "fixture"},
        "context": {"include": ["context/awareness.md"]},
    })
    (tmp_path / "context").mkdir()
    (tmp_path / "context/awareness.md").write_text("You have fixture tools.\n")
    for without_yaml in (False, True):
        code, result = _run("behavior-hygiene-validation", tmp_path, without_yaml=without_yaml)
        assert code == 0
        if without_yaml:
            assert result["passed"] is False
            assert result["unverified"] is True
            assert result["errors"][0]["type"] == "yaml_parser_unavailable"
        else:
            assert result["passed"] is True
            assert result["errors"] == []


@pytest.mark.parametrize("suffix", [".yaml", ".md"])
def test_missing_yaml_parser_never_certifies_behaviors(tmp_path: Path, suffix: str) -> None:
    bundle_path = _write(tmp_path / "behaviors" / f"fixture{suffix}", {
        "bundle": {"name": "fixture"},
        "session": {
            "orchestrator": {"module": "fixture-loop"},
            "context": {"module": "fixture-context"},
        },
    })
    code, result = _run("behavior-hygiene-validation", tmp_path, without_yaml=True)
    assert code == 0
    assert result["passed"] is False
    assert result["unverified"] is True
    assert {error["type"] for error in result["errors"]} == {
        "yaml_parser_unavailable"
    }
    code, single = _run(
        "yaml-structure-lint", tmp_path, bundle_path=bundle_path, without_yaml=True
    )
    assert code == 1
    assert single["passed"] is False
    assert single["unverified"] is True
    assert single["errors"][0]["type"] == "yaml_parser_unavailable"


@pytest.mark.parametrize("suffix", [".yaml", ".md"])
def test_flow_style_runtime_is_unverified_without_yaml(tmp_path: Path, suffix: str) -> None:
    bundle_path = tmp_path / "behaviors" / f"fixture{suffix}"
    bundle_path.parent.mkdir()
    content = "bundle: {name: fixture}\nsession: {context: null, orchestrator: null}\n"
    if suffix == ".md":
        content = f"---\n{content}---\n"
    bundle_path.write_text(content, encoding="utf-8")
    for without_yaml in (False, True):
        code, result = _run(
            "behavior-hygiene-validation", tmp_path, without_yaml=without_yaml
        )
        assert code == 0
        assert result["passed"] is False
        if without_yaml:
            assert result["unverified"] is True
            assert result["errors"][0]["type"] == "yaml_parser_unavailable"
        else:
            assert {error["type"] for error in result["errors"]} == {
                "has_session_orchestrator", "has_session_context"
            }
    code, single = _run(
        "yaml-structure-lint", tmp_path, bundle_path=bundle_path, without_yaml=True
    )
    assert code == 1
    assert single["passed"] is False
    assert single["unverified"] is True
    assert single["errors"][0]["type"] == "yaml_parser_unavailable"


@pytest.mark.parametrize("bundle_type", ["root", "standalone"])
@pytest.mark.parametrize("suffix", [".yaml", ".md"])
def test_complete_hosts_can_choose_runtime_defaults(
    tmp_path: Path, bundle_type: str, suffix: str
) -> None:
    path = _write(tmp_path / "bundles" / f"fixture{suffix}", {
        "bundle": {"name": "fixture"},
        "session": {
            "orchestrator": {"module": "fixture-loop"},
            "context": {"module": "fixture-context"},
        },
    })
    code, result = _run(
        "yaml-structure-lint", tmp_path, bundle_path=path, bundle_type=bundle_type
    )
    assert code == 0
    assert result["passed"] is True


def test_flat_and_legacy_roots_are_checked_without_context_assets(tmp_path: Path) -> None:
    roots = {
        "bundles/anchors.md",
        "bundles/anchors-amp-dev.md",
        "bundles/group/deeper/complete.md",
        "bundles/anchors/bundle.md",
    }
    for relative in roots:
        _write(
            tmp_path / relative,
            {"bundle": {"name": Path(relative).stem}},
            "This bundle provides documented capabilities.\n",
        )
    _write(
        tmp_path / "bundles/anchors/context/asset.md",
        {"title": "Context, not a bundle"},
        "This document explains a capability.\n@fixture:context/unreferenced.md\n",
    )
    _write(tmp_path / "bundles/README.md", {"title": "Documentation"}, "Documentation.\n")
    (tmp_path / "context").mkdir()
    (tmp_path / "context/unreferenced.md").write_text("Not loaded by any root.\n")

    code, discovery = _run("repo-discovery", tmp_path)
    assert code == 0
    found = {Path(p).relative_to(tmp_path).as_posix()
             for p in discovery["bundles_found"]["standalone"]}
    assert found == roots
    assert discovery["summary"]["standalone_count"] == len(roots)

    code, head = _run("bundle-head-cost", tmp_path)
    assert code == 0
    assert {d["file"] for d in head["bundle_details"]} == roots
    code, body = _run("body-instruction-check", tmp_path)
    assert code == 0
    assert not body.get("skipped"), body
    assert body["files_checked"] == len(roots)
    assert {warning["file"] for warning in body["warnings"]} == roots
    code, awareness = _run("awareness-redundancy-check", tmp_path)
    assert code == 0
    assert awareness["context_files_checked"] == 0


def test_nested_behavior_manifest_still_rejects_runtime(tmp_path: Path) -> None:
    _write(tmp_path / "behaviors/fixture/bundle.md", {
        "bundle": {"name": "fixture"},
        "session": {"context": {"module": "fixture-context"}},
    })
    _, result = _run("behavior-hygiene-validation", tmp_path)
    assert result["behaviors_checked"] == 1
    assert result["passed"] is False
    assert result["errors"][0]["type"] == "has_session_context"


@pytest.mark.parametrize("namespace", ["fixture", "foundation"])
def test_flat_root_cost_and_awareness_honor_namespace_root(
    tmp_path: Path, namespace: str
) -> None:
    root_body = f"@{namespace}:context/rules.md\n"
    own_rules = "You must verify changes before declaring completion.\n"
    repo_rules = "You read the shared repository rules, not the fixture namespace rules.\n"
    rules = own_rules if namespace == "fixture" else repo_rules
    description = "Inspect the fixture repository."
    _write(
        tmp_path / "bundles/fixture.md",
        {"bundle": {"name": "fixture", "namespace_root": "fixture"}},
        root_body,
    )
    asset_dir = tmp_path / "bundles/fixture"
    (asset_dir / "context").mkdir(parents=True)
    (asset_dir / "context/rules.md").write_text(own_rules, encoding="utf-8")
    (tmp_path / "context").mkdir()
    (tmp_path / "context/rules.md").write_text(repo_rules, encoding="utf-8")
    _write(
        asset_dir / "agents/explorer.md",
        {"meta": {"name": "explorer", "description": description}},
        "You inspect the fixture repository.\n",
    )
    _, head = _run("bundle-head-cost", tmp_path)
    assert head["bundles_measured"] == 1
    detail = head["bundle_details"][0]
    assert detail["context_chars"] == len(root_body.strip()) + len(rules)
    assert detail["agent_description_chars"] == len(description)
    assert detail["agents_counted"] == 1
    assert detail["is_lower_bound"] is False
    _, awareness = _run("awareness-redundancy-check", tmp_path)
    assert awareness["context_files_checked"] == 1
    expected_file = ("bundles/fixture/context/rules.md"
                     if namespace == "fixture" else "context/rules.md")
    assert awareness["file_details"][0]["file"] == expected_file
    assert awareness["file_details"][0]["referenced_by"] == ["bundles/fixture.md"]


@pytest.mark.parametrize("includes", [None, [], ["fixture:included"], ["included"]])
def test_head_cost_counts_only_explicit_local_agents(
    tmp_path: Path, includes: list[str] | None
) -> None:
    data = {"bundle": {"name": "fixture", "namespace_root": ".."}}
    if includes is not None:
        data["agents"] = {"include": includes}
    _write(tmp_path / "behaviors/fixture.yaml", data)
    descriptions = {
        "included": "Inspect the requested fixture.",
        "unrelated": "An unrelated agent that must not inflate this behavior's cost.",
    }
    for name, description in descriptions.items():
        _write(
            tmp_path / f"agents/{name}.md",
            {"meta": {"name": name, "description": description}},
            "You inspect the repository.\n",
        )
    _, head = _run("bundle-head-cost", tmp_path)
    assert head["bundles_measured"] == 1
    detail = head["bundle_details"][0]
    expected_names = list(descriptions) if includes is None else (
        ["included"] if includes else []
    )
    assert detail["agents_counted"] == len(expected_names)
    assert detail["agent_description_chars"] == sum(
        len(descriptions[name]) for name in expected_names
    )
    assert detail["is_lower_bound"] is False