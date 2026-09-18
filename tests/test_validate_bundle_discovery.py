"""Execution tests for behavior-aware ``validate-bundle`` discovery.

The tests extract and execute the recipes' own bash-step heredocs. They do not
maintain a second discovery implementation that could agree with itself while
the YAML recipe drifts.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from amplifier_foundation.exceptions import BundleLoadError
from amplifier_foundation.registry import BundleRegistry

REPO_ROOT = Path(__file__).parent.parent
MULTI_RECIPE = REPO_ROOT / "recipes" / "validate-bundle.yaml"
SINGLE_RECIPE = REPO_ROOT / "recipes" / "validate-single-bundle.yaml"


def _step_body(recipe: Path, step_id: str) -> str:
    """Extract one bash step's Python heredoc verbatim and de-indent it."""
    lines = recipe.read_text(encoding="utf-8").splitlines()
    step = next(index for index, line in enumerate(lines) if line.strip() == f'- id: "{step_id}"')
    start = next(
        index
        for index in range(step, len(lines))
        if lines[index].rstrip().endswith("<< 'EOF'")
    )
    end = next(index for index in range(start + 1, len(lines)) if lines[index].strip() == "EOF")
    indent = len(lines[start]) - len(lines[start].lstrip())
    return "\n".join(line[indent:] for line in lines[start + 1 : end])


def _run_script(script: str, *, bundle_path: Path) -> subprocess.CompletedProcess[str]:
    """Run a rendered heredoc in a subprocess so recipe exits stay contained."""
    env = dict(os.environ)
    env["VALIDATE_BUNDLE_PATH"] = str(bundle_path)
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _discover(path: Path) -> tuple[subprocess.CompletedProcess[str], dict]:
    """Execute validate-bundle's real discovery step against ``path``."""
    completed = _run_script(_step_body(MULTI_RECIPE, "discover-bundles"), bundle_path=path)
    assert completed.stdout, completed.stderr
    return completed, json.loads(completed.stdout)


def _single_step(step_id: str, bundle_path: Path, repo_root: Path) -> tuple[subprocess.CompletedProcess[str], dict]:
    """Execute an actual validate-single-bundle parser/trace step."""
    script = _step_body(SINGLE_RECIPE, step_id)
    for name, value in {"bundle_path": bundle_path, "repo_root": repo_root}.items():
        script = script.replace(f'"{{{{{name}}}}}"', repr(str(value)))
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.stdout, completed.stderr
    return completed, json.loads(completed.stdout)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _behavior(name: str = "capability") -> str:
    return f"""\
bundle:
  name: {name}
  description: Composable test behavior
hooks:
  - module: example-hook
"""


def test_behavior_only_repo_discovers_direct_behavior_entries_not_docs(tmp_path: Path) -> None:
    """Direct YAML/YML and frontmatter Markdown are behavior entry points; prose is not."""
    repo = tmp_path / "behavior-only"
    _write(repo / "behaviors" / "direct.yaml", _behavior("direct"))
    _write(repo / "behaviors" / "alternate.yml", _behavior("alternate"))
    _write(
        repo / "behaviors" / "markdown.md",
        "---\n" + _behavior("markdown") + "---\nUse the capability.\n",
    )
    _write(repo / "behaviors" / "README.md", "# Documentation\nNot a bundle.\n")
    _write(repo / "behaviors" / "reference.md", "# Reference\nNot a bundle.\n")

    completed, payload = _discover(repo)

    assert completed.returncode == 0, completed.stderr
    assert payload["errors"] == []
    assert payload["total_bundles"] == 3
    assert {entry["bundle_type"] for entry in payload["bundles"]} == {"behavior"}
    assert {entry["bundle_name"] for entry in payload["bundles"]} == {
        "direct",
        "alternate",
        "markdown",
    }
    discovered = {Path(entry["bundle_path"]).name for entry in payload["bundles"]}
    assert "README.md" not in discovered
    assert "reference.md" not in discovered


def test_discovery_reports_each_explicit_conventional_behavior_file(tmp_path: Path) -> None:
    """One-level behavior files are validation targets without directory-alias precedence."""
    repo = tmp_path / "nested-behaviors"
    _write(repo / "behaviors" / "markdown" / "bundle.md", "---\n" + _behavior("markdown") + "---\n")
    _write(repo / "behaviors" / "yaml" / "bundle.yaml", _behavior("yaml"))
    _write(repo / "behaviors" / "yml" / "bundle.yml", _behavior("yml"))
    _write(repo / "behaviors" / "precedence" / "bundle.md", "---\n" + _behavior("md") + "---\n")
    _write(repo / "behaviors" / "precedence" / "bundle.yaml", _behavior("yaml-later"))
    _write(repo / "behaviors" / "precedence" / "notes.md", "# Not an entry point\n")

    completed, payload = _discover(repo)

    assert completed.returncode == 0, completed.stderr
    assert payload["errors"] == []
    paths = {Path(entry["bundle_path"]).relative_to(repo).as_posix() for entry in payload["bundles"]}
    assert paths == {
        "behaviors/markdown/bundle.md",
        "behaviors/yaml/bundle.yaml",
        "behaviors/yml/bundle.yml",
        "behaviors/precedence/bundle.md",
        "behaviors/precedence/bundle.yaml",
    }
    assert payload["total_bundles"] == len(paths)
    assert {entry["bundle_type"] for entry in payload["bundles"]} == {"behavior"}


def test_registry_accepts_explicit_yml_file_but_not_a_yml_only_directory(tmp_path: Path) -> None:
    """The validator reports `.yml` file targets without claiming directory shorthand works."""
    behavior = tmp_path / "repo" / "behaviors" / "only-yml" / "bundle.yml"
    _write(behavior, _behavior("only-yml"))

    loaded = asyncio.run(
        BundleRegistry(home=tmp_path / "registry-file")._load_from_path(behavior)
    )
    assert loaded.name == "only-yml"

    with pytest.raises(BundleLoadError, match="missing bundle.md or bundle.yaml"):
        asyncio.run(
            BundleRegistry(home=tmp_path / "registry-directory")._load_from_path(
                behavior.parent
            )
        )


def test_discovery_keeps_root_standalone_and_behavior_categories_distinct(tmp_path: Path) -> None:
    """A mixed repository routes each real entry point to its proper validator type."""
    repo = tmp_path / "mixed"
    _write(repo / "bundle.md", "---\n" + _behavior("root") + "---\n")
    _write(repo / "bundles" / "complete.yaml", _behavior("complete"))
    _write(repo / "bundles" / "README.md", "# Not a standalone entry\n")
    _write(repo / "behaviors" / "capability.yaml", _behavior("capability"))

    completed, payload = _discover(repo)

    assert completed.returncode == 0, completed.stderr
    assert {(entry["bundle_name"], entry["bundle_type"]) for entry in payload["bundles"]} == {
        ("root", "root"),
        ("complete", "standalone"),
        ("capability", "behavior"),
    }


def test_single_file_under_behaviors_is_classified_as_a_behavior(tmp_path: Path) -> None:
    """Direct invocation retains behavior semantics instead of the generic single-file type."""
    behavior = tmp_path / "repo" / "behaviors" / "capability.yaml"
    _write(behavior, _behavior())

    completed, payload = _discover(behavior)

    assert completed.returncode == 0, completed.stderr
    assert payload["is_single_file"] is True
    assert payload["bundles"] == [
        {
            "bundle_path": str(behavior.resolve()),
            "bundle_type": "behavior",
            "bundle_name": "capability",
            "repo_root": str(behavior.parent.parent),
        }
    ]


def test_empty_repo_fails_before_an_empty_foreach_can_report_success(tmp_path: Path) -> None:
    """No supported artifact is an explicit failure, not a successful empty collection."""
    completed, payload = _discover(tmp_path)

    assert completed.returncode != 0
    assert payload["passed"] is False
    assert payload["bundles"] == []
    assert payload["total_bundles"] == 0
    assert [error["type"] for error in payload["errors"]] == ["no_bundles_discovered"]


def test_behavior_partial_without_session_passes_single_bundle_parser_and_trace(tmp_path: Path) -> None:
    """Behavior partials are valid without a provider or session/orchestrator."""
    repo = tmp_path / "partial"
    behavior = repo / "behaviors" / "capability.yaml"
    _write(
        behavior,
        """\
bundle:
  name: capability
  description: A composable partial with no session configuration
includes:
  - bundle: foundation:behaviors/redaction.yaml
hooks:
  - module: example-hook
""",
    )

    lint_completed, lint = _single_step("yaml-structure-lint", behavior, repo)
    trace_completed, trace = _single_step("trace-dependencies", behavior, repo)

    assert lint_completed.returncode == 0, lint_completed.stderr
    assert lint["passed"] is True
    assert lint["errors"] == []
    assert trace_completed.returncode == 0, trace_completed.stderr
    assert trace["errors"] == []
    assert trace["bundle_name"] == "capability"
    assert trace["external_includes"] == ["foundation:behaviors/redaction.yaml"]


def test_single_bundle_behavior_report_instruction_preserves_host_owned_completeness() -> None:
    """The report contract says behavior composability, not standalone completeness."""
    content = SINGLE_RECIPE.read_text(encoding="utf-8")

    assert "If `{{bundle_type}}` is `behavior`, this is a composable partial." in content
    assert "Do NOT require a provider" in content
    assert "`session.orchestrator`" in content
    assert "Anchors supporting base" in content