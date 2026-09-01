"""Tests for Module Dependency Resolvability Check (validate-bundle-repo.yaml v3.11.0).

Real-world motivation: `amplifier update` refreshes every bundle module with
`uv pip install -e <cache-module-dir> --python <app venv> --quiet --no-sources`.
`--no-sources` disables [tool.uv.sources] entirely, and uv does not treat an
already-installed editable as a resolution candidate. A module pyproject.toml
that depends on a SIBLING in-repo distribution by bare name -- or only via
[tool.uv.sources] -- fails that refresh with "X was not found in the package
registry".

Two real incidents drove this check: amplifier-engram (all 3 modules broke
the updater's refresh on a live host; fixed by PEP 508 direct git
references) and amplifier-browser-bridge modules/tool-browser-bridge (same
latent bare-name + [tool.uv.sources] pattern).

These tests both:
1. Verify the recipe's structural wiring (step exists, correct dependencies,
   quality-classification/synthesize-report integration, changelog/version).
2. Execute the ACTUAL embedded check script (extracted verbatim from the
   recipe YAML, never reimplemented) against on-disk pyproject.toml
   fixtures, so a future edit to the recipe's script is exercised by these
   tests instead of a separately-maintained copy that could silently drift.
"""

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

RECIPE_PATH = Path(__file__).parent.parent / "recipes" / "validate-bundle-repo.yaml"


@pytest.fixture(scope="module")
def recipe_data():
    """Load and parse the recipe YAML once for all tests."""
    if not RECIPE_PATH.exists():
        pytest.skip("Recipe file not found")
    content = RECIPE_PATH.read_text(encoding="utf-8")
    return yaml.safe_load(content), content


@pytest.fixture(scope="module")
def steps_by_id(recipe_data):
    """Build a dict of steps keyed by id for easy lookup."""
    data, _ = recipe_data
    return {step["id"]: step for step in data.get("steps", []) if "id" in step}


@pytest.fixture(scope="module")
def check_script(steps_by_id):
    """Extract the module-dep-resolvability-check step's embedded Python verbatim."""
    step = steps_by_id.get("module-dep-resolvability-check")
    if step is None:
        pytest.skip("module-dep-resolvability-check step not found")
    command = step["command"]
    match = re.search(r"<< 'EOF'\n(.*)\nEOF\n?$", command, re.DOTALL)
    assert match, "Could not extract heredoc body from module-dep-resolvability-check command"
    return match.group(1)


def run_check(check_script: str, repo_dir: Path, published_distributions: str = "") -> dict:
    """Run the extracted check script against repo_dir, returning parsed JSON.

    Substitutes the recipe's {{repo_path}} / {{published_distributions}}
    template placeholders exactly as the recipe executor would, then runs
    the real script as a subprocess (not imported/exec'd in-process, so a
    stray sys.exit() in the script can't take down the test runner).
    """
    script = check_script.replace('"{{repo_path}}"', repr(str(repo_dir)))
    script = script.replace('"""{{published_distributions}}"""', repr(published_distributions))
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"Check script failed (exit {proc.returncode}): {proc.stderr}"
    return json.loads(proc.stdout)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


# ── Structural wiring ────────────────────────────────────────────────────────


class TestStepExistsAndWired:
    def test_step_exists(self, steps_by_id):
        assert "module-dep-resolvability-check" in steps_by_id

    def test_step_is_bash(self, steps_by_id):
        step = steps_by_id["module-dep-resolvability-check"]
        assert step.get("type") == "bash"

    def test_step_output_name(self, steps_by_id):
        step = steps_by_id["module-dep-resolvability-check"]
        assert step.get("output") == "module_dep_resolvability_check"

    def test_step_parse_json(self, steps_by_id):
        step = steps_by_id["module-dep-resolvability-check"]
        assert step.get("parse_json") is True

    def test_step_depends_on_environment_check(self, steps_by_id):
        step = steps_by_id["module-dep-resolvability-check"]
        assert "environment-check" in step.get("depends_on", [])

    def test_step_on_error_continue(self, steps_by_id):
        step = steps_by_id["module-dep-resolvability-check"]
        assert step.get("on_error") == "continue"

    def test_uses_amplifier_python_interpreter(self, steps_by_id):
        """CRITICAL repo lesson (v3.9.0): a heredoc must never use bare python3."""
        step = steps_by_id["module-dep-resolvability-check"]
        assert step["command"].strip().startswith("\"${AMPLIFIER_PYTHON:-python3}\" << 'EOF'")


class TestContextVariable:
    def test_context_has_published_distributions(self, recipe_data):
        data, _ = recipe_data
        assert "published_distributions" in data.get("context", {})

    def test_published_distributions_default_is_empty_string(self, recipe_data):
        data, _ = recipe_data
        assert data["context"].get("published_distributions") == ""


class TestQualityClassificationWiring:
    def test_depends_on_includes_new_step(self, steps_by_id):
        step = steps_by_id["quality-classification"]
        assert "module-dep-resolvability-check" in step.get("depends_on", [])

    def test_quality_classification_parses_new_output(self, recipe_data):
        _, content = recipe_data
        assert "module_dep_resolvability_check" in content
        assert "module_dep_issues" in content

    def test_error_severity_feeds_critical_count(self, recipe_data):
        _, content = recipe_data
        assert 'critical_count += len([i for i in results["module_dep_issues"]' in content


class TestSynthesizeReportWiring:
    def test_includes_module_dependency_section(self, recipe_data):
        _, content = recipe_data
        assert "Module Dependency Resolvability" in content

    def test_synthesize_report_references_output(self, steps_by_id):
        step = steps_by_id["synthesize-report"]
        assert "{{module_dep_resolvability_check}}" in step["prompt"]


class TestVersionAndChangelog:
    def test_version_is_3_11_0(self, recipe_data):
        data, _ = recipe_data
        assert data["version"] == "3.11.0"

    def test_changelog_has_v3_11_0_entry(self, recipe_data):
        _, content = recipe_data
        assert "v3.11.0" in content

    def test_changelog_mentions_engram_incident(self, recipe_data):
        _, content = recipe_data
        assert "amplifier-engram" in content

    def test_changelog_mentions_browser_bridge_incident(self, recipe_data):
        _, content = recipe_data
        assert "amplifier-browser-bridge" in content


# ── Check logic (executes the actual embedded script) ───────────────────────


class TestCheckLogicBareSiblingReference:
    def test_bare_dependency_on_root_package_is_error(self, tmp_path, check_script):
        _write(tmp_path / "pyproject.toml", """
            [project]
            name = "acme-repo"
        """)
        _write(tmp_path / "modules" / "mod-a" / "pyproject.toml", """
            [project]
            name = "acme-module-mod-a"
            dependencies = ["acme-repo>=0.1.0"]

            [build-system]
            build-backend = "hatchling.build"
        """)
        result = run_check(check_script, tmp_path)
        assert result["passed"] is False
        assert result["modules_checked"] == 1
        assert len(result["errors"]) == 1
        error = result["errors"][0]
        assert error["type"] == "module_dep_unresolvable_no_sources"
        assert error["dependency"] == "acme-repo"
        assert error["severity"] == "ERROR"
        assert "not found in the package registry" in error["message"]
        assert "allow-direct-references" in error["fix"]  # hatchling backend

    def test_bare_dependency_on_sibling_module_includes_subdirectory_hint(self, tmp_path, check_script):
        _write(tmp_path / "pyproject.toml", """
            [project]
            name = "acme-repo"
        """)
        _write(tmp_path / "modules" / "mod-a" / "pyproject.toml", """
            [project]
            name = "acme-module-mod-a"
        """)
        _write(tmp_path / "modules" / "mod-b" / "pyproject.toml", """
            [project]
            name = "acme-module-mod-b"
            dependencies = ["acme-module-mod-a"]
        """)
        result = run_check(check_script, tmp_path)
        errors = [e for e in result["errors"] if e["module"] == "mod-b"]
        assert len(errors) == 1
        assert "#subdirectory=modules/mod-a" in errors[0]["fix"]

    def test_uv_sources_entry_provides_no_protection(self, tmp_path, check_script):
        _write(tmp_path / "pyproject.toml", """
            [project]
            name = "acme-repo"
        """)
        _write(tmp_path / "modules" / "mod-a" / "pyproject.toml", """
            [project]
            name = "acme-module-mod-a"
        """)
        _write(tmp_path / "modules" / "mod-b" / "pyproject.toml", """
            [project]
            name = "acme-module-mod-b"
            dependencies = ["acme-module-mod-a"]

            [tool.uv.sources]
            acme-module-mod-a = { path = "../mod-a", editable = true }
        """)
        result = run_check(check_script, tmp_path)
        errors = [e for e in result["errors"] if e["module"] == "mod-b"]
        assert len(errors) == 1
        assert "--no-sources disables" in errors[0]["message"]
        assert "no protection" in errors[0]["message"]

    def test_direct_url_reference_does_not_fire(self, tmp_path, check_script):
        _write(tmp_path / "pyproject.toml", """
            [project]
            name = "acme-repo"
        """)
        _write(tmp_path / "modules" / "mod-a" / "pyproject.toml", """
            [project]
            name = "acme-module-mod-a"
            dependencies = ["acme-repo @ git+https://example.com/acme-repo@main"]
        """)
        result = run_check(check_script, tmp_path)
        assert result["passed"] is True
        assert result["errors"] == []

    def test_unrelated_external_dependency_does_not_fire(self, tmp_path, check_script):
        _write(tmp_path / "pyproject.toml", """
            [project]
            name = "acme-repo"
        """)
        _write(tmp_path / "modules" / "mod-a" / "pyproject.toml", """
            [project]
            name = "acme-module-mod-a"
            dependencies = ["requests>=2.0", "amplifier-core>=1.0.0"]
        """)
        result = run_check(check_script, tmp_path)
        assert result["passed"] is True
        assert result["errors"] == []


class TestPublishedDistributionsEscapeHatch:
    def test_published_distribution_downgrades_to_info(self, tmp_path, check_script):
        _write(tmp_path / "pyproject.toml", """
            [project]
            name = "acme-repo"
        """)
        _write(tmp_path / "modules" / "mod-a" / "pyproject.toml", """
            [project]
            name = "acme-module-mod-a"
            dependencies = ["acme-repo>=0.1.0"]
        """)
        result = run_check(check_script, tmp_path, published_distributions='["acme-repo"]')
        assert result["errors"] == []
        assert len(result["info"]) == 1
        assert result["info"][0]["type"] == "module_dep_bare_name_but_published"
        assert result["info"][0]["severity"] == "INFO"

    def test_unpublished_sibling_still_fires_alongside_published_one(self, tmp_path, check_script):
        _write(tmp_path / "pyproject.toml", """
            [project]
            name = "acme-repo"
        """)
        _write(tmp_path / "modules" / "mod-a" / "pyproject.toml", """
            [project]
            name = "acme-module-mod-a"
        """)
        _write(tmp_path / "modules" / "mod-b" / "pyproject.toml", """
            [project]
            name = "acme-module-mod-b"
            dependencies = ["acme-repo>=0.1.0", "acme-module-mod-a"]
        """)
        result = run_check(check_script, tmp_path, published_distributions='["acme-repo"]')
        assert len(result["errors"]) == 1
        assert result["errors"][0]["dependency"] == "acme-module-mod-a"
        assert len(result["info"]) == 1
        assert result["info"][0]["dependency"] == "acme-repo"


class TestSkipConditions:
    def test_no_modules_dir_skips(self, tmp_path, check_script):
        _write(tmp_path / "pyproject.toml", """
            [project]
            name = "acme-repo"
        """)
        result = run_check(check_script, tmp_path)
        assert result["skipped"] is True
        assert result["passed"] is True

    def test_no_root_pyproject_still_checks_modules(self, tmp_path, check_script):
        _write(tmp_path / "modules" / "mod-a" / "pyproject.toml", """
            [project]
            name = "acme-module-mod-a"
        """)
        _write(tmp_path / "modules" / "mod-b" / "pyproject.toml", """
            [project]
            name = "acme-module-mod-b"
            dependencies = ["acme-module-mod-a"]
        """)
        result = run_check(check_script, tmp_path)
        assert result["skipped"] is False
        assert len(result["errors"]) == 1
        assert result["errors"][0]["dependency"] == "acme-module-mod-a"


class TestOptionalDependencies:
    def test_bare_sibling_in_optional_dependencies_fires(self, tmp_path, check_script):
        _write(tmp_path / "pyproject.toml", """
            [project]
            name = "acme-repo"
        """)
        _write(tmp_path / "modules" / "mod-a" / "pyproject.toml", """
            [project]
            name = "acme-module-mod-a"

            [project.optional-dependencies]
            extra = ["acme-repo>=0.1.0"]
        """)
        result = run_check(check_script, tmp_path)
        assert len(result["errors"]) == 1
        assert result["errors"][0]["field"] == "optional-dependencies.extra"
