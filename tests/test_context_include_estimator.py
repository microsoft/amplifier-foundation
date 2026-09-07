"""The `context.include` token estimator must MEASURE the file, not guess at it.

`validate-bundle-repo`'s behavior-hygiene Rule 4 resolved an include only two ways:
a leading ``@`` was charged a flat 500 tokens, anything else was tried as a relative
path. A ``<namespace>:<path>`` include -- ``foundation:context/agents/foo.md``, the
form this repo's own behaviors use -- matched NEITHER: it does not start with ``@``,
and neither ``behaviors/foundation:context/...`` nor ``<repo>/foundation:context/...``
exists. So it fell through to the flat 500-token default.

``behaviors/agents.yaml`` carries two such includes. 2 x 500 = **exactly 1000**,
against a ``> 1000`` ERROR gate -- so 6,276 real on-disk tokens were reported as 1000
and graded WARNING, one token under the ERROR that was true.

THE RULER, NAMED (the calibration the fix is footnoted with):
    The estimator's unit is unchanged: ``len(content) // 4`` (chars/4), chosen so the
    recipe stays dependency-free. Measured on this repo's own context markdown
    (n=31 files >= 1500 bytes), chars/4 / o200k_base has median **1.128** and range
    **0.940 - 1.312**. It runs HIGH on prose, which is the conservative direction for
    a budget gate: the gate fires early, never late.

    The fixture below is ``context/agents/session-storage-knowledge.md`` -- a real
    file from the very directory the defect lived in, not synthetic and not tuned.
    Its chars/4 count is within +/-10% of a real tokenizer (o200k_base), and
    ``test_measured_count_is_within_10pct_of_a_real_tokenizer`` asserts exactly that
    whenever ``tiktoken`` is importable.

These tests EXECUTE the recipe's own step body rather than re-implementing it. A
re-implementation would agree with itself while the recipe drifted, which is the
failure mode under investigation.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
RECIPE = REPO_ROOT / "recipes" / "validate-bundle-repo.yaml"
STEP_ID = "behavior-hygiene-validation"

# A real file from the directory the defect lived in. Not synthetic, not tuned.
FIXTURE_SOURCE = REPO_ROOT / "context" / "agents" / "session-storage-knowledge.md"

# chars/4 must land within this band of a real tokenizer on the fixture.
TOKENIZER_TOLERANCE = 0.10

# What an include the validator genuinely cannot resolve still costs.
FLAT_FALLBACK = 500


def _step_body(recipe: Path, step_id: str) -> str:
    """Extract the named bash step's ``<< 'EOF' ... EOF`` Python body, verbatim."""
    lines = recipe.read_text(encoding="utf-8").splitlines()
    step = next(i for i, ln in enumerate(lines) if ln.strip() == f'- id: "{step_id}"')
    start = next(
        i for i in range(step, len(lines)) if lines[i].rstrip().endswith("<< 'EOF'")
    )
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "EOF")
    indent = len(lines[start]) - len(lines[start].lstrip())
    return "\n".join(ln[indent:] for ln in lines[start + 1 : end])


def _run_step(repo_path: Path) -> dict:
    """Run the recipe's real behavior-hygiene step against a repo path."""
    env = dict(os.environ)
    env["VALIDATE_BUNDLE_REPO_PATH"] = str(repo_path)
    proc = subprocess.run(
        [sys.executable, "-c", _step_body(RECIPE, STEP_ID)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, (
        f"step exited {proc.returncode}\nSTDERR:\n{proc.stderr}"
    )
    return json.loads(proc.stdout)


def _chars4(content: str) -> int:
    return len(content) // 4


def _detail(result: dict, name: str) -> dict:
    matches = [d for d in result["behavior_details"] if d.get("name") == name]
    assert matches, (
        f"no behavior detail named {name!r} in {[d.get('name') for d in result['behavior_details']]}"
    )
    return matches[0]


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """A minimal repo whose only behavior includes a known-token file by namespace ref."""
    repo = tmp_path / "repo"
    target = repo / "context" / "agents" / FIXTURE_SOURCE.name
    target.parent.mkdir(parents=True)
    shutil.copyfile(FIXTURE_SOURCE, target)

    behaviors = repo / "behaviors"
    behaviors.mkdir()
    (behaviors / "known-tokens.yaml").write_text(
        "name: known-tokens\n"
        "description: fixture behavior with one namespace-qualified include\n"
        "context:\n"
        f"  include:\n    - foundation:context/agents/{FIXTURE_SOURCE.name}\n",
        encoding="utf-8",
    )
    return repo


@pytest.fixture
def fixture_tokens() -> int:
    """The fixture's token count under the estimator's own declared ruler (chars/4)."""
    return _chars4(FIXTURE_SOURCE.read_text(encoding="utf-8"))


# =============================================================================
# The defect: a `<namespace>:<path>` include was charged a flat guess
# =============================================================================


def test_namespace_qualified_include_is_measured_not_guessed(
    fixture_repo: Path, fixture_tokens: int
) -> None:
    """FAIL-BEFORE: on the unfixed estimator this reports exactly 500, not the file."""
    detail = _detail(_run_step(fixture_repo), "known-tokens")
    assert detail["context_total_tokens"] == fixture_tokens, (
        f"expected the file to be READ ({fixture_tokens} tokens, chars/4); got "
        f"{detail['context_total_tokens']}. A flat {FLAT_FALLBACK} means the "
        f"`<namespace>:<path>` include fell through to the guess again."
    )
    assert detail["context_total_tokens"] != FLAT_FALLBACK


def test_at_prefixed_namespace_include_is_also_measured(
    fixture_repo: Path, fixture_tokens: int
) -> None:
    """`@foundation:path` and `foundation:path` name the same file; both must be read."""
    behavior = fixture_repo / "behaviors" / "known-tokens.yaml"
    behavior.write_text(
        behavior.read_text(encoding="utf-8")
        .replace("- foundation:", "- '@foundation:")
        .rstrip()
        + "'\n",
        encoding="utf-8",
    )
    detail = _detail(_run_step(fixture_repo), "known-tokens")
    assert detail["context_total_tokens"] == fixture_tokens


def test_measured_include_is_not_flagged_as_an_estimate(fixture_repo: Path) -> None:
    detail = _detail(_run_step(fixture_repo), "known-tokens")
    assert "context_unresolved_includes" not in detail
    assert "context_tokens_is_estimate" not in detail


# =============================================================================
# The fallback survives -- but it stops being silent
# =============================================================================


def test_unresolvable_include_keeps_the_flat_fallback_and_says_so(
    fixture_repo: Path,
) -> None:
    """A guess folded silently into a number that reads as measured IS the defect."""
    (fixture_repo / "behaviors" / "known-tokens.yaml").write_text(
        "name: known-tokens\n"
        "description: fixture behavior with an include this repo genuinely does not carry\n"
        "context:\n"
        "  include:\n    - otherbundle:context/not-in-this-repo.md\n",
        encoding="utf-8",
    )
    detail = _detail(_run_step(fixture_repo), "known-tokens")
    assert detail["context_total_tokens"] == FLAT_FALLBACK
    assert detail["context_unresolved_includes"] == [
        "otherbundle:context/not-in-this-repo.md"
    ]
    assert detail["context_tokens_is_estimate"] is True


def test_url_style_refs_are_not_split_on_their_scheme_colon(fixture_repo: Path) -> None:
    """`git+https://...` has a colon but no namespace; it must not be path-split."""
    (fixture_repo / "behaviors" / "known-tokens.yaml").write_text(
        "name: known-tokens\n"
        "description: fixture behavior with a URL-shaped include\n"
        "context:\n"
        "  include:\n    - git+https://example.invalid/repo.git\n",
        encoding="utf-8",
    )
    detail = _detail(_run_step(fixture_repo), "known-tokens")
    assert detail["context_total_tokens"] == FLAT_FALLBACK
    assert detail["context_unresolved_includes"] == [
        "git+https://example.invalid/repo.git"
    ]


# =============================================================================
# The gate: the true number must actually reach the ERROR threshold
# =============================================================================


def test_two_such_includes_now_trip_the_error_gate(
    fixture_repo: Path, fixture_tokens: int
) -> None:
    """2 x flat-500 = exactly 1000, one token under the `> 1000` ERROR. Measured, it is not."""
    second = fixture_repo / "context" / "agents" / "second.md"
    shutil.copyfile(FIXTURE_SOURCE, second)
    (fixture_repo / "behaviors" / "known-tokens.yaml").write_text(
        "name: known-tokens\n"
        "description: fixture behavior with two namespace-qualified includes\n"
        "context:\n"
        "  include:\n"
        f"    - foundation:context/agents/{FIXTURE_SOURCE.name}\n"
        "    - foundation:context/agents/second.md\n",
        encoding="utf-8",
    )
    result = _run_step(fixture_repo)
    detail = _detail(result, "known-tokens")
    assert detail["context_total_tokens"] == 2 * fixture_tokens
    excessive = [e for e in result["errors"] if e["type"] == "context_tokens_excessive"]
    assert excessive, (
        f"{2 * fixture_tokens} tokens must trip the >1000 ERROR gate; the unfixed "
        f"estimator reported exactly 1000 here and graded it a WARNING instead."
    )


# =============================================================================
# The ruler, cross-checked against a real tokenizer
# =============================================================================


def test_measured_count_is_within_10pct_of_a_real_tokenizer(
    fixture_tokens: int,
) -> None:
    """chars/4 vs o200k_base on the fixture. The flat 500 is ~81% low on the same file."""
    tiktoken = pytest.importorskip(
        "tiktoken", reason="tokenizer cross-check needs tiktoken"
    )
    content = FIXTURE_SOURCE.read_text(encoding="utf-8")
    real = len(tiktoken.get_encoding("o200k_base").encode(content))
    error = abs(fixture_tokens - real) / real
    assert error <= TOKENIZER_TOLERANCE, (
        f"chars/4={fixture_tokens} vs o200k_base={real} is {error:.1%} off, outside "
        f"the stated +/-{TOKENIZER_TOLERANCE:.0%} band for this fixture."
    )
    flat_error = abs(FLAT_FALLBACK - real) / real
    assert flat_error > TOKENIZER_TOLERANCE, (
        "the fixture no longer discriminates: the flat fallback is inside the band too"
    )


# =============================================================================
# The recipe itself
# =============================================================================


def test_recipe_declares_the_estimator_fix() -> None:
    recipe = RECIPE.read_text(encoding="utf-8")
    assert "_candidate_relpaths" in recipe, (
        "validate-bundle-repo must resolve `<namespace>:<path>` includes; without it a "
        "6,276-token behavior is reported as 1000 and graded WARNING."
    )
    assert "context_unresolved_includes" in recipe
    assert "context_tokens_is_estimate" in recipe
    assert "v3.14.0" in recipe, "the changelog must record the estimator fix"
