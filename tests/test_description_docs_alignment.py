"""The authoring docs must not teach what the validators now warn about.

`validate-bundle-repo.yaml` Phase 2.84 (shipped in PR #373) warns
`awareness_is_pointer_only` on a `context.include` file that is when-to-use
prose plus a `delegate(`/`load_skill(` pointer. That PR corrected the rule in
`agents/bundle-design-expert.md` and added BUNDLE_GUIDE's
"Awareness: concept + trigger + pointer" section -- but left TWO older
passages that still recommend building exactly that file:

  * ``docs/AGENT_AUTHORING.md`` -- "The Behavior + Agent Pattern" opened by
    telling authors to pair the agent with a behavior injecting a "thin
    awareness pointer (~30 lines)" that "tells root sessions: this domain
    exists, delegate to my-expert".
  * ``docs/BUNDLE_GUIDE.md`` -- a worked example labelled the same file an
    "Acceptable variant".

A creation surface that emits the defect by default is the exact failure this
whole item exists to close, and it is worse than an unenforced convention: the
author who follows the doc gets a WARNING from the validator that the doc told
them to earn. Same class as `personafy` instructing a ~700-800 char cap
against a 400-char enforced one.

These are string pins, and they are FAIL-BEFORE: every assertion below fails
on ``origin/main`` at cfd0e23 and passes on this branch. They are deliberately
narrow -- they pin the *contradiction*, not the prose around it, so the docs
stay editable.

Refs: model_performance-8050, model_performance-pwmy
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
AGENT_AUTHORING = REPO_ROOT / "docs" / "AGENT_AUTHORING.md"
BUNDLE_GUIDE = REPO_ROOT / "docs" / "BUNDLE_GUIDE.md"
PRINCIPLES = REPO_ROOT / "context" / "shared" / "description-authoring-principles.md"

#: The section every doc that touches awareness files must point at rather
#: than restate (description-authoring-principles.md V1).
AWARENESS_SECTION_ANCHOR = "awareness-concept--trigger--pointer"


def _text(path: Path) -> str:
    assert path.exists(), f"{path} is missing; this test pins its content"
    return path.read_text(encoding="utf-8")


def _squash(text: str) -> str:
    """Collapse whitespace so a reflow does not break a phrase pin."""
    return re.sub(r"\s+", " ", text)


# =============================================================================
# 1. The pointer-only awareness file is not recommended anywhere
# =============================================================================


def test_agent_authoring_does_not_recommend_a_pointer_only_awareness_file() -> None:
    """AGENT_AUTHORING must not tell authors to build the file 2.84 warns on."""
    body = _squash(_text(AGENT_AUTHORING))
    assert 'The thin awareness file tells root sessions: "This domain exists.' not in body, (
        "AGENT_AUTHORING still recommends a pointer-only awareness file, which "
        "validate-bundle-repo.yaml Phase 2.84 warns on as awareness_is_pointer_only"
    )
    assert "# Thin pointer (~30 lines)" not in body, (
        "the Behavior + Agent Pattern example still ships a pointer-only context.include"
    )


def test_agent_authoring_points_at_the_canonical_awareness_section() -> None:
    """It must cross-reference rather than restate the rule (V1)."""
    assert AWARENESS_SECTION_ANCHOR in _text(AGENT_AUTHORING), (
        "AGENT_AUTHORING must link BUNDLE_GUIDE's awareness section instead of "
        "carrying its own second copy of the rule"
    )


def test_bundle_guide_does_not_call_a_pointer_only_file_an_acceptable_variant() -> None:
    """One document must not answer the same question two different ways."""
    body = _squash(_text(BUNDLE_GUIDE))
    assert "Acceptable variant: a tiny breadcrumb" not in body, (
        "BUNDLE_GUIDE's worked example still blesses the pointer-only awareness "
        "file its own 'Awareness: concept + trigger + pointer' section warns about"
    )
    assert '# ~30 lines, "domain exists, delegate"' not in body


@pytest.mark.parametrize("path", [AGENT_AUTHORING, BUNDLE_GUIDE])
def test_the_phrase_survives_only_as_a_named_anti_pattern(path: Path) -> None:
    """`domain exists, delegate` may still appear -- but only as the thing NOT to do.

    Pinned rather than banned outright: naming the anti-pattern is how a reader
    who already built one recognises it. What must not survive is the phrase
    presented as guidance.
    """
    for line in _text(path).splitlines():
        if "domain exists, delegate" not in line.lower():
            continue
        assert any(marker in line for marker in ("❌", "NOT ", "not ", "second copy")), (
            f"{path.name}: {line.strip()!r} presents the pointer-only pattern as "
            "guidance rather than as an anti-pattern"
        )


# =============================================================================
# 2. The enforced caps are not simultaneously described as open
# =============================================================================


def test_the_principles_doc_does_not_call_the_enforced_agent_cap_provisional() -> None:
    """V5 says Enforced; the closing section used to say the opposite.

    Two versions of the same rule in one file is the defect V1 of that very
    file describes -- "a second copy that can drift from the first and force
    the model to arbitrate between two versions of your own instructions".
    """
    body = _squash(_text(PRINCIPLES))
    assert "**Enforced**" in body, "V5's table must still mark the agent cap enforced"
    assert "agent `meta.description` budgets in V5 remain provisional" not in body, (
        "the closing section still contradicts V5's enforced table"
    )
    assert "WARN/ERROR calibration is still open" not in body


# =============================================================================
# 3. The WHY is paid for with measurements, not taste
# =============================================================================


def test_the_dollar_cost_of_head_bytes_is_cited() -> None:
    """A rule justified only in bytes gets argued with; one priced in dollars does not.

    `model_performance-g7h3` bought 98 paired end-to-end tasks on the daily
    driver. The item that commissioned these docs named this citation
    explicitly and it was missing.
    """
    body = _squash(_text(PRINCIPLES))
    assert "−13.57% $/task" in body or "-13.57% $/task" in body
    assert "[−22.27%, −4.86%]" in body or "[-22.27%, -4.86%]" in body


def test_a_per_bundle_catalog_cost_is_cited_with_its_unit() -> None:
    """Per-bundle is the granularity a bundle author actually works in.

    6,258 -> 1,911 rendered `delegate` catalog bytes across android-tester's
    three agents, re-measured from that repo's pre-/post-sweep commits. The
    unit has to travel with the number: bytes of description as stored plus the
    catalog line's own framing.
    """
    body = _squash(_text(PRINCIPLES))
    assert "6,258 → 1,911 bytes" in body
    assert "443e393" in body and "863afa1" in body, (
        "the commits the figure was measured from must be named so it is reproducible"
    )
