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
import yaml

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
        step = next(
            i for i, ln in enumerate(lines) if ln.strip() == f'- id: "{step_id}"'
        )
    except StopIteration:  # pragma: no cover - only on a pre-change recipe
        pytest.fail(
            f"step {step_id!r} does not exist in {recipe}. On the pre-change recipes this "
            f"is the expected FAIL-BEFORE: the check had not been written yet."
        )
    start = next(
        i for i in range(step, len(lines)) if lines[i].rstrip().endswith("<< 'EOF'")
    )
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
    assert proc.returncode == 0, (
        f"step {step_id} exited {proc.returncode}\nSTDERR:\n{proc.stderr}"
    )
    return json.loads(proc.stdout)


def run_repo_step(step_id: str, repo_path: Path) -> dict:
    return _run(BUNDLE_REPO_RECIPE, step_id, repo_path, "VALIDATE_BUNDLE_REPO_PATH")


#: The literal `structural-validation` uses to receive the discovery payload:
#: a `json.loads` of a triple-quoted template placeholder. Kept as data so the
#: harness fails loudly if the recipe moves it, rather than silently running a
#: step that never got its input.
DISCOVERY_SUBSTITUTION = "json.loads(" + "'''" + "{{discovery_results}}" + "'''" + ")"


def run_agents_structural(repo_path: Path) -> dict:
    """agent-discovery -> structural-validation, the two-step chain.

    The discovery payload reaches the second step through the ENVIRONMENT, not
    through source substitution -- and that substitution is a REAL DEFECT in
    the recipe, not merely a harness inconvenience.

    `structural-validation` json.loads a triple-quoted template placeholder. On
    Windows the discovery JSON carries a path like ``D:\\a\\repo\\...``; Python
    collapses each doubled backslash while parsing the triple-quoted literal,
    leaving ``\\a`` -- not a valid JSON escape -- and the step dies with
    ``Invalid \\escape``. Measured on windows-latest / Python 3.13 in CI on
    this branch. It is the same class of bug v1.7.0 fixed for ``repo_path``,
    still live on the step-to-step payload, and it means `validate-agents`
    cannot complete on a Windows checkout at all. Reported as F4 in
    ``docs/lanes/pwmy-principles-to-tooling/DONE-NOTE.md``; fixing it means
    changing how the runner hands large JSON between steps, which is its own
    item.

    This harness routes around it so these tests measure the CHECKS rather than
    that defect. Every other line of the step body is executed verbatim.
    """
    env = dict(os.environ)
    env["VALIDATE_AGENTS_REPO_PATH"] = str(repo_path)
    discovery = subprocess.run(
        [sys.executable, "-c", _step_body(AGENTS_RECIPE, "agent-discovery")],
        capture_output=True,
        text=True,
        env=env,
    )
    assert discovery.returncode == 0, discovery.stderr
    raw = _step_body(AGENTS_RECIPE, "structural-validation")
    assert DISCOVERY_SUBSTITUTION in raw, (
        "the discovery-payload substitution point moved; update this harness"
    )
    body = raw.replace(
        DISCOVERY_SUBSTITUTION, "json.loads(os.environ['ALIGNMENT_DISCOVERY_JSON'])"
    )
    env["ALIGNMENT_DISCOVERY_JSON"] = discovery.stdout.strip()
    proc = subprocess.run(
        [sys.executable, "-c", body], capture_output=True, text=True, env=env
    )
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
    assert types_of(result, "errors"), (
        "a skill with broken frontmatter must not pass clean"
    )
    assert result["errors"][0]["type"] in {
        "skill_frontmatter_invalid",
        "skill_description_missing",
    }


# =============================================================================
# 3. Awareness redundancy -- the rule is measurable, and it is stated
# =============================================================================


def test_awareness_redundancy_names_the_entry_it_duplicates() -> None:
    result = run_repo_step("awareness-redundancy-check", FIXTURE)
    hits = [
        w for w in result["warnings"] if w["type"] == "awareness_redundant_with_catalog"
    ]
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
    result = _head_cost(
        REPO_ROOT, source_hint="https://github.com/microsoft/amplifier-foundation.git"
    )
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
        assert detail["unresolved_refs"], (
            "is_lower_bound must name what could not be read"
        )


def _head_cost(repo: Path, source_hint: str = "") -> dict:
    """Run the actual heredoc with an explicit (never ambient) source identity."""
    env = dict(os.environ, VALIDATE_BUNDLE_REPO_PATH=str(repo))
    env["VALIDATE_BUNDLE_REPO_SOURCE"] = source_hint
    proc = subprocess.run(
        [sys.executable, "-c", _step_body(BUNDLE_REPO_RECIPE, "bundle-head-cost")],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _catalog_skill(repo: Path, relative: str, name: str, description: str) -> None:
    target = repo / relative / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\n"
        + yaml.safe_dump({"name": name, "description": description})
        + "---\n\nBody.\n",
        encoding="utf-8",
    )


def _skill_mount(repo: Path, sources: list[str], visible: bool = True) -> None:
    target = repo / "behaviors/navigation.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(
            {
                "bundle": {"name": "navigation", "namespace_root": ".."},
                "tools": [
                    {
                        "module": "tool-skills",
                        "config": {
                            "skills": sources,
                            "visibility": {"enabled": visible},
                        },
                    }
                ],
                "agents": {"include": ["a", "b", "c"]},
            }
        ),
        encoding="utf-8",
    )


def _navigation_detail(repo: Path, source_hint: str = "") -> dict:
    return next(
        d
        for d in _head_cost(repo, source_hint)["bundle_details"]
        if d["file"] == "behaviors/navigation.yaml"
    )


@pytest.mark.parametrize("git_source", [False, True])
def test_head_cost_scopes_configured_skills(tmp_path: Path, git_source: bool) -> None:
    """Repository-wide attribution overcharges a two-skill behavior by 2,796 chars."""
    _catalog_skill(tmp_path, "skills/navigation", "navigation", "n" * 200)
    _catalog_skill(tmp_path, "skills/transcript", "transcript", "t" * 132)
    _catalog_skill(tmp_path, "skills/unrelated", "unrelated", "u" * 2796)
    for name, length in (("a", 526), ("b", 572), ("c", 1081)):
        target = tmp_path / f"agents/{name}.md"
        target.parent.mkdir(exist_ok=True)
        target.write_text(
            "---\n"
            + yaml.safe_dump(
                {
                    "meta": {"name": name, "description": "a" * length},
                }
            )
            + "---\nBody.\n",
            encoding="utf-8",
        )
    sources = ["skills/navigation", "skills/transcript"]
    if git_source:
        sources = [
            f"git+https://EXAMPLE.com/owner/repo.git@main#subdirectory={s}"
            for s in sources
        ]
    _skill_mount(tmp_path, sources)
    detail = _navigation_detail(
        tmp_path, "https://example.com/owner/repo@other#subdirectory=behaviors"
    )
    assert detail["skill_description_chars"] == 332
    assert detail["skills_counted"] == 2
    assert {s["name"] for s in detail["skill_sources"]} == {"navigation", "transcript"}
    assert detail["head_chars"] == 2511
    assert detail["is_lower_bound"] is True  # ambient discovery is not measured
    assert any("ambient" in str(ref) for ref in detail["unresolved_refs"])


@pytest.mark.parametrize("reverse,expected", [(False, 17), (True, 29)])
def test_head_cost_source_priority_and_recursion(
    tmp_path: Path, reverse: bool, expected: int
) -> None:
    _catalog_skill(tmp_path, "first/nested/a", "duplicate", "a" * 11)
    _catalog_skill(tmp_path, "first/z", "unique", "b" * 6)
    _catalog_skill(tmp_path, "second/a", "duplicate", "c" * 23)
    sources = ["first", "second"] if not reverse else ["second", "first"]
    _skill_mount(tmp_path, sources + sources)
    detail = _navigation_detail(tmp_path)
    assert detail["skill_description_chars"] == expected
    assert detail["skills_counted"] == 2


@pytest.mark.parametrize("source", ["skills/only/SKILL.md", "beside"])
def test_head_cost_rejects_file_and_manifest_relative_fallback(
    tmp_path: Path, source: str
) -> None:
    _catalog_skill(tmp_path, "skills/only", "only", "known")
    _catalog_skill(tmp_path, "behaviors/beside", "beside", "not runtime cwd")
    _skill_mount(tmp_path, [source])
    detail = _navigation_detail(tmp_path)
    assert detail["skills_counted"] == 0
    assert detail["skill_description_chars"] == 0
    assert any(source in str(ref) for ref in detail["unresolved_refs"])


@pytest.mark.parametrize(
    "unknown",
    [
        "git+https://foreign.example/owner/repo@main#subdirectory=skills/known",
        "git+https://example.com/owner/repo@main#subdirectory=skills/known",
        "missing",
        "skills/broken",
        "../outside",
        "skills/escape",
        "namespace:skills",
        "~validator_review_no_such_account_6592/catalog",
    ],
)
@pytest.mark.parametrize("unknown_first", [True, False])
def test_head_cost_unknown_sources_and_visibility(
    tmp_path: Path, unknown: str, unknown_first: bool
) -> None:
    repo = tmp_path / "repo"
    _catalog_skill(repo, "skills/known", "known", "visible")
    _catalog_skill(tmp_path, "outside", "escaped", "outside")
    (repo / "skills/escape").symlink_to(tmp_path / "outside", target_is_directory=True)
    broken = repo / "skills/broken/SKILL.md"
    broken.parent.mkdir()
    broken.write_text("---\nname: broken\ndescription: [unclosed\n---\nBody.\n")
    sources = [unknown, "skills/known"] if unknown_first else ["skills/known", unknown]
    _skill_mount(repo, sources)
    detail = _navigation_detail(repo)  # absent hint must not guess repo identity
    assert detail["skill_description_chars"] == (0 if unknown_first else 7)
    assert detail["skills_counted"] == (0 if unknown_first else 1)
    assert detail["is_lower_bound"] is True
    assert any(unknown in str(ref) for ref in detail["unresolved_refs"])
    if unknown.startswith("~validator_review"):
        assert any("home directory" in str(ref) for ref in detail["unresolved_refs"])
    assert any("ambient" in str(ref) for ref in detail["unresolved_refs"])
    if unknown_first:
        assert any("shadow" in str(ref) for ref in detail["unresolved_refs"])
    _skill_mount(repo, sources, visible=False)
    hidden = _navigation_detail(repo)
    assert hidden["skill_description_chars"] == 0
    assert hidden["skills_counted"] == 0
    assert hidden["skill_description_chars_excluded"] == (0 if unknown_first else 7)
    assert "visibility" in hidden["skill_exclusion_reason"]


def test_head_cost_hint_mismatch_and_no_mount(tmp_path: Path) -> None:
    _catalog_skill(tmp_path, "skills/known", "known", "visible")
    uri = "git+https://example.com/owner/repo@main#subdirectory=skills"
    _skill_mount(tmp_path, [uri])
    detail = _navigation_detail(tmp_path, "https://other.example/owner/repo")
    assert detail["skills_counted"] == 0
    assert any(uri in str(ref) for ref in detail["unresolved_refs"])
    (tmp_path / "behaviors/navigation.yaml").write_text("bundle:\n  name: navigation\n")
    detail = _navigation_detail(tmp_path)
    assert detail["skill_description_chars"] == 0
    assert not detail["is_lower_bound"]


@pytest.mark.parametrize("source", ["./catalog", "~/catalog"])
def test_head_cost_candidate_cwd_utf8_and_exclusions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _catalog_skill(tmp_path, "catalog/nested", "selected", "é雪ab")
    for excluded in ("tests", "test-fixtures", ".venv", ".git", "node_modules"):
        _catalog_skill(tmp_path, f"catalog/{excluded}/hidden", "excluded", "unrelated")
    _catalog_skill(tmp_path, "behaviors/catalog/wrong", "wrong-cwd", "unrelated")
    _skill_mount(tmp_path, [source])
    detail = _navigation_detail(tmp_path)
    assert detail["skill_description_chars"] == 4  # characters, not UTF-8 bytes
    assert detail["skills_counted"] == 1
    assert detail["skill_sources"][0]["file"] == "catalog/nested/SKILL.md"
    skill_unknowns = [ref for ref in detail["unresolved_refs"] if isinstance(ref, dict)]
    assert len(skill_unknowns) == 1  # ambient only, not excluded metadata
    assert skill_unknowns[0]["source"] == "ambient workspace/user discovery"


@pytest.mark.parametrize(
    "content",
    [
        b"---\nname: missing-description\n---\nBody.\n",
        b"---\ndescription: missing-name\n---\nBody.\n",
        b"---\nname: malformed\ndescription: \xff\n---\nBody.\n",
    ],
)
def test_head_cost_invalid_metadata_remains_unknown(
    tmp_path: Path, content: bytes
) -> None:
    target = tmp_path / "catalog/SKILL.md"
    target.parent.mkdir()
    target.write_bytes(content)
    _skill_mount(tmp_path, ["catalog"])
    detail = _navigation_detail(tmp_path)
    assert detail["skill_description_chars"] == 0
    assert detail["skills_counted"] == 0
    assert any("metadata" in str(ref) for ref in detail["unresolved_refs"])


def test_head_cost_threshold_stays_live_with_partial_estimates(tmp_path: Path) -> None:
    _catalog_skill(tmp_path, "skills/large", "large", "x" * 4001)
    _skill_mount(tmp_path, ["skills/large"])
    result = _head_cost(tmp_path)
    assert result["warnings"][0]["severity"] == "WARNING"
    assert result["warnings"][0]["head_chars"] == 4001
    _catalog_skill(tmp_path, "skills/large", "large", "x" * 4000)
    result = _head_cost(tmp_path)
    assert not result["warnings"]
    assert result["bundle_details"][0]["is_lower_bound"]
    assert "not" in result["bundle_details"][0]["estimate_qualification"].lower()


@pytest.mark.parametrize("count", [1, 2, 3])
def test_tool_count_still_warns_on_three_direct_tools(
    tmp_path: Path, count: int
) -> None:
    target = tmp_path / "behaviors/unrelated.yaml"
    target.parent.mkdir()
    target.write_text(
        yaml.safe_dump(
            {
                "bundle": {"name": "unrelated"},
                "tools": [{"module": f"tool-{n}"} for n in range(count)],
            }
        ),
        encoding="utf-8",
    )
    result = run_repo_step("behavior-hygiene-validation", tmp_path)
    hits = [w for w in result["warnings"] if w["type"] == "tool_count_high"]
    assert len(hits) == (1 if count == 3 else 0)
    assert not hits or hits[0]["severity"] == "WARNING"


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
        "---\nmeta:\n  name: a\n  description: "
        + json.dumps(agent_description)
        + "\n---\n\nBody.\n",
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
