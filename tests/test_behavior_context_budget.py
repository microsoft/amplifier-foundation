"""Guard the delegation awareness/depth split, and the estimator that measures it.

`behaviors/agents.yaml` and `behaviors/tasks.yaml` used to `context.include` the full
`delegation-instructions.md` + `multi-agent-patterns.md` — **6,276 tokens on disk (len//4)**,
loaded into the root system prompt of every foundation-root session.

The validator did not catch it. Its behavior-hygiene Rule 4 resolved an include only two
ways: a leading `@` was charged a flat 500, anything else was tried as a relative path. A
`<namespace>:<path>` reference — the form these behaviors actually use — matched neither, so
it fell through to the 500-token default. Two includes × 500 = **exactly 1000**, against a
`> 1000` ERROR gate: 6,276 real tokens were reported as 1000 and graded WARNING, one token
under the ERROR that was true.

These tests measure the real files, so the budget cannot be satisfied by an estimator's
blind spot.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
BEHAVIORS = REPO_ROOT / "behaviors"

# The validator's own unit (`len(content) // 4`), so this guard and
# `recipes/validate-bundle-repo.yaml` cannot disagree about what a token is.
ERROR_BUDGET = 1000  # validator: context_tokens_excessive
AWARENESS_BUDGET = 500  # validator: lightweight awareness only

CORE = "context/agents/delegation-core.md"
DEPTH = "context/agents/delegation-depth.md"
PATTERNS = "context/agents/multi-agent-patterns.md"

# The agents the split routes depth to, and the property that earns each one.
DEPTH_AGENTS = {
    "agents/foundation-expert.md": "the only agent that declares tool-delegate",
    "agents/session-analyst.md": "the only agent that resumes sessions by session_id",
}


def _estimate_tokens(content: str) -> int:
    return len(content) // 4


def _resolve_include(behavior_path: Path, ref: str) -> Path | None:
    """Resolve an include the way validate-bundle-repo v3.14.0 does."""
    ref = ref[1:] if ref.startswith("@") else ref
    candidates = [ref]
    if ":" in ref and "//" not in ref:
        namespace, _, rel = ref.partition(":")
        if namespace and rel and "/" not in namespace:
            candidates.append(rel)
    for rel in candidates:
        for base in (behavior_path.parent, REPO_ROOT):
            candidate = base / rel
            if candidate.is_file():
                return candidate
    return None


def _context_includes(behavior_path: Path) -> list[str]:
    data = yaml.safe_load(behavior_path.read_text(encoding="utf-8")) or {}
    context = data.get("context")
    if not isinstance(context, dict):
        return []
    return [ref for ref in context.get("include", []) if isinstance(ref, str)]


def _behavior_context_tokens(behavior_path: Path) -> tuple[int, list[str]]:
    total = 0
    unresolved: list[str] = []
    for ref in _context_includes(behavior_path):
        resolved = _resolve_include(behavior_path, ref)
        if resolved is None:
            total += 500
            unresolved.append(ref)
        else:
            total += _estimate_tokens(resolved.read_text(encoding="utf-8"))
    return total, unresolved


BEHAVIOR_FILES = sorted(BEHAVIORS.glob("*.yaml"))


# =============================================================================
# The files
# =============================================================================


def test_core_file_exists_and_is_awareness_sized() -> None:
    core = REPO_ROOT / CORE
    assert core.is_file(), f"{CORE} must exist -- it is what the behaviors load"
    tokens = _estimate_tokens(core.read_text(encoding="utf-8"))
    assert tokens < AWARENESS_BUDGET, (
        f"{CORE} is {tokens} tokens (len//4); the awareness budget is "
        f"<{AWARENESS_BUDGET}. Move material to {DEPTH}, do not raise this number."
    )


def test_depth_files_exist() -> None:
    for rel in (DEPTH, PATTERNS):
        assert (REPO_ROOT / rel).is_file(), f"{rel} must exist -- agents @-mention it"


# =============================================================================
# The behaviors
# =============================================================================


@pytest.mark.parametrize("behavior", BEHAVIOR_FILES, ids=lambda p: p.name)
def test_no_behavior_exceeds_the_error_budget(behavior: Path) -> None:
    """Every include is measured from disk, not charged a flat guess."""
    tokens, unresolved = _behavior_context_tokens(behavior)
    assert tokens <= ERROR_BUDGET, (
        f"{behavior.name} context.include is {tokens} tokens (len//4), over the "
        f"{ERROR_BUDGET}-token ERROR gate. Unresolved (flat-500) includes: {unresolved}"
    )


@pytest.mark.parametrize("behavior", ["agents.yaml", "tasks.yaml"])
def test_delegation_behaviors_carry_only_the_core(behavior: str) -> None:
    path = BEHAVIORS / behavior
    includes = _context_includes(path)
    assert includes == [f"foundation:{CORE}"], (
        f"behaviors/{behavior} must load the awareness core and nothing else; got {includes}"
    )
    tokens, unresolved = _behavior_context_tokens(path)
    assert not unresolved, f"behaviors/{behavior} has unresolvable includes: {unresolved}"
    assert tokens < AWARENESS_BUDGET, f"behaviors/{behavior} is {tokens} tokens (len//4)"


@pytest.mark.parametrize("behavior", BEHAVIOR_FILES, ids=lambda p: p.name)
def test_no_behavior_loads_a_depth_file(behavior: Path) -> None:
    """Depth belongs in agent bodies. A behavior loading it puts it in every root session."""
    for ref in _context_includes(behavior):
        for depth_rel in (DEPTH, PATTERNS):
            assert depth_rel not in ref, (
                f"{behavior.name} loads {depth_rel} into the root prompt. "
                f"@-mention it from the agent bodies that need it instead."
            )


# =============================================================================
# The routing -- depth must actually be reachable
# =============================================================================


@pytest.mark.parametrize("agent_rel,why", sorted(DEPTH_AGENTS.items()))
def test_depth_is_reachable_from_the_agents_that_need_it(agent_rel: str, why: str) -> None:
    body = (REPO_ROOT / agent_rel).read_text(encoding="utf-8")
    assert f"@foundation:{DEPTH}" in body, (
        f"{agent_rel} ({why}) must @-mention {DEPTH}; otherwise the depth material "
        f"is unreachable from any session after the split."
    )


def test_multi_agent_patterns_is_reachable_from_the_delegating_agent() -> None:
    body = (REPO_ROOT / "agents/foundation-expert.md").read_text(encoding="utf-8")
    assert f"@foundation:{PATTERNS}" in body


def test_the_only_agent_declaring_tool_delegate_is_the_one_we_routed_depth_to() -> None:
    """If another agent gains tool-delegate, this split's routing needs revisiting."""
    declaring = sorted(
        p.name
        for p in (REPO_ROOT / "agents").glob("*.md")
        if "tool-delegate" in p.read_text(encoding="utf-8")
    )
    assert declaring == ["foundation-expert.md"], (
        f"agents declaring tool-delegate changed to {declaring}; the delegation-depth "
        f"routing in behaviors/agents.yaml assumes foundation-expert is the only one."
    )


# =============================================================================
# The estimator -- the guard and the validator must measure the same way
# =============================================================================


def test_validator_resolves_namespace_qualified_includes() -> None:
    recipe = (REPO_ROOT / "recipes" / "validate-bundle-repo.yaml").read_text(encoding="utf-8")
    assert "_candidate_relpaths" in recipe, (
        "validate-bundle-repo must resolve `<namespace>:<path>` includes; without it a "
        "6,276-token behavior is reported as 1000 and graded WARNING."
    )
    assert "context_unresolved_includes" in recipe, (
        "an include that still falls back to the flat 500 must be recorded, not folded "
        "silently into a number that reads as measured."
    )
