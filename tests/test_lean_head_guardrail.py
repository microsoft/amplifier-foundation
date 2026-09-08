"""The Anthropic non-regression guardrail for the lean head (model_performance-zc6t).

WHAT THIS PROTECTS, AND WHY IT IS WORTH A CI JOB
------------------------------------------------
`model_performance-g7h3` bought 98 end-to-end claude-opus-5 / S7-17 runs
(49/arm, $428.10) and all three pre-registered estimators excluded zero for the
first time in the program:

    primary      (VALID only, n 11/8)  -13.57%  95% CI [-22.27%, -4.86%]
    ALL runs     (n 49/49)             -16.42%  95% CI [-23.29%,  -9.56%]
    block-paired (7 pairs)             -14.44%  95% CI [-26.83%,  -2.06%]

Co-primary quality is CITED from `model_performance-5zp`: one-sided 95% lower
bound -7.15 pp against the frozen -10 pp non-inferiority margin -- CLEARS.

The head is SHARED between vendors and Anthropic is the daily driver, where this
lever is worth 4.20x more per request than on the OpenAI wire. That multiple
rests entirely on ONE cold head write per session: the head must be byte-stable
so `cache_read` returns to exactly it at every compaction boundary. A head that
silently regrows, or that acquires a per-turn-varying byte, costs that 4.20x
without anything failing.

WHAT EACH ASSERTION CAN AND CANNOT PROVE
----------------------------------------
Stated plainly, because a guardrail whose limits are not written down gets read
as proving more than it does:

(a) HEAD CENSUS -- fully proven here, for the components THIS repo owns.
    Exact char counts of real source files and of the real composed `delegate`
    preamble. No estimate, no token model. This is the assertion that goes RED
    against the pre-change (stock) text: see
    `docs/lanes/zc6t-lean-head-ship/DONE-NOTE.md` for the recorded
    fail-before/pass-after CI run URLs.

(b) ONE COLD HEAD WRITE PER SESSION -- the *precondition* is proven here; the
    cache behaviour itself is not. A static repo test cannot observe
    `cache_read` on a live Anthropic response. What it CAN prove, and what
    actually breaks the property in practice, is that the owned head text is
    deterministic: no timestamp, no absolute path, no content hash, no PID, no
    session id. Those are the tokens that make turn N's head differ from turn
    N-1's and force a second cold write. The live-wire half is measured, not
    asserted -- `probes/bji-lean-head/head_stability.py` in the evals repo, and
    the 98 g7h3 runs.

(c) TASK-SUCCESS NON-INFERIORITY -- cited, not re-measured. The item's spend
    authority is $0 for exactly this reason: 5zp already bought the answer. The
    assertion here pins the citation so the margin cannot be quietly loosened
    or the lower bound quietly edited to something that no longer clears.

Pure-Python, filesystem-only, no network and no API key: this runs identically
on the Linux and Windows CI legs.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = Path(__file__).parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "lean-head-v1"
DELEGATE_MODULE_DIR = REPO_ROOT / "modules" / "tool-delegate"

# --- (a) the census budgets --------------------------------------------------
#
# Chars, not bytes and not tokens. The published head census is in chars
# (`instr 45,842 + tools 38,477 = 84,319` stock; `21,654 + 26,595 = 48,249` V1),
# so these stay in the same unit and can be added to it directly.
#
# Each budget is the MEASURED lean size of the artifact, pinned. A budget set
# above what shipped would let the head regrow up to it silently, which is the
# whole failure mode.
OWNED_HEAD_BUDGET_CHARS = {
    # Required cwd-AGENTS auto-loading rule costs +12 chars.
    "bundles/anchors/context/system.md": 1154,
    "bundles/anchors-amp-dev/context/amplifier-ecosystem.md": 1300,
    "delegate:preamble": 938,
}

# What the same three artifacts measured BEFORE this change. Kept so the saving
# is a number in the repo rather than a claim in a commit message, and so the
# red-before run has something to point at.
OWNED_HEAD_STOCK_CHARS = {
    "bundles/anchors/context/system.md": 1358,
    "bundles/anchors-amp-dev/context/amplifier-ecosystem.md": 1432,
    "delegate:preamble": 1190,
}

# --- (c) the frozen margin and its citation ---------------------------------
NON_INFERIORITY_MARGIN_PP = -10.0  # frozen, SPEC.md 9.1
CITED_QUALITY_LOWER_BOUND_PP = -7.15  # model_performance-5zp, one-sided 95% LB
CITED_COST_DELTA_PCT = -13.57  # model_performance-g7h3 primary
CITED_COST_CI_PCT = (-22.27, -4.86)

# --- fidelity: rules that must survive every rewrite ------------------------
#
# "Fidelity beats compression at every point of conflict." A saving bought by
# deleting a real instruction is not a saving; it is a behaviour change with no
# trace back to the commit that caused it. Each entry is a literal that must
# appear in the named file -- a rule, a constraint, a command, or a pointer that
# was present in the stock text.
REQUIRED_RULES = {
    "bundles/anchors/context/system.md": [
        "You are Amplifier",
        "`todo`",
        "GitHub-flavored markdown",
        "`file_path:line_number`",
        "defensive security only",
        "AGENTS.md",
        "load_skill(list=true)",
        'mode(operation="list")',
        'recipes(operation="list")',
        "Generated with Amplifier",
        "Co-Authored-By: Amplifier <240397093+microsoft-amplifier@users.noreply.github.com>",
    ],
    "bundles/anchors-amp-dev/context/amplifier-ecosystem.md": [
        "development OF the Amplifier ecosystem",
        "dependency order",
        "core → foundation → modules → bundles → apps",
        "never push downstream before upstream is merged",
        "DTU",
        "push core-side first",
        "Never read `events.jsonl` directly",
        "100k tokens",
        # Restored during this change -- present in stock, absent from the v1
        # span (which was authored against an older, smaller stock text).
        "context-intelligence:session-navigator",
        "context-intelligence:graph-analyst",
        "scripts/amplifier-session.py",
        "there is no `amplifier session repair` subcommand",
    ],
}

# --- (b) tokens that would break one-cold-head-write-per-session -------------
#
# Anything whose value can differ between two composions of the same head.
VOLATILE_PATTERNS = {
    "an absolute POSIX path": re.compile(r"(?<![\w`])/(?:home|root|tmp|Users)/"),
    "a Windows absolute path": re.compile(r"[A-Za-z]:\\\\"),
    "an ISO timestamp": re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"),
    "a 40-hex sha": re.compile(r"\b[0-9a-f]{40}\b"),
    "a cache content-hash suffix": re.compile(r"-[0-9a-f]{16}\b"),
}

CONTEXT_FILES = tuple(REQUIRED_RULES)


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _delegate_preamble(*, self_delegation: bool = True) -> str:
    """The `delegate` description with the runtime agent catalog removed.

    The catalog is appended by the tool at runtime from whatever bundle is
    composed, so it is out of this guardrail's scope by construction -- the
    preamble is the part that lives in this repo's source.
    """
    if str(DELEGATE_MODULE_DIR) not in sys.path:
        sys.path.insert(0, str(DELEGATE_MODULE_DIR))
    from amplifier_module_tool_delegate import DelegateTool  # noqa: PLC0415

    coordinator = MagicMock()
    coordinator.session_id = "guardrail-session"
    coordinator.config = {"agents": {}}
    coordinator.session_state = {}
    coordinator._tool_dispatch_context = {}
    capabilities = {"session.spawn": AsyncMock(), "self_delegation_depth": 0}
    coordinator.get_capability = lambda name: capabilities.get(name)
    coordinator.get = MagicMock(return_value=None)
    parent = MagicMock()
    parent.session_id = "guardrail-session"
    parent.config = {"session": {"orchestrator": {}}}
    coordinator.session = parent

    tool = DelegateTool(
        coordinator,
        {
            "features": {"self_delegation": {"enabled": self_delegation}},
            "settings": {"exclude_tools": []},
        },
    )
    description = tool.description
    for marker in ("\n\nAvailable agents:\n", "\n\nNo agents currently registered"):
        if marker in description:
            return description.split(marker)[0]
    return description


class TestBytePinsAgainstTheV1Head:
    """HALF A: the shipped `delegate` preamble IS the V1 text, byte for byte.

    The acceptance criterion is equality with what `leanhead_shim.py` produced
    for variant v1 -- not with a prose description of it -- so the reference is
    vendored here rather than paraphrased. `tests/fixtures/lean-head-v1/` is a
    verbatim copy of the relevant slice of `probes/bji-lean-head/v1_tools.json`
    from the evals repo; see that directory's README.md for provenance.
    """

    def test_delegate_preamble_is_byte_identical_to_v1(self) -> None:
        want = (FIXTURES / "delegate_preamble.txt").read_text(encoding="utf-8")
        got = _delegate_preamble()
        assert got == want, (
            "The composed `delegate` preamble has drifted from the V1 lean head "
            f"({len(got)} chars vs the pinned {len(want)}). The 13.57% cost "
            "reduction was measured against these exact bytes.\n"
            f"--- pinned v1 ---\n{want}\n--- composed now ---\n{got}"
        )

    def test_the_v1_fixture_is_the_measured_size(self) -> None:
        """Guards the fixture itself: an edited reference proves nothing."""
        want = (FIXTURES / "delegate_preamble.txt").read_text(encoding="utf-8")
        assert len(want) == OWNED_HEAD_BUDGET_CHARS["delegate:preamble"]


class TestGuardrailAHeadCensus:
    """(a) The census stays at or below the shipped lean size.

    THIS is the assertion that fails against the stock head. Run it on the
    pre-change tree and every one of these three artifacts is over budget.
    """

    @pytest.mark.parametrize("rel", sorted(CONTEXT_FILES))
    def test_context_file_within_budget(self, rel: str) -> None:
        budget = OWNED_HEAD_BUDGET_CHARS[rel]
        actual = len(_read(rel))
        assert actual <= budget, (
            f"{rel} is {actual} chars, over its pinned lean budget of {budget} "
            f"(+{actual - budget}). The head is shared with the Anthropic wire, "
            "where every char is paid on one cold write per session. If the "
            "growth is a RULE that must be there, raise the budget in the same "
            "commit and say why in the message -- never silently."
        )

    def test_delegate_preamble_within_budget(self) -> None:
        budget = OWNED_HEAD_BUDGET_CHARS["delegate:preamble"]
        actual = len(_delegate_preamble())
        assert actual <= budget, (
            f"The delegate preamble is {actual} chars, over its pinned lean "
            f"budget of {budget} (+{actual - budget})."
        )

    def test_the_owned_head_is_smaller_than_it_was(self) -> None:
        """The saving is a number in the repo, not a claim in a commit message."""
        lean = sum(OWNED_HEAD_BUDGET_CHARS.values())
        stock = sum(OWNED_HEAD_STOCK_CHARS.values())
        assert lean < stock, f"lean {lean} is not below stock {stock}"
        assert stock - lean == 588, (
            f"The current saving moved: {stock - lean} chars, expected 588 "
            "after the original measured 600-char saving absorbed the required "
            "cwd-AGENTS rule. Update the budget and DONE-NOTE together, or the "
            "current census and this repo disagree."
        )


class TestGuardrailBOneColdHeadWritePerSession:
    """(b) The precondition for `cache_read` returning to exactly the head.

    See the module docstring: this proves determinism of the owned head text,
    not the cache behaviour itself. A volatile token here is the concrete,
    repo-side cause of a second cold write.
    """

    @pytest.mark.parametrize("rel", sorted(CONTEXT_FILES))
    def test_context_file_carries_no_volatile_token(self, rel: str) -> None:
        text = _read(rel)
        offenders = [
            f"{label}: {match.group(0)!r}"
            for label, pattern in VOLATILE_PATTERNS.items()
            for match in [pattern.search(text)]
            if match
        ]
        assert not offenders, (
            f"{rel} contains a token that can differ between two composions of "
            f"the same head: {offenders}. Every such token forces a second cold "
            "head write and forfeits the 4.20x Anthropic cache advantage."
        )

    def test_delegate_preamble_carries_no_volatile_token(self) -> None:
        text = _delegate_preamble()
        offenders = [
            f"{label}: {match.group(0)!r}"
            for label, pattern in VOLATILE_PATTERNS.items()
            for match in [pattern.search(text)]
            if match
        ]
        assert not offenders, f"delegate preamble is not byte-stable: {offenders}"

    def test_the_owned_head_composes_identically_twice(self) -> None:
        """Composition is a pure function of the sources, not of the clock."""
        assert _delegate_preamble() == _delegate_preamble()
        for rel in CONTEXT_FILES:
            assert _read(rel) == _read(rel)


class TestGuardrailCTaskSuccessNonInferiority:
    """(c) The frozen -10 pp margin, cited from 5zp -- never re-measured here.

    SPEC.md 9.1 freezes the profile and 5zp established that moving it makes
    runs incomparable to every published bound. This pins the citation so a
    later edit cannot loosen the margin or replace the lower bound with one
    that no longer clears it.
    """

    def test_cited_quality_lower_bound_clears_the_frozen_margin(self) -> None:
        assert CITED_QUALITY_LOWER_BOUND_PP > NON_INFERIORITY_MARGIN_PP, (
            f"The cited one-sided 95% LB ({CITED_QUALITY_LOWER_BOUND_PP} pp) no "
            f"longer clears the frozen margin ({NON_INFERIORITY_MARGIN_PP} pp). "
            "Shipping the lean head is not justified without a fresh "
            "measurement -- and the margin is frozen, so it is the LB that must "
            "move, not the margin."
        )

    def test_the_frozen_margin_has_not_been_loosened(self) -> None:
        assert NON_INFERIORITY_MARGIN_PP == -10.0

    def test_the_cited_cost_effect_excludes_zero(self) -> None:
        """The reason this change ships at all: the CI does not straddle zero."""
        low, high = CITED_COST_CI_PCT
        assert low < CITED_COST_DELTA_PCT < high
        assert high < 0, (
            f"The cited 95% CI {CITED_COST_CI_PCT} no longer excludes zero; the "
            "SHIP rule that fired for this change does not hold."
        )


class TestFidelityBeatsCompression:
    """No rule, constraint, command or pointer was traded away for bytes.

    The whole-head fidelity diff (all 23 targets, in-repo and out) is recorded
    in `docs/lanes/zc6t-lean-head-ship/FINDINGS.md`. This pins the in-repo half
    so a future compression pass cannot quietly drop one.
    """

    @pytest.mark.parametrize("rel", sorted(REQUIRED_RULES))
    def test_every_required_rule_survived(self, rel: str) -> None:
        # Case-insensitive on purpose: the invariant is that the RULE is still
        # stated, not that a sentence still starts with the same capital. A
        # case-sensitive match reported two false losses against the stock text
        # ("Never push downstream..." vs "never push downstream...") during the
        # recorded fail-before run, which is exactly the kind of noise that
        # teaches a reader to skim past a real one.
        text = _read(rel).casefold()
        missing = [rule for rule in REQUIRED_RULES[rel] if rule.casefold() not in text]
        assert not missing, (
            f"{rel} lost these rules/commands/pointers: {missing}. Fidelity "
            "beats compression at every point of conflict -- restore the text "
            "and raise the char budget rather than shipping the saving."
        )
