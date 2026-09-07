"""The description rules must EXECUTE, not merely be written down somewhere.

PR #341 set the no-`<example>` policy for agent descriptions in
``context/shared/description-authoring-principles.md``. It was applied inside
``amplifier-foundation`` and **nowhere else**. A year later a sweep found **29
files across 6 repos** still shipping example blocks in descriptions and fixed
them by hand -- and nothing at authoring or validation time would have stopped
the next one. A rule that lives only as a convention is a rule that quietly
stops being true.

These tests pin the four checks that move it to where it executes:

    1  description length cap, in CHARS   WARN at the cap, ERROR at 2x
    2  <example>/<commentary>             ERROR for agents AND SKILLS
    3  awareness redundancy               WARNING, under a stated measurable rule
    4  bundle head cost                   WARNING at 4,000 chars

FAIL-BEFORE IS BUILT IN. Point ``ALIGNMENT_RECIPE_DIR`` at a checkout of the
recipes as they were BEFORE this change and every test below that asserts a
new finding fails, because the check does not exist there:

    git worktree add /tmp/main-recipes main
    ALIGNMENT_RECIPE_DIR=/tmp/main-recipes/recipes python -m pytest \\
        tests/test_description_alignment_checks.py

The counts both ways are quoted in
``docs/lanes/pwmy-principles-to-tooling/DONE-NOTE.md``.

These tests EXECUTE the recipe's own step bodies rather than re-implementing
them. A re-implementation would agree with itself while the recipe drifted,
which is the failure mode under investigation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
RECIPE_DIR = Path(os.environ.get("ALIGNMENT_RECIPE_DIR", REPO_ROOT / "recipes"))
BUNDLE_REPO_RECIPE = RECIPE_DIR / "validate-bundle-repo.yaml"
AGENTS_RECIPE = RECIPE_DIR / "validate-agents.yaml"

# The fixture that violates all four checks at once. Lives under test-fixtures/,
# which every scan excludes by name -- pointing repo_path AT it makes it the
# repo root, so the exclusion does not apply to its own contents.
FIXTURE = REPO_ROOT / "test-fixtures" / "misaligned-bundle"

# The caps, restated here so a silent threshold change fails a test rather than
# quietly widening the gate. Both recipes must carry these same two numbers.
AGENT_WARN_CHARS = 600
AGENT_ERROR_CHARS = 1200
SKILL_WARN_CHARS = 400
SKILL_ERROR_CHARS = 800
HEAD_COST_WARN_CHARS = 4000


def _step_body(recipe: Path, step_id: str) -> str:
    """Extract the named bash step's ``<< 'EOF' ... EOF`` Python body, verbatim."""
    lines = recipe.read_text(encoding="utf-8").splitlines()
    try:
        step = next(i for i, ln in enumerate(lines) if ln.strip() == f'- id: "{step_id}"')
    except StopIteration:  # pragma: no cover - only on a pre-change recipe
        pytest.fail(
            f"step {step_id!r} does not exist in {recipe}. On the pre-change recipes this "
            f"is the expected FAIL-BEFORE: the check had not been written yet."
        )
    start = next(i for i in range(step, len(lines)) if lines[i].rstrip().endswith("<< 'EOF'"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "EOF")
    indent = len(lines[start]) - len(lines[start].lstrip())
    return "\n".join(ln[indent:] for ln in lines[start + 1 : end])


def _run(recipe: Path, step_id: str, repo_path: Path, env_var: str) -> dict:
    env = dict(os.environ)
    env[env_var] = str(repo_path)
    proc = subprocess.run(
        [sys.executable, "-c", _step_body(recipe, step_id)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, f"step {step_id} exited {proc.returncode}\nSTDERR:\n{proc.stderr}"
    return json.loads(proc.stdout)


def run_repo_step(step_id: str, repo_path: Path) -> dict:
    return _run(BUNDLE_REPO_RECIPE, step_id, repo_path, "VALIDATE_BUNDLE_REPO_PATH")


def run_agents_structural(repo_path: Path) -> dict:
    """agent-discovery -> structural-validation, the two-step chain."""
    env = dict(os.environ)
    env["VALIDATE_AGENTS_REPO_PATH"] = str(repo_path)
    discovery = subprocess.run(
        [sys.executable, "-c", _step_body(AGENTS_RECIPE, "agent-discovery")],
        capture_output=True,
        text=True,
        env=env,
    )
    assert discovery.returncode == 0, discovery.stderr
    body = _step_body(AGENTS_RECIPE, "structural-validation").replace(
        "{{discovery_results}}", discovery.stdout.strip()
    )
    proc = subprocess.run([sys.executable, "-c", body], capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def types_of(result: dict, key: str) -> list[str]:
    return [item.get("type") for item in result.get(key, [])]


def codes_of(structural: dict) -> list[str]:
    out = []
    for agent in structural.get("agents", []):
        out += [e["code"] for e in agent.get("errors", [])]
        out += [w["code"] for w in agent.get("warnings", [])]
    return out


# =============================================================================
# The fixture is still a fixture
# =============================================================================


def test_fixture_violates_all_four_checks() -> None:
    """If this stops failing, every fail-before count below stops meaning anything."""
    agents = run_repo_step("agent-description-validation", FIXTURE)
    skills = run_repo_step("skill-description-validation", FIXTURE)
    awareness = run_repo_step("awareness-redundancy-check", FIXTURE)
    head = run_repo_step("bundle-head-cost", FIXTURE)

    assert "agent_description_excessive" in types_of(agents, "errors")
    assert "skill_description_excessive" in types_of(skills, "errors")
    assert "example_block_present" in types_of(agents, "errors")
    assert "example_block_present" in types_of(skills, "errors")
    assert "awareness_redundant_with_catalog" in types_of(awareness, "warnings")
    assert "awareness_is_pointer_only" in types_of(awareness, "warnings")
    assert "bundle_head_cost_high" in types_of(head, "warnings")


# =============================================================================
# 1. Description length cap, in CHARACTERS
# =============================================================================


def test_agent_description_over_2x_is_an_error(tmp_path: Path) -> None:
    repo = _mini_repo(tmp_path, agent_description="x" * (AGENT_ERROR_CHARS + 1))
    result = run_repo_step("agent-description-validation", repo)
    assert "agent_description_excessive" in types_of(result, "errors")
    detail = result["agent_details"][0]
    assert detail["description_chars"] == AGENT_ERROR_CHARS + 1


def test_agent_description_at_the_cap_warns_but_does_not_error(tmp_path: Path) -> None:
    repo = _mini_repo(tmp_path, agent_description="x" * (AGENT_WARN_CHARS + 1))
    result = run_repo_step("agent-description-validation", repo)
    assert types_of(result, "errors") == []
    assert "agent_description_high" in types_of(result, "warnings")


def test_agent_description_under_the_cap_is_clean(tmp_path: Path) -> None:
    repo = _mini_repo(tmp_path, agent_description="x" * (AGENT_WARN_CHARS - 1))
    result = run_repo_step("agent-description-validation", repo)
    assert types_of(result, "errors") == []
    assert types_of(result, "warnings") == []


def test_validate_agents_agrees_with_validate_bundle_repo_on_the_cap() -> None:
    """Two validators that disagree about the cap are worse than one."""
    structural = run_agents_structural(FIXTURE)
    assert "DESCRIPTION_EXCESSIVE" in codes_of(structural)
    repo_wide = run_repo_step("agent-description-validation", FIXTURE)
    assert "agent_description_excessive" in types_of(repo_wide, "errors")


def test_both_recipes_carry_the_same_two_cap_numbers() -> None:
    agents_src = AGENTS_RECIPE.read_text(encoding="utf-8")
    repo_src = BUNDLE_REPO_RECIPE.read_text(encoding="utf-8")
    assert f"DESCRIPTION_WARN_CHARS = {AGENT_WARN_CHARS}" in agents_src
    assert f"DESCRIPTION_ERROR_CHARS = {AGENT_ERROR_CHARS}" in agents_src
    assert f"AGENT_DESCRIPTION_WARN_CHARS = {AGENT_WARN_CHARS}" in repo_src
    assert f"AGENT_DESCRIPTION_ERROR_CHARS = {AGENT_ERROR_CHARS}" in repo_src


# =============================================================================
# 2. <example>/<commentary> -- the SKILL half, which no recipe checked before
# =============================================================================


def test_skill_description_example_block_is_an_error(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    skill = repo / "skills" / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: s\ndescription: |\n  Does a thing when a thing is needed.\n"
        "  <example>\n  user: 'do it'\n  </example>\n---\n\nBody.\n",
        encoding="utf-8",
    )
    result = run_repo_step("skill-description-validation", repo)
    assert "example_block_present" in types_of(result, "errors")


def test_skill_description_commentary_is_an_error(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    skill = repo / "skills" / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: s\ndescription: |\n  Does a thing when a thing is needed.\n"
        "  <commentary>why this routes here</commentary>\n---\n\nBody.\n",
        encoding="utf-8",
    )
    result = run_repo_step("skill-description-validation", repo)
    assert "commentary_present" in types_of(result, "errors")


def test_skill_over_2x_errors_and_at_cap_warns(tmp_path: Path) -> None:
    over = _mini_skill_repo(tmp_path / "over", "y" * (SKILL_ERROR_CHARS + 1))
    at_cap = _mini_skill_repo(tmp_path / "at_cap", "y" * (SKILL_WARN_CHARS + 1))
    assert "skill_description_excessive" in types_of(
        run_repo_step("skill-description-validation", over), "errors"
    )
    warn_result = run_repo_step("skill-description-validation", at_cap)
    assert types_of(warn_result, "errors") == []
    assert "skill_description_high" in types_of(warn_result, "warnings")


def test_docs_is_scanned_and_that_is_a_trap_worth_pinning() -> None:
    """`docs/` is NOT excluded here, on purpose, and it bites.

    Measured live while building this change: committing four evidence
    `SKILL.md` files under `docs/lanes/` took this repo's shipped-skill count
    from 3 to 7 and its largest head-cost figure from 26,368 to 28,279 chars.

    The exclusion set is NOT widened to hide that. It is pinned byte-for-byte
    against every other `EXCLUDED_DIRS` in the recipe by
    `tests/test_anchors_bundles_dry.py::TestDiscoveryScopeParity`, whose
    documented delta from validate-agents' wider scope is exactly `docs` --
    and narrowing it here would hide a skill that genuinely lives under docs/.
    So the rule is: do not name an evidence file `SKILL.md`. This test is what
    catches it if someone does.
    """
    result = run_repo_step("skill-description-validation", REPO_ROOT)
    assert "docs" not in result["excluded_dirs"], (
        "docs/ is deliberately scanned -- see TestDiscoveryScopeParity's documented delta"
    )
    assert result["skills_checked"] == 3, (
        "this repo ships exactly 3 skills; a higher count means a non-skill file "
        f"named SKILL.md was committed: {[d['file'] for d in result['skill_details']]}"
    )


def test_every_new_step_reports_the_scope_it_scanned() -> None:
    """A number is not auditable unless the scope it was taken over is stated."""
    for step in (
        "skill-description-validation",
        "awareness-redundancy-check",
        "bundle-head-cost",
    ):
        result = run_repo_step(step, REPO_ROOT)
        assert set(result["excluded_dirs"]) == {
            ".git",
            ".venv",
            "node_modules",
            "test-fixtures",
            "tests",
        }, step


def test_a_broken_skill_does_not_pass_clean(tmp_path: Path) -> None:
    """v3.13.0's lesson, applied to skills from the start.

    Six ways of failing to read a description used to collapse into "", and ""
    passes every check: 0 chars is under budget and contains no <example>.
    """
    repo = tmp_path / "repo"
    (repo / "skills" / "broken").mkdir(parents=True)
    (repo / "skills" / "broken" / "SKILL.md").write_text(
        "---\nname: broken\ndescription: [unclosed\n---\n\nBody.\n", encoding="utf-8"
    )
    result = run_repo_step("skill-description-validation", repo)
    assert types_of(result, "errors"), "a skill with broken frontmatter must not pass clean"
    assert result["errors"][0]["type"] in {
        "skill_frontmatter_invalid",
        "skill_description_missing",
    }


# =============================================================================
# 3. Awareness redundancy -- the rule is measurable, and it is stated
# =============================================================================


def test_awareness_redundancy_names_the_entry_it_duplicates() -> None:
    result = run_repo_step("awareness-redundancy-check", FIXTURE)
    hits = [w for w in result["warnings"] if w["type"] == "awareness_redundant_with_catalog"]
    assert hits, "the fixture's awareness file restates its agent description"
    assert hits[0]["entry"] == "agent:fixture-agent"
    assert hits[0]["coverage"] >= result["rule"]["file_redundant"]
    assert hits[0]["entry_file"].endswith("fixture-agent.md")


def test_awareness_pointer_only_rule_fires_on_when_to_use_plus_pointer() -> None:
    result = run_repo_step("awareness-redundancy-check", FIXTURE)
    hits = [w for w in result["warnings"] if w["type"] == "awareness_is_pointer_only"]
    assert hits
    assert hits[0]["trigger_share"] >= result["rule"]["trigger_share"]


def test_the_rule_is_reported_so_a_reader_can_audit_it() -> None:
    result = run_repo_step("awareness-redundancy-check", FIXTURE)
    rule = result["rule"]
    assert rule["sentence_cover"] == 0.60
    assert rule["file_redundant"] == 0.60
    assert rule["trigger_share"] == 0.60
    assert rule["min_sentence_terms"] == 3
    assert rule["min_file_sentences"] == 3


def test_foundations_own_context_files_do_not_fire() -> None:
    """The calibration that matters: this must not cry wolf on real context docs.

    Measured 2026-09-07 across every cached bundle repo on one host: 36 context
    files scanned, R1 fired 0 times (max coverage observed 0.333) and R2 fired
    3 times -- each on a file that is genuinely when-to-use prose plus a
    pointer.
    """
    result = run_repo_step("awareness-redundancy-check", REPO_ROOT)
    assert result["context_files_checked"] > 0
    assert result["warnings"] == [], (
        "foundation's own context files are not awareness files and must not fire: "
        f"{[w['file'] for w in result['warnings']]}"
    )


def test_a_substantive_context_file_next_to_a_pointer_one_is_not_flagged() -> None:
    """The fixture carries both kinds; only the pointer-shaped one is flagged."""
    result = run_repo_step("awareness-redundancy-check", FIXTURE)
    flagged = {w["file"] for w in result["warnings"]}
    assert "context/fixture-awareness.md" in flagged
    assert "context/fixture-operating-rules.md" not in flagged


# =============================================================================
# 4. Bundle head cost
# =============================================================================


def test_head_cost_warns_over_the_threshold() -> None:
    result = run_repo_step("bundle-head-cost", FIXTURE)
    hits = [w for w in result["warnings"] if w["type"] == "bundle_head_cost_high"]
    assert hits
    assert hits[0]["head_chars"] > HEAD_COST_WARN_CHARS
    assert result["threshold_chars"] == HEAD_COST_WARN_CHARS


def test_head_cost_reports_its_three_terms_separately() -> None:
    """A single number nobody can decompose is not auditable."""
    detail = run_repo_step("bundle-head-cost", FIXTURE)["bundle_details"][0]
    assert (
        detail["head_chars"]
        == detail["context_chars"]
        + detail["agent_description_chars"]
        + detail["skill_description_chars"]
    )


def test_the_engineered_bundles_stay_under_the_threshold() -> None:
    """anchors is the bundle the head-cost work actually engineered. It must pass."""
    result = run_repo_step("bundle-head-cost", REPO_ROOT)
    by_file = {d["file"]: d for d in result["bundle_details"]}
    anchors = by_file["bundles/anchors/bundle.md"]
    amp_dev = by_file["bundles/anchors-amp-dev/bundle.md"]
    assert anchors["head_chars"] < HEAD_COST_WARN_CHARS
    assert amp_dev["head_chars"] < HEAD_COST_WARN_CHARS


def test_visibility_disabled_skills_are_excluded_but_still_reported() -> None:
    """Turning skill visibility off is a real lever; the report must show what it bought."""
    result = run_repo_step("bundle-head-cost", REPO_ROOT)
    anchors = next(
        d for d in result["bundle_details"] if d["file"] == "bundles/anchors/bundle.md"
    )
    assert anchors["skill_description_chars"] == 0
    assert anchors["skill_description_chars_excluded"] > 0
    assert "visibility" in anchors["skill_exclusion_reason"]


def test_an_unresolvable_reference_is_a_lower_bound_never_a_guess() -> None:
    result = run_repo_step("bundle-head-cost", REPO_ROOT)
    estimated = [d for d in result["bundle_details"] if d.get("is_lower_bound")]
    for detail in estimated:
        assert detail["unresolved_refs"], "is_lower_bound must name what could not be read"


# =============================================================================
# This repository must survive its own new checks
# =============================================================================


def test_foundation_main_has_no_new_errors() -> None:
    """A gate that reddens its own repo on day one gets turned off on day two."""
    agents = run_repo_step("agent-description-validation", REPO_ROOT)
    skills = run_repo_step("skill-description-validation", REPO_ROOT)
    assert agents["errors"] == [], [e["message"] for e in agents["errors"]]
    assert skills["errors"] == [], [e["message"] for e in skills["errors"]]


def test_the_recipes_record_the_change() -> None:
    assert "v3.15.0" in BUNDLE_REPO_RECIPE.read_text(encoding="utf-8")
    assert "v1.8.0" in AGENTS_RECIPE.read_text(encoding="utf-8")


# =============================================================================
# helpers
# =============================================================================


def _mini_repo(tmp_path: Path, agent_description: str) -> Path:
    repo = tmp_path / "repo"
    agent = repo / "agents" / "a.md"
    agent.parent.mkdir(parents=True)
    agent.write_text(
        "---\nmeta:\n  name: a\n  description: " + json.dumps(agent_description) + "\n---\n\nBody.\n",
        encoding="utf-8",
    )
    return repo


def _mini_skill_repo(root: Path, description: str) -> Path:
    skill = root / "skills" / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: s\ndescription: " + json.dumps(description) + "\n---\n\nBody.\n",
        encoding="utf-8",
    )
    return root
