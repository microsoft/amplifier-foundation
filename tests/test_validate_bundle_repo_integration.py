"""Integration tests for validate-bundle-repo.yaml v3.4.0 changes.

Verifies:
- Valid YAML structure
- Presence and correctness of new context variables
- Presence and correctness of new steps (validate-all-flags, validate-recipes,
  set-default-recipe-validation)
- quality-classification depends_on includes new steps
- synthesize-report prompt includes Recipe Validation block
- version metadata is up-to-date
"""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

RECIPE_PATH = Path(__file__).parent.parent / "recipes" / "validate-bundle-repo.yaml"


def _parse_header_version(content: str) -> str:
    """Extract version from recipe header comment.

    Header convention: '# Repository-Wide Bundle Validator Recipe vX.Y.Z'
    """
    match = re.search(r"Recipe v(\d+\.\d+\.\d+)", content)
    if not match:
        raise ValueError(f"Header version not found in: {content.splitlines()[0]}")
    return match.group(1)


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


# ── YAML validity ─────────────────────────────────────────────────────────────


class TestYAMLValidity:
    def test_recipe_is_valid_yaml(self, recipe_data):
        """Recipe must parse as a valid YAML dict."""
        data, _ = recipe_data
        assert isinstance(data, dict), "Top-level must be a dict"

    def test_recipe_has_name(self, recipe_data):
        """Recipe must have a name field."""
        data, _ = recipe_data
        assert "name" in data

    def test_recipe_has_version(self, recipe_data):
        """Recipe must have a version field."""
        data, _ = recipe_data
        assert "version" in data

    def test_recipe_has_steps(self, recipe_data):
        """Recipe must have a non-empty steps list."""
        data, _ = recipe_data
        assert isinstance(data.get("steps"), list)
        assert len(data["steps"]) > 0


# ── Version metadata ──────────────────────────────────────────────────────────


class TestVersionMetadata:
    def test_version_field_matches_header_version(self, recipe_data):
        """YAML version field and header comment must always agree.

        Detects the exact bug that broke CI: bumping the header comment
        without also updating the YAML version: field (or vice versa).
        """
        data, content = recipe_data
        header_version = _parse_header_version(content)
        yaml_version = data["version"]
        assert header_version == yaml_version, (
            f"Header version '{header_version}' does not match YAML version "
            f"field '{yaml_version}'. Both must be updated on every recipe bump."
        )

    def test_current_version_in_changelog(self, recipe_data):
        """Current version must have a changelog entry.

        Detects a bump where the version field was updated but no changelog
        entry was written for the new version.
        """
        data, content = recipe_data
        version = data["version"]
        assert f"v{version}" in content, (
            f"Recipe is at version '{version}' but no 'v{version}' entry "
            f"found in CHANGELOG. Bump the version field OR add a changelog entry."
        )

    def test_header_format_is_canonical(self, recipe_data):
        """Header comment must follow the canonical format '# ... Recipe vX.Y.Z'.

        Ensures the version is machine-parseable for version-agnostic tests.
        """
        _, content = recipe_data
        header_lines = content.split("\n")[:5]
        header = "\n".join(header_lines)
        assert re.search(r"^# .* Recipe v\d+\.\d+\.\d+$", header, re.MULTILINE), (
            f"Header must contain a line matching '# ... Recipe vX.Y.Z'. Found:\n{header}"
        )

    def test_changelog_still_has_v3_4_0_entry(self, recipe_data):
        """Changelog must retain the historical v3.4.0 entry."""
        _, content = recipe_data
        assert "v3.4.0" in content


# ── New context variables ─────────────────────────────────────────────────────


class TestNewContextVariables:
    def test_context_has_validate_recipes(self, recipe_data):
        """Context must include validate_recipes variable."""
        data, _ = recipe_data
        ctx = data.get("context", {})
        assert "validate_recipes" in ctx, (
            "Context must have validate_recipes variable (opt-in recipe validation flag)"
        )

    def test_validate_recipes_default_is_false(self, recipe_data):
        """validate_recipes default must be 'false'."""
        data, _ = recipe_data
        ctx = data.get("context", {})
        assert ctx.get("validate_recipes") == "false"

    def test_context_has_validate_all(self, recipe_data):
        """Context must include validate_all convenience flag."""
        data, _ = recipe_data
        ctx = data.get("context", {})
        assert "validate_all" in ctx, "Context must have validate_all convenience flag"

    def test_validate_all_default_is_false(self, recipe_data):
        """validate_all default must be 'false'."""
        data, _ = recipe_data
        ctx = data.get("context", {})
        assert ctx.get("validate_all") == "false"

    def test_context_has_recipes_dir(self, recipe_data):
        """Context must include recipes_dir variable."""
        data, _ = recipe_data
        ctx = data.get("context", {})
        assert "recipes_dir" in ctx, (
            "Context must have recipes_dir for sub-recipe invocation"
        )

    def test_recipes_dir_default_is_recipes(self, recipe_data):
        """recipes_dir default must be 'recipes'."""
        data, _ = recipe_data
        ctx = data.get("context", {})
        assert ctx.get("recipes_dir") == "recipes"

    def test_context_has_known_agents(self, recipe_data):
        """Context must include known_agents variable."""
        data, _ = recipe_data
        ctx = data.get("context", {})
        assert "known_agents" in ctx, (
            "Context must have known_agents for recipe validation"
        )

    def test_known_agents_default_is_empty_string(self, recipe_data):
        """known_agents default must be empty string."""
        data, _ = recipe_data
        ctx = data.get("context", {})
        # Default is '' (empty string) - YAML may load as None or ''
        val = ctx.get("known_agents")
        assert val == "" or val is None, (
            f"known_agents default should be '' or None, got {val!r}"
        )


# ── validate-all-flags step ───────────────────────────────────────────────────


class TestValidateAllFlagsStep:
    def test_validate_all_flags_step_exists(self, steps_by_id):
        """validate-all-flags step must be present."""
        assert "validate-all-flags" in steps_by_id, (
            "Step 'validate-all-flags' not found in recipe steps"
        )

    def test_validate_all_flags_is_bash(self, steps_by_id):
        """validate-all-flags must be a bash step."""
        step = steps_by_id.get("validate-all-flags", {})
        assert step.get("type") == "bash"

    def test_validate_all_flags_output_is_validation_flags(self, steps_by_id):
        """validate-all-flags output must be 'validation_flags'."""
        step = steps_by_id.get("validate-all-flags", {})
        assert step.get("output") == "validation_flags"

    def test_validate_all_flags_parse_json(self, steps_by_id):
        """validate-all-flags must have parse_json: true."""
        step = steps_by_id.get("validate-all-flags", {})
        assert step.get("parse_json") is True

    def test_validate_all_flags_depends_on_environment_check(self, steps_by_id):
        """validate-all-flags must depend on environment-check."""
        step = steps_by_id.get("validate-all-flags", {})
        depends = step.get("depends_on", [])
        assert "environment-check" in depends


# ── validate-recipes step ─────────────────────────────────────────────────────


class TestValidateRecipesStep:
    def test_validate_recipes_step_exists(self, steps_by_id):
        """validate-recipes step must be present."""
        assert "validate-recipes" in steps_by_id, (
            "Step 'validate-recipes' not found in recipe steps"
        )

    def test_validate_recipes_is_recipe_type(self, steps_by_id):
        """validate-recipes must be type 'recipe'."""
        step = steps_by_id.get("validate-recipes", {})
        assert step.get("type") == "recipe"

    def test_validate_recipes_has_condition(self, steps_by_id):
        """validate-recipes must have a condition for opt-in behavior."""
        step = steps_by_id.get("validate-recipes", {})
        assert "condition" in step, "validate-recipes must have a condition (opt-in)"
        assert "validate_recipes" in step["condition"]

    def test_validate_recipes_on_error_continue(self, steps_by_id):
        """validate-recipes must have on_error: continue."""
        step = steps_by_id.get("validate-recipes", {})
        assert step.get("on_error") == "continue"

    def test_validate_recipes_output_is_recipe_validation(self, steps_by_id):
        """validate-recipes output must be 'recipe_validation'."""
        step = steps_by_id.get("validate-recipes", {})
        assert step.get("output") == "recipe_validation"

    def test_validate_recipes_depends_on_repo_discovery(self, steps_by_id):
        """validate-recipes must depend on repo-discovery."""
        step = steps_by_id.get("validate-recipes", {})
        depends = step.get("depends_on", [])
        assert "repo-discovery" in depends

    def test_validate_recipes_depends_on_validate_all_flags(self, steps_by_id):
        """validate-recipes must depend on validate-all-flags.

        This is required because the condition reads validation_flags.validate_recipes,
        which is the output of validate-all-flags. Without this dependency, the recipe
        engine could evaluate the condition before validate-all-flags completes.
        """
        step = steps_by_id.get("validate-recipes", {})
        depends = step.get("depends_on", [])
        assert "validate-all-flags" in depends, (
            "validate-recipes depends_on is missing 'validate-all-flags'. "
            "The condition {{validation_flags.validate_recipes}} consumes the output "
            "of validate-all-flags; the dependency must be explicit."
        )

    def test_validate_recipes_context_passes_repo_path(self, steps_by_id):
        """validate-recipes context must pass repo_path."""
        step = steps_by_id.get("validate-recipes", {})
        ctx = step.get("context", {})
        assert "repo_path" in ctx

    def test_validate_recipes_context_passes_recipes_dir(self, steps_by_id):
        """validate-recipes context must pass recipes_dir."""
        step = steps_by_id.get("validate-recipes", {})
        ctx = step.get("context", {})
        assert "recipes_dir" in ctx

    def test_validate_recipes_context_passes_known_agents(self, steps_by_id):
        """validate-recipes context must pass known_agents."""
        step = steps_by_id.get("validate-recipes", {})
        ctx = step.get("context", {})
        assert "known_agents" in ctx


# ── set-default-recipe-validation step ───────────────────────────────────────


class TestSetDefaultRecipeValidationStep:
    def test_set_default_step_exists(self, steps_by_id):
        """set-default-recipe-validation step must be present."""
        assert "set-default-recipe-validation" in steps_by_id, (
            "Step 'set-default-recipe-validation' not found in recipe steps"
        )

    def test_set_default_is_bash(self, steps_by_id):
        """set-default-recipe-validation must be a bash step."""
        step = steps_by_id.get("set-default-recipe-validation", {})
        assert step.get("type") == "bash"

    def test_set_default_output_is_recipe_validation(self, steps_by_id):
        """set-default-recipe-validation output must be 'recipe_validation'."""
        step = steps_by_id.get("set-default-recipe-validation", {})
        assert step.get("output") == "recipe_validation"

    def test_set_default_parse_json(self, steps_by_id):
        """set-default-recipe-validation must have parse_json: true."""
        step = steps_by_id.get("set-default-recipe-validation", {})
        assert step.get("parse_json") is True

    def test_set_default_depends_on_validate_all_flags(self, steps_by_id):
        """set-default-recipe-validation must depend on validate-all-flags."""
        step = steps_by_id.get("set-default-recipe-validation", {})
        depends = step.get("depends_on", [])
        assert "validate-all-flags" in depends


# ── quality-classification depends_on ────────────────────────────────────────


class TestQualityClassificationDependsOn:
    def test_quality_classification_exists(self, steps_by_id):
        """quality-classification step must be present."""
        assert "quality-classification" in steps_by_id

    def test_quality_classification_depends_on_validate_recipes(self, steps_by_id):
        """quality-classification must depend on validate-recipes."""
        step = steps_by_id.get("quality-classification", {})
        depends = step.get("depends_on", [])
        assert "validate-recipes" in depends, (
            "quality-classification depends_on must include 'validate-recipes'"
        )

    def test_quality_classification_depends_on_set_default_recipe_validation(
        self, steps_by_id
    ):
        """quality-classification must depend on set-default-recipe-validation."""
        step = steps_by_id.get("quality-classification", {})
        depends = step.get("depends_on", [])
        assert "set-default-recipe-validation" in depends, (
            "quality-classification depends_on must include 'set-default-recipe-validation'"
        )


# ── synthesize-report prompt ──────────────────────────────────────────────────


class TestSynthesizeReportPrompt:
    def test_synthesize_report_exists(self, steps_by_id):
        """synthesize-report step must be present."""
        assert "synthesize-report" in steps_by_id

    def test_synthesize_report_includes_recipe_validation_block(self, recipe_data):
        """synthesize-report prompt must include Recipe Validation section."""
        _, content = recipe_data
        assert "Recipe Validation" in content, (
            "synthesize-report prompt must include a Recipe Validation section"
        )

    def test_synthesize_report_depends_on_set_default_recipe_validation(
        self, steps_by_id
    ):
        """synthesize-report must depend on set-default-recipe-validation."""
        step = steps_by_id.get("synthesize-report", {})
        depends = step.get("depends_on", [])
        assert "set-default-recipe-validation" in depends, (
            "synthesize-report depends_on must include 'set-default-recipe-validation'"
        )


def _render(text: str, values: dict) -> str:
    """Template transport only; no checker logic is replicated here."""

    def value(match: re.Match) -> str:
        data = values[match[1]]
        return data if isinstance(data, str) else json.dumps(data)

    return re.sub(r"\{\{([^}]+)\}\}", value, text)


def _execute_step(step: dict, values: dict, cwd: Path) -> dict:
    env = dict(
        os.environ,
        AMPLIFIER_PYTHON=sys.executable,
        AMPLIFIER_RECIPE_SCRATCH_DIR=str(cwd),
    )
    env.update({name: _render(v, values) for name, v in step.get("env", {}).items()})
    proc = subprocess.run(
        ["bash", "-c", _render(step["command"], values)],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _classification(steps: dict, tmp_path: Path, **overrides) -> dict:
    step = steps["quality-classification"]
    values: dict[str, object] = {
        name: {}
        for name in re.findall(
            r"\{\{([^}]+)\}\}", step["command"] + json.dumps(step.get("env", {}))
        )
    }
    for name, payload in {
        "discovery_results": {"repo_structure": {"has_behaviors": True}},
        "individual_validation": {
            "bundles": [
                {
                    "name": "fixture",
                    "category": "behavior",
                    "errors": [],
                    "warnings": [],
                }
            ]
        },
        "tool_placement_results": {},
    }.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        values[f"{name}._payload_file"] = str(path)
    values["recipe_validation"] = _execute_step(
        steps["set-default-recipe-validation"], {}, tmp_path
    )
    values["validation_flags"] = {
        "validate_all": "false",
        "validate_agents": "false",
        "validate_recipes": "false",
    }
    values["build_check"] = _execute_step(
        steps["set-default-build-check"], {}, tmp_path
    )
    values.update(overrides)
    values["enhance_diagrams"] = overrides.get("enhance_diagrams", "false")
    build = values["build_check"]
    assert isinstance(build, dict)
    values["build_check._payload_file"] = build.get(
        "_payload_file", str(tmp_path / "missing-build-output.json")
    )
    return _execute_step(step, values, tmp_path)


def test_report_version_and_scope(recipe_data, steps_by_id, tmp_path: Path) -> None:
    data, content = recipe_data
    assert data["version"] == data["context"]["validator_version"] == "3.17.3"
    assert 'version: &validator_version "3.17.3"' in content
    assert "validator_version: *validator_version" in content
    for step_id in ("quality-classification", "synthesize-report"):
        assert "validate-all-flags" in steps_by_id[step_id]["depends_on"]
    env_check = _execute_step(
        steps_by_id["environment-check"],
        {"validator_version": "caller-declared"},
        tmp_path,
    )
    assert env_check["validator_version"] == "caller-declared"
    assert "all checks enabled" not in env_check["mode_description"]
    assert env_check["capabilities"]["foundation_available"]
    flags = _execute_step(
        steps_by_id["validate-all-flags"],
        {
            "validate_all": "true",
            "validate_agents": "false",
            "validate_recipes": "false",
        },
        tmp_path,
    )
    assert flags["validate_agents"] == flags["validate_recipes"] == "true"
    assert flags["requested_flags"]["validate_recipes"] == "false"
    disabled = _execute_step(steps_by_id["set-default-recipe-validation"], {}, tmp_path)
    assert disabled["performed"] is False
    assert disabled["skipped"] is True
    assert disabled["skip_reason"] == "disabled_by_flag"
    result = _classification(steps_by_id, tmp_path, validation_flags=flags)
    summary = result["recipe_validation_summary"]
    assert summary["skipped"] is True and summary["performed"] is False
    assert summary["skip_reason"] == "disabled_by_flag"
    assert summary["recipes_validated"] == 0
    assert summary["validation_flags"] == flags
    assert result["validation_flags"] == flags
    envelope, doc = _report_fixture()
    envelope["quality_classification"]["validator_version"] = "caller-declared"
    envelope["inputs"]["env_check"]["validator_version"] = "caller-declared"
    published = _publish_report(
        steps_by_id["synthesize-report"], tmp_path, envelope, doc
    )
    assert published.returncode == 0, published.stderr
    assert "# Bundle Repository Validation Report vcaller-declared" in published.stdout
    assert "caller-overridable" in published.stdout


@pytest.mark.parametrize("nested", [False, True])
def test_recipe_subresult_skip_is_not_inferred_from_good(
    steps_by_id, tmp_path: Path, nested: bool
) -> None:
    payload = {
        "quality_level": "good",
        "skipped": True,
        "skip_reason": "no_recipes",
        "recipes_validated": 0,
    }
    result = _classification(
        steps_by_id,
        tmp_path,
        recipe_validation={"quality_classification": payload} if nested else payload,
    )
    summary = result["recipe_validation_summary"]
    assert summary["skipped"] is True and summary["performed"] is False
    assert summary["skip_reason"] == "no_recipes"
    assert summary["recipes_validated"] == 0


@pytest.mark.parametrize(
    "payload,performed,count",
    [
        ({"quality_level": "good"}, False, 0),
        (
            {
                "quality_classification": {
                    "quality_level": "good",
                    "summary": {"total": 2},
                    "recipes": [{}, {}],
                }
            },
            True,
            2,
        ),
    ],
)
def test_recipe_execution_requires_result_evidence(
    steps_by_id, tmp_path: Path, payload: dict, performed: bool, count: int
) -> None:
    result = _classification(steps_by_id, tmp_path, recipe_validation=payload)
    summary = result["recipe_validation_summary"]
    assert summary["performed"] is performed
    assert summary["skipped"] is not performed
    assert summary["recipes_validated"] == count
    if not performed:
        assert summary["skip_reason"] == "output_unavailable"


def test_full_mode_requires_build_probe(
    steps_by_id, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Importable hatchling alone cannot certify the pip-wheel prerequisite."""
    (tmp_path / "hatchling.py").write_text("# Import-only prerequisite fixture.\n")
    (tmp_path / "pip.py").write_text("raise SystemExit(1)\n")
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join([str(tmp_path), os.environ.get("PYTHONPATH", "")])
    )
    result = _execute_step(
        steps_by_id["environment-check"],
        {"validator_version": "build-probe-fixture"},
        tmp_path,
    )
    assert result["capabilities"]["foundation_available"]
    assert result["capabilities"]["hatchling_available"]
    assert result["capabilities"]["pip_wheel_available"] is False
    assert result["validation_mode"] == "full_no_build"
    assert result["summary"]["full_validation_available"] is False
    assert result["summary"]["can_validate_builds"] is False


def _actual_build(
    steps: dict,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    details: str = "",
    returncode: int = 1,
    capabilities: dict | None = None,
    has_pyproject: bool = True,
) -> dict:
    """Run the real build command; only the external pip process is controlled."""
    (root / "pip.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "Path('build-attempted').write_text('yes')\n"
        f"sys.stderr.write({details!r})\nraise SystemExit({returncode})\n",
        encoding="utf-8",
    )
    if has_pyproject:
        (root / "pyproject.toml").write_text("[project]\nname='controlled-build'\n")
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join([str(root), os.environ.get("PYTHONPATH", "")])
    )
    return _execute_step(
        steps["build-check"],
        {
            "repo_path": str(root),
            "env_check": {
                "capabilities": capabilities
                if capabilities is not None
                else {
                    "hatchling_available": True,
                    "pip_wheel_available": True,
                }
            },
        },
        root,
    )


@pytest.mark.parametrize(
    "details",
    [
        "controlled package build failure",
        "controlled package build failure\nsecond line\n",
        "compiler failed at C:\\build\\new\\test\nquoted 'detail' and 雪\n",
        "long diagnostic\n" + "x" * 700 + "\nC:\\build\\last\n",
    ],
)
def test_actual_failed_build_preserves_error_and_execution(
    steps_by_id, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, details: str
) -> None:
    build = _actual_build(steps_by_id, tmp_path, monkeypatch, details=details)
    assert (tmp_path / "build-attempted").read_text() == "yes"
    assert build["passed"] is False
    assert build["build_tested"] is False  # existing success-only semantics retained
    assert build["errors"][0]["details"] == details
    result = _classification(steps_by_id, tmp_path, build_check=build)
    assert result["quality_level"] == "critical"
    issue = result["packaging_issues"][0]
    assert issue["message"] == build["errors"][0]["message"] == "Package build failed"
    assert issue["details"] == details
    assert issue["severity"] == "critical"
    assert result["checks_performed"]["build_check"]["performed"] is True
    assert result["checks_performed"]["build_check"]["skipped"] is False
    assert build["performed"] is True
    payload = Path(build["_payload_file"])
    assert payload.is_relative_to(tmp_path)
    assert payload.stat().st_mode & 0o777 == 0o600
    stored = json.loads(payload.read_text(encoding="utf-8"))
    assert stored["errors"][0]["details"] == details


@pytest.mark.parametrize("case", ["success", "no_pyproject", "no_hatchling", "no_pip"])
def test_actual_build_success_and_skip_controls(
    steps_by_id, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    capabilities = {
        "hatchling_available": case != "no_hatchling",
        "pip_wheel_available": case != "no_pip",
    }
    build = _actual_build(
        steps_by_id,
        tmp_path,
        monkeypatch,
        returncode=0,
        capabilities=capabilities,
        has_pyproject=case != "no_pyproject",
    )
    assert build["passed"] is True
    result = _classification(steps_by_id, tmp_path, build_check=build)
    execution = result["checks_performed"]["build_check"]
    assert execution["performed"] is (case == "success")
    assert build["performed"] is (case == "success")
    if case == "success":
        assert build["build_success"] is True
        assert build["build_tested"] is True
    else:
        assert not (tmp_path / "build-attempted").exists()
        assert execution["skipped"] is True
        assert execution["skip_reason"] == build["reason"] == build["skip_reason"]


@pytest.mark.parametrize("payload", [{}, {"_payload_file": "absent.json"}])
def test_missing_build_output_is_unverified_not_pass(
    steps_by_id, tmp_path: Path, payload: dict
) -> None:
    result = _classification(steps_by_id, tmp_path, build_check=payload)
    execution = result["checks_performed"]["build_check"]
    assert execution["performed"] is False and execution["skipped"] is True
    assert execution["skip_reason"] == "output_unavailable"
    assert execution["unverified"] is True
    assert result["quality_level"] == "critical"
    assert result["packaging_issues"][0]["type"] == "build_check_unverified"


@pytest.mark.parametrize("contents", ["{broken json", "null", "[]", "{}"])
def test_malformed_build_payload_is_unverified(
    steps_by_id, tmp_path: Path, contents: str
) -> None:
    target = tmp_path / "malformed-build.json"
    target.write_text(contents, encoding="utf-8")
    result = _classification(
        steps_by_id, tmp_path, build_check={"_payload_file": str(target)}
    )
    assert result["quality_level"] == "critical"
    execution = result["checks_performed"]["build_check"]
    assert execution["unverified"] is True
    assert execution["skip_reason"] == "output_unavailable"


def test_unparseable_hygiene_output_has_skip_reason(
    steps_by_id, tmp_path: Path
) -> None:
    result = _classification(steps_by_id, tmp_path, behavior_hygiene_results="{broken")
    execution = result["checks_performed"]["behavior_hygiene"]
    assert execution["performed"] is False and execution["skipped"] is True
    assert execution["skip_reason"] == "output_unavailable"


@pytest.mark.parametrize("with_error", [False, True])
def test_hygiene_counts_preserve_verdict(
    steps_by_id, tmp_path: Path, with_error: bool
) -> None:
    behavior: dict[str, object] = {
        "bundle": {"name": "fixture"},
        "tools": [{"module": f"tool-{n}"} for n in range(3)],
    }
    if with_error:
        behavior["session"] = {"context": None}
    target = tmp_path / "behaviors/fixture.yaml"
    target.parent.mkdir()
    target.write_text(yaml.safe_dump(behavior), encoding="utf-8")
    hygiene = _execute_step(
        steps_by_id["behavior-hygiene-validation"],
        {"repo_path": str(tmp_path)},
        tmp_path,
    )
    result = _classification(
        steps_by_id,
        tmp_path,
        behavior_hygiene_results=hygiene,
    )
    counts = result["hygiene_summary"]
    assert counts["total_hygiene_errors"] == int(with_error)
    assert counts["total_hygiene_warnings"] == 1
    assert counts["total_hygiene_issues"] == 1 + int(with_error)
    assert result["quality_level"] == ("critical" if with_error else "polish")
    assert sorted(i["severity"] for i in result["hygiene_issues"]) == (
        ["ERROR", "WARNING"] if with_error else ["WARNING"]
    )


REPORT_CHECKS = (
    "behavior_hygiene_results",
    "behavior_reference_hygiene_results",
    "standalone_completeness_results",
    "experiments_validation_results",
    "context_sink_results",
    "tool_placement_results",
    "recipe_validation",
    "structure_lint",
    "mode_validation_results",
    "body_instruction_check_results",
    "agent_description_validation_results",
    "skill_description_validation_results",
    "awareness_redundancy_results",
    "bundle_head_cost_results",
    "readme_install_convention_results",
    "module_dep_resolvability_check",
)


def _report_fixture() -> tuple[dict, dict]:
    """Hand-checked equivalent of the 13-good/five-skip machine outcome."""
    flags = {
        "validate_all": "false",
        "validate_agents": "false",
        "validate_recipes": "false",
        "requested_flags": {
            "validate_all": "false",
            "validate_agents": "false",
            "validate_recipes": "false",
        },
    }
    execution = {
        name: {"performed": False, "skipped": True, "skip_reason": reason}
        for name, reason in (
            ("standalone_completeness", "No bundles in /bundles/ directory"),
            ("experiments", "No bundles in /experiments/ directory"),
            (
                "awareness_redundancy",
                "no in-repo context files are referenced by any bundle or behavior",
            ),
            ("recipe_validation", "disabled_by_flag"),
            ("optional_agent_validation", "no_optional_agent_validator_step"),
        )
    }
    execution["build_check"] = {
        "performed": True,
        "skipped": False,
        "unverified": False,
        "skip_reason": None,
    }
    execution["individual_validation"] = {
        "performed": True,
        "skipped": False,
        "skip_reason": None,
    }
    bundles = [
        {
            "path": f"/fixture/behaviors/b{n}.yaml",
            "name": f"b{n}",
            "category": "behavior",
            "errors": [],
            "warnings": [],
            "has_description": True,
        }
        for n in range(13)
    ]
    classified = [
        {
            "path": b["path"],
            "name": b["name"],
            "category": "behavior",
            "quality": "good",
            "error_count": 0,
            "warning_count": 0,
        }
        for b in bundles
    ]
    checks = {
        name: {"passed": True, "errors": [], "warnings": []} for name in REPORT_CHECKS
    }
    for name in (
        "behavior_hygiene",
        "behavior_reference_hygiene",
        "context_sink",
        "tool_placement",
        "structure_lint",
        "mode_validation",
        "body_instruction",
        "agent_descriptions",
        "skill_descriptions",
        "bundle_head_cost",
        "readme_convention",
        "module_dependencies",
    ):
        execution[name] = {"performed": True, "skipped": False, "skip_reason": None}
    checks["recipe_validation"] = {
        "quality_level": "good",
        "performed": False,
        "skipped": True,
        "skip_reason": "disabled_by_flag",
        "recipes_validated": 0,
    }
    for raw, name in (
        ("standalone_completeness_results", "standalone_completeness"),
        ("experiments_validation_results", "experiments"),
        ("awareness_redundancy_results", "awareness_redundancy"),
    ):
        checks[raw].update(execution[name])
    qc = {
        "validator_version": "3.17.3",
        "quality_level": "good",
        "summary": {
            "total": 13,
            "good": 13,
            "polish": 0,
            "needs_work": 0,
            "critical": 0,
        },
        "bundles": classified,
        "checks_performed": execution,
        "structural_issues": [],
        "validation_flags": flags,
        "environment_status": {"validation_mode": "full"},
    }
    return {
        "schema_version": 1,
        "quality_classification": qc,
        "inputs": {
            "env_check": {
                "validator_version": "3.17.3",
                "validation_mode": "full",
                "capabilities": {
                    "foundation_available": True,
                    "hatchling_available": True,
                    "pip_wheel_available": True,
                },
                "limitations": [],
                "python_version": "fixture",
            },
            "validation_flags": flags,
            "packaging_check": {"has_pyproject": True, "passed": True},
            "discovery_results": {
                "repo_path": "/fixture",
                "total_count": 13,
                "bundles_found": {"behaviors": [b["path"] for b in bundles]},
            },
            "individual_validation": {
                "bundles": bundles,
                "summary": {"total": 13, "loaded": 13, "failed": 0},
            },
            "build_check": {
                "performed": True,
                "skipped": False,
                "passed": True,
                "build_tested": True,
                "build_success": True,
                "errors": [],
                "warnings": [],
                "info": [],
            },
            "checks": checks,
        },
        "requested_options": {"enhance_diagrams": "false"},
    }, {
        "dot_available": True,
        "summary": {"bundle_dot_status": "fresh"},
        "findings": [],
    }


def _publish_report(
    step: dict, root: Path, envelope: dict, doc: dict
) -> subprocess.CompletedProcess:
    assert step.get("type") == "bash", (
        "final publication must not be delegated to an LLM"
    )
    report_path, doc_path = root / "report-input.json", root / "doc-input.json"
    report_path.write_text(json.dumps(envelope), encoding="utf-8")
    doc_path.write_text(json.dumps(doc), encoding="utf-8")
    return _run_report_paths(step, root, report_path, doc_path)


def _run_report_paths(
    step: dict,
    root: Path,
    report_path: Path,
    doc_path: Path,
    *,
    decode_report: bool = True,
    scratch: Path | None = None,
) -> subprocess.CompletedProcess:
    values = {
        "quality_classification._report_payload_file": str(report_path),
        "bundle_doc_freshness._payload_file": str(doc_path),
    }
    env = dict(os.environ, AMPLIFIER_PYTHON=sys.executable)
    env.pop("AMPLIFIER_RECIPE_SCRATCH_DIR", None)
    if scratch is not None:
        env["AMPLIFIER_RECIPE_SCRATCH_DIR"] = str(scratch)
    env.update({name: _render(value, values) for name, value in step["env"].items()})
    assert max(map(len, (env[name] for name in step["env"]))) < 1024
    command = _render(step["command"], values)
    assert len(command.encode()) < 98000
    proc = subprocess.run(
        ["bash", "-c", command],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode == 0:
        # Test transport decoding only; no recipe-engine dependency or copied
        # consumer parser. The real executor seam is verified separately.
        report = json.loads(proc.stdout)
        assert type(report) is str
        receipt_path = Path(
            proc.stderr.split("Report artifact receipt: ", 1)[1].splitlines()[0]
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        artifact = Path(receipt["artifact_path"])
        encoded = report.encode("utf-8")
        assert artifact.is_absolute() and artifact.suffix == ".md"
        assert artifact.read_bytes() == encoded
        assert artifact.stat().st_mode & 0o777 == 0o600
        assert receipt_path.stat().st_mode & 0o777 == 0o600
        assert receipt == {
            "schema_version": 1,
            "step_id": "synthesize-report",
            "validator_version": json.loads(report_path.read_text(encoding="utf-8"))[
                "quality_classification"
            ]["validator_version"],
            "artifact_path": str(artifact),
            "utf8_bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "mode": "0600",
            "input_sha256": {
                "VALIDATE_REPORT_PAYLOAD": hashlib.sha256(
                    report_path.read_bytes()
                ).hexdigest(),
                "VALIDATE_DOC_PAYLOAD": hashlib.sha256(
                    doc_path.read_bytes()
                ).hexdigest(),
            },
        }
        assert artifact.parent == (scratch or report_path.parent).resolve()
        assert receipt_path == artifact.with_suffix(".md.receipt.json")
        if decode_report:
            return subprocess.CompletedProcess(
                proc.args, proc.returncode, report, proc.stderr
            )
    return proc


def _fenced_json(report: str) -> list:
    return [
        json.loads(m[2])
        for m in re.finditer(
            r"^(`{3,})json\n(.*?)\n\1$", report, re.MULTILINE | re.DOTALL
        )
    ]


def test_final_report_is_deterministic_bash_publication(
    steps_by_id, tmp_path: Path
) -> None:
    step = steps_by_id["synthesize-report"]
    assert step.get("type") == "bash"
    assert not any(k in step for k in ("agent", "mode", "prompt"))
    assert (
        step["parse_json"] is True
        and step["on_error"] == "fail"
        and step["timeout"] == 30
    )
    assert step["env"] == {
        "VALIDATE_REPORT_PAYLOAD": "{{quality_classification._report_payload_file}}",
        "VALIDATE_DOC_PAYLOAD": "{{bundle_doc_freshness._payload_file}}",
    }
    assert "bundle-doc-freshness-check" in step["depends_on"]
    envelope, doc = _report_fixture()
    one = _publish_report(step, tmp_path, envelope, doc)
    two = _publish_report(step, tmp_path, envelope, doc)
    assert one.returncode == two.returncode == 0, one.stderr
    assert one.stdout == two.stdout
    for title in (
        "Executive Summary",
        "Environment Status",
        "Bundle Discovery",
        "Python Packaging Status",
        "Quality Classification Summary",
        "v3.0.0 Hygiene Validation",
        "Detailed Findings",
        "Recommendations",
        "Metadata",
    ):
        assert f"## {title}\n" in one.stdout


def test_report_mixed_skips_are_not_passes(steps_by_id, tmp_path: Path) -> None:
    envelope, doc = _report_fixture()
    proc = _publish_report(steps_by_id["synthesize-report"], tmp_path, envelope, doc)
    assert proc.returncode == 0, proc.stderr
    table = proc.stdout.split("### Checks Performed\n", 1)[1].split(
        "### Validation Flags", 1
    )[0]
    rows = [row for row in table.splitlines() if "| SKIPPED |" in row]
    assert len(rows) == 5
    for name, entry in envelope["quality_classification"]["checks_performed"].items():
        if entry["skipped"]:
            assert any(
                name in row and entry["skip_reason"] in row and "PASS" not in row
                for row in rows
            )
    assert "Skipped checks: 5" in proc.stdout
    assert (
        "Skipped checks were not evaluated; no PASS is asserted for them."
        in proc.stdout
    )
    assert "13 loaded; 0 failed" in proc.stdout


@pytest.mark.parametrize(
    "quality,expected",
    [
        ("good", "PASS"),
        ("polish", "PASS WITH SUGGESTIONS"),
        ("needs_work", "PASS WITH WARNINGS"),
        ("critical", "FAIL"),
        ("limited", "LIMITED VALIDATION"),
    ],
)
def test_report_failed_build_and_quality_levels(
    steps_by_id, tmp_path: Path, quality: str, expected: str
) -> None:
    envelope, doc = _report_fixture()
    qc, inputs = envelope["quality_classification"], envelope["inputs"]
    qc["quality_level"] = quality
    if quality == "limited":
        qc["summary"] = {
            "total": 13,
            "good": 0,
            "polish": 0,
            "needs_work": 0,
            "critical": 0,
            "not_validated": 13,
        }
        qc["bundles"] = []
        inputs["individual_validation"] = {
            "skipped": True,
            "environment_limitation": True,
            "reason": "missing_foundation",
            "bundles": [],
        }
        qc["checks_performed"]["individual_validation"] = {
            "performed": False,
            "skipped": True,
            "skip_reason": "missing_foundation",
        }
        inputs["env_check"]["validation_mode"] = "structural_only"
        qc["environment_status"]["validation_mode"] = "structural_only"
    elif quality != "good":
        severity = {
            "polish": "WARNING",
            "needs_work": "needs_work",
            "critical": "ERROR",
        }[quality]
        inputs["build_check"]["errors" if quality == "critical" else "warnings"].append(
            {
                "type": "controlled",
                "severity": severity,
                "message": "actual diagnostic",
                "details": "line one\nC:\\build\\next",
            }
        )
        if quality == "critical":
            inputs["build_check"].update(
                passed=False, build_tested=False, build_success=False
            )
    proc = _publish_report(steps_by_id["synthesize-report"], tmp_path, envelope, doc)
    assert proc.returncode == 0, proc.stderr
    assert f"**Overall Verdict**: {expected}\n" in proc.stdout
    assert "| build_check | true | false | false |" in proc.stdout


def test_report_findings_and_head_scope_are_lossless(
    steps_by_id, tmp_path: Path
) -> None:
    envelope, doc = _report_fixture()
    envelope["quality_classification"]["quality_level"] = "critical"
    checks = envelope["inputs"]["checks"]
    records = [
        {
            "type": f"record-{n}",
            "severity": severity,
            "message": "same | message\n``` \n## PASS",
            "details": "''' $(touch NEVER) C:\\build\\new\n雪",
            "fix": f"fix-{n}",
        }
        for n, severity in enumerate(
            ("ERROR", "WARNING", "INFO", "SUGGESTION", "needs_work", "critical")
        )
    ]
    checks["module_dep_resolvability_check"]["errors"] = records[:1]
    checks["mode_validation_results"]["warnings"] = records[1:]
    checks["mode_validation_results"]["non_modes_found"] = [
        {"file": "not-mode.md", "reason": "document"}
    ]
    checks["agent_description_validation_results"]["non_agents_found"] = [
        {"file": "not-agent.md", "reason": "document"}
    ]
    head = {
        "file": "small.yaml",
        "head_chars": 7,
        "context_chars": 0,
        "agent_description_chars": 0,
        "skill_description_chars": 7,
        "is_lower_bound": True,
        "estimate_scope": "direct only",
        "estimate_qualification": "not a runtime guarantee",
        "unresolved_refs": [{"source": "missing", "reason": "shadowing omission"}],
        "skill_sources": [{"source": "local", "name": "known", "description_chars": 7}],
        "skill_description_chars_excluded": 12,
        "skills_counted_excluded": 2,
        "skill_exclusion_reason": "visibility false",
    }
    checks["bundle_head_cost_results"].update(
        threshold_chars=4000, bundle_details=[head]
    )
    proc = _publish_report(steps_by_id["synthesize-report"], tmp_path, envelope, doc)
    assert proc.returncode == 0, proc.stderr
    blocks = _fenced_json(proc.stdout)
    for name in (
        "bundle_head_cost_results",
        "mode_validation_results",
        "agent_description_validation_results",
    ):
        assert checks[name] in blocks
    inventory = next(
        b for b in blocks if isinstance(b, list) and b and "record" in b[0]
    )
    assert sorted((f["record"] for f in inventory), key=lambda r: r["type"]) == records
    for severity in (
        "ERROR",
        "WARNING",
        "INFO",
        "SUGGESTION",
        "NEEDS_WORK",
        "CRITICAL",
    ):
        assert f"{severity}=1" in proc.stdout
    assert not (tmp_path / "NEVER").exists()
    doc["summary"]["bundle_dot_status"] = "stale"
    doc["findings"] = [
        {
            "code": "BUNDLE_DOT_STALE",
            "severity": "WARNING",
            "message": "stale",
            "fix": "regenerate",
        }
    ]
    envelope["quality_classification"]["quality_level"] = "good"
    for check in checks.values():
        check["errors"], check["warnings"] = [], []
    stale = _publish_report(steps_by_id["synthesize-report"], tmp_path, envelope, doc)
    assert stale.returncode == 0, stale.stderr
    assert "**Overall Verdict**: PASS WITH WARNINGS" in stale.stdout
    assert doc in _fenced_json(stale.stdout)


@pytest.mark.parametrize(
    "fault",
    [
        "schema",
        "version",
        "mode",
        "summary",
        "missing_checks",
        "missing_env",
        "missing_version",
        "missing_quality",
        "contradictory_execution",
        "missing_skip_reason",
        "failed_build_good",
        "untested_build",
        "error_good",
    ],
)
def test_report_inconsistent_inputs_stop_publication(
    steps_by_id, tmp_path: Path, fault: str
) -> None:
    envelope, doc = _report_fixture()
    qc, inputs = envelope["quality_classification"], envelope["inputs"]
    if fault == "schema":
        envelope["schema_version"] = 2
    elif fault == "version":
        inputs["env_check"]["validator_version"] = "other"
    elif fault == "mode":
        qc["environment_status"]["validation_mode"] = "structural_only"
    elif fault == "summary":
        qc["summary"]["good"] = 12
    elif fault == "missing_checks":
        del inputs["checks"]["bundle_head_cost_results"]
    elif fault == "missing_env":
        del inputs["env_check"]
    elif fault == "missing_version":
        del qc["validator_version"]
    elif fault == "missing_quality":
        del qc["quality_level"]
    elif fault == "contradictory_execution":
        qc["checks_performed"]["build_check"]["skipped"] = True
    elif fault == "missing_skip_reason":
        del qc["checks_performed"]["recipe_validation"]["skip_reason"]
    elif fault == "failed_build_good":
        inputs["build_check"]["passed"] = False
    elif fault == "untested_build":
        inputs["build_check"]["build_tested"] = False
    else:
        inputs["checks"]["module_dep_resolvability_check"]["errors"] = [
            {"severity": "ERROR", "message": "late"}
        ]
    proc = _publish_report(steps_by_id["synthesize-report"], tmp_path, envelope, doc)
    assert proc.returncode != 0
    assert proc.stdout == "" and proc.stderr


@pytest.mark.parametrize(
    "fault",
    [
        "missing_execution",
        "raw_skip_mismatch",
        "false_full_capability",
        "aggregate_good_critical",
        "bad_flags",
        "disabled_recipe_performed",
        "nonboolean_build",
    ],
)
def test_report_core_contradictions_refuse_publication(
    steps_by_id, tmp_path: Path, fault: str
) -> None:
    envelope, doc = _report_fixture()
    qc, inputs = envelope["quality_classification"], envelope["inputs"]
    if fault == "missing_execution":
        del qc["checks_performed"]["context_sink"]
    elif fault == "raw_skip_mismatch":
        inputs["checks"]["context_sink_results"]["skipped"] = True
    elif fault == "false_full_capability":
        inputs["env_check"]["capabilities"]["pip_wheel_available"] = False
    elif fault == "aggregate_good_critical":
        qc["summary"].update(good=12, critical=1)
        qc["bundles"][0]["quality"] = "critical"
    elif fault == "bad_flags":
        inputs["validation_flags"]["validate_recipes"] = "maybe"
    elif fault == "disabled_recipe_performed":
        qc["checks_performed"]["recipe_validation"] = {
            "performed": True,
            "skipped": False,
            "skip_reason": None,
        }
    elif fault == "nonboolean_build":
        inputs["build_check"]["performed"] = 1
    proc = _publish_report(steps_by_id["synthesize-report"], tmp_path, envelope, doc)
    assert proc.returncode != 0 and proc.stdout == "" and proc.stderr


def test_report_recipe_execution_unverified_and_nested_inventory(
    steps_by_id, tmp_path: Path
) -> None:
    envelope, doc = _report_fixture()
    qc, inputs = envelope["quality_classification"], envelope["inputs"]
    inputs["validation_flags"]["validate_recipes"] = "true"
    inputs["validation_flags"]["requested_flags"]["validate_recipes"] = "true"
    qc["checks_performed"]["recipe_validation"] = {
        "performed": True,
        "skipped": False,
        "skip_reason": None,
    }
    issue = {"severity": "ERROR", "message": "recipe failure", "fix": "fix recipe"}
    inputs["checks"]["recipe_validation"] = {
        "errors": [issue],  # Context-level copy must not be inventoried twice.
        "approval_summary": "LLM prose must never enter a factual report",
        "quality_classification": {
            "quality_level": "critical",
            "critical_issues": [issue],
            "errors": [issue],  # Only normalized issue lists are inventoried.
        },
    }
    qc["quality_level"] = "critical"
    proc = _publish_report(steps_by_id["synthesize-report"], tmp_path, envelope, doc)
    assert proc.returncode == 0, proc.stderr
    inventory = next(
        b
        for b in _fenced_json(proc.stdout)
        if isinstance(b, list) and b and "record" in b[0]
    )
    assert [i["record"] for i in inventory] == [issue]
    assert "LLM prose must never enter" not in proc.stdout
    assert "**Overall Verdict**: FAIL" in proc.stdout
    assert "| recipe_validation | true | false | null | PERFORMED |" in proc.stdout
    inputs["checks"]["recipe_validation"] = {
        "performed": True,
        "skipped": False,
        "recipes_validated": 1,
    }
    qc["quality_level"] = "good"
    qc["checks_performed"]["context_sink"].update(unverified=True)
    proc = _publish_report(steps_by_id["synthesize-report"], tmp_path, envelope, doc)
    assert proc.returncode == 0, proc.stderr
    assert "**Overall Verdict**: LIMITED VALIDATION" in proc.stdout
    assert "| context_sink | true | false | true | UNVERIFIED |" in proc.stdout


def test_report_legacy_strings_and_unspecified_severity(
    steps_by_id, tmp_path: Path
) -> None:
    envelope, doc = _report_fixture()
    envelope["quality_classification"]["quality_level"] = "critical"
    checks = envelope["inputs"]["checks"]
    raw = {"message": "No explicit severity", "fix": "retain the uncertainty"}
    checks["context_sink_results"].update(
        errors=["legacy error"],
        warnings=["legacy warning"],
        info=["legacy info"],
        suggestions=["legacy suggestion", raw],
    )
    proc = _publish_report(steps_by_id["synthesize-report"], tmp_path, envelope, doc)
    assert proc.returncode == 0, proc.stderr
    inventory = next(
        b
        for b in _fenced_json(proc.stdout)
        if isinstance(b, list) and b and "record" in b[0]
    )
    assert [i["severity"] for i in inventory] == [
        "ERROR",
        "WARNING",
        "INFO",
        "SUGGESTION",
        "UNSPECIFIED",
    ]
    assert inventory[-1]["record"] == raw


@pytest.mark.parametrize(
    "fault",
    [
        "build_unverified",
        "individual_skipped",
        "individual_unverified",
        "checker_unverified",
        "recipe_outer_unverified",
        "optional_agent_performed",
    ],
)
def test_report_specfix1_execution_contradictions(
    steps_by_id, tmp_path: Path, fault: str
) -> None:
    envelope, doc = _report_fixture()
    qc, inputs = envelope["quality_classification"], envelope["inputs"]
    if fault == "build_unverified":
        inputs["build_check"]["unverified"] = True
    elif fault == "individual_skipped":
        inputs["individual_validation"].update(
            performed=False, skipped=True, reason="not loaded"
        )
    elif fault == "individual_unverified":
        inputs["individual_validation"]["unverified"] = True
        qc["checks_performed"]["individual_validation"]["unverified"] = False
    elif fault == "checker_unverified":
        inputs["checks"]["context_sink_results"]["unverified"] = True
        qc["checks_performed"]["context_sink"]["unverified"] = False
    elif fault == "recipe_outer_unverified":
        inputs["checks"]["recipe_validation"] = {
            "unverified": True,
            "quality_classification": {"quality_level": "good"},
        }
        qc["checks_performed"]["recipe_validation"]["unverified"] = False
    else:
        qc["checks_performed"]["optional_agent_validation"].update(
            performed=True, skipped=False
        )
    proc = _publish_report(steps_by_id["synthesize-report"], tmp_path, envelope, doc)
    assert proc.returncode != 0 and proc.stdout == "" and proc.stderr


@pytest.mark.parametrize(
    "source", ["build_check", "individual_validation", "context_sink"]
)
def test_report_specfix1_explicit_unknowns_are_not_false(
    steps_by_id, tmp_path: Path, source: str
) -> None:
    envelope, doc = _report_fixture()
    execution = envelope["quality_classification"]["checks_performed"][source]
    execution.pop("unverified", None)
    inputs = envelope["inputs"]
    raw = (
        inputs["checks"]["context_sink_results"]
        if source == "context_sink"
        else inputs[source]
    )
    raw["unverified"] = True
    proc = _publish_report(steps_by_id["synthesize-report"], tmp_path, envelope, doc)
    assert proc.returncode == 0, proc.stderr
    assert "**Overall Verdict**: LIMITED VALIDATION" in proc.stdout
    assert f"| {source} | true | false | null | UNVERIFIED |" in proc.stdout
    del raw["unverified"]
    proc = _publish_report(steps_by_id["synthesize-report"], tmp_path, envelope, doc)
    assert proc.returncode == 0, proc.stderr
    assert f"| {source} | true | false | null | PERFORMED |" in proc.stdout
    assert "unverified" not in execution  # Renderer never mutates supplied facts.


@pytest.mark.parametrize(
    "source_quality,aggregate",
    [
        ("critical", "good"),
        ("critical", "needs_work"),
        ("needs_work", "good"),
        ("polish", "good"),
    ],
)
@pytest.mark.parametrize("shape", ["flat", "nested"])
def test_report_specfix1_recipe_quality_precedence(
    steps_by_id, tmp_path: Path, source_quality: str, aggregate: str, shape: str
) -> None:
    envelope, doc = _report_fixture()
    qc, inputs = envelope["quality_classification"], envelope["inputs"]
    qc["quality_level"] = aggregate
    inputs["validation_flags"]["validate_recipes"] = "true"
    inputs["validation_flags"]["requested_flags"]["validate_recipes"] = "true"
    qc["checks_performed"]["recipe_validation"] = {
        "performed": True,
        "skipped": False,
        "skip_reason": None,
    }
    machine = {
        "quality_level": source_quality,
        "summary": {"total": 1},
        "critical_issues": [],
    }
    inputs["checks"]["recipe_validation"] = (
        {"quality_classification": machine} if shape == "nested" else machine
    )
    proc = _publish_report(steps_by_id["synthesize-report"], tmp_path, envelope, doc)
    assert proc.returncode != 0 and proc.stdout == "" and proc.stderr


def test_report_specfix1_structural_precedence(steps_by_id, tmp_path: Path) -> None:
    envelope, doc = _report_fixture()
    issue = {
        "type": "orphan_agents",
        "severity": "needs_work",
        "message": "orphan agents",
        "fix": "include the agents",
    }
    envelope["quality_classification"]["structural_issues"] = [issue]
    proc = _publish_report(steps_by_id["synthesize-report"], tmp_path, envelope, doc)
    assert proc.returncode != 0 and proc.stdout == "" and proc.stderr
    envelope["quality_classification"]["quality_level"] = "needs_work"
    proc = _publish_report(steps_by_id["synthesize-report"], tmp_path, envelope, doc)
    assert proc.returncode == 0, proc.stderr
    assert "**Overall Verdict**: PASS WITH WARNINGS" in proc.stdout
    inventory = next(
        b
        for b in _fenced_json(proc.stdout)
        if isinstance(b, list) and b and "record" in b[0]
    )
    assert inventory[0]["record"] == issue


@pytest.mark.parametrize(
    "field",
    ["approval_summary", "composition_analysis", "repo_conventions", "final_report"],
)
def test_report_specfix1_flat_recipe_analysis_refused(
    steps_by_id, tmp_path: Path, field: str
) -> None:
    envelope, doc = _report_fixture()
    qc, inputs = envelope["quality_classification"], envelope["inputs"]
    inputs["validation_flags"]["validate_recipes"] = "true"
    inputs["validation_flags"]["requested_flags"]["validate_recipes"] = "true"
    qc["checks_performed"]["recipe_validation"] = {
        "performed": True,
        "skipped": False,
        "skip_reason": None,
    }
    inputs["checks"]["recipe_validation"] = {
        "performed": True,
        "skipped": False,
        "recipes_validated": 1,
        field: "LLM_APPROVAL_SENTINEL",
    }
    proc = _publish_report(steps_by_id["synthesize-report"], tmp_path, envelope, doc)
    assert proc.returncode != 0 and proc.stdout == "" and proc.stderr
    assert "LLM_APPROVAL_SENTINEL" not in proc.stdout
