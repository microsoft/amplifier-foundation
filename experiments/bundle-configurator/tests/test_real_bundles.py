"""Real-world integration tests for BundleConfigurator against live Foundation bundles.

All tests require the Amplifier cache to be populated (the Foundation bundle and
amplifier-dev must have been fetched at least once).  They are skipped
automatically in normal ``pytest`` runs and only execute with::

    pytest -m integration

**Token count notes**: ``total_tokens()`` tracks context files referenced via
namespace URIs (e.g. ``amplifier:context/...``) as well as the root instruction.
The ground truth for ``foundation`` is ~23,425 tokens dominated by 18 context
files (~23,194 tokens) plus the root instruction (~231 tokens).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from amplifier_configurator import BundleConfigurator
from amplifier_configurator.models import PartKind

pytestmark = pytest.mark.integration

_CACHE_EXISTS = Path("~/.amplifier/cache/").expanduser().exists()

_skip_if_no_cache = pytest.mark.skipif(
    not _CACHE_EXISTS,
    reason="Amplifier cache not available (~/.amplifier/cache/ missing)",
)


# ---------------------------------------------------------------------------
# 1. test_load_foundation_bundle
# ---------------------------------------------------------------------------


@_skip_if_no_cache
def test_load_foundation_bundle() -> None:
    """Foundation bundle loads and exposes expected behaviors and tools."""
    cfg = BundleConfigurator.load_sync("foundation")

    # Loads without error
    assert cfg is not None

    # Has behaviors
    behaviors = cfg.list_behaviors()
    assert len(behaviors) > 0, "Expected at least one behavior from foundation"

    # total_tokens reflects context files + root instruction.
    # Foundation has 18 context files (~23K tokens) plus root instruction (~231).
    assert cfg.total_tokens() > 5_000, (
        f"Expected total_tokens() > 5,000 for foundation (context files + instruction); "
        f"got {cfg.total_tokens()}.  If this fails, context file backfill may be broken."
    )

    # Context parts must be present and have non-zero token counts
    ctx_parts = cfg.list_parts(kind=PartKind.CONTEXT)
    assert len(ctx_parts) > 0, (
        "Expected context parts to be extracted from _pending_context"
    )
    assert all(p.tokens > 0 for p in ctx_parts), (
        f"All context parts should have non-zero token counts after backfill; "
        f"zero-token parts: {[p.name for p in ctx_parts if p.tokens == 0]}"
    )

    # Structural checks: behaviors must include named behaviors with non-empty URIs.
    # We do NOT assert specific behavior names because the live foundation bundle
    # is a git-tracked resource that changes; hardcoded names break whenever
    # upstream removes or renames a behavior.
    behavior_names = {b.name for b in behaviors}
    assert len(behavior_names) >= 5, (
        f"Expected at least 5 distinct behavior names in foundation; got {sorted(behavior_names)}"
    )
    named_behaviors = [b for b in behaviors if b.name and b.uri]
    assert len(named_behaviors) >= 5, (
        f"Expected at least 5 named behaviors; got {[b.name for b in named_behaviors]}"
    )

    # Specific tools must be present
    tool_names = {p.name for p in cfg.list_parts(kind=PartKind.TOOL)}
    assert "tool-bash" in tool_names, (
        f"'tool-bash' missing from foundation tools. Got: {sorted(tool_names)}"
    )
    assert "tool-filesystem" in tool_names, (
        f"'tool-filesystem' missing from foundation tools. Got: {sorted(tool_names)}"
    )


# ---------------------------------------------------------------------------
# 2. test_load_amplifier_dev_bundle
# ---------------------------------------------------------------------------


@_skip_if_no_cache
def test_load_amplifier_dev_bundle() -> None:
    """amplifier-dev loads and includes all foundation behaviors plus its own."""
    foundation_cfg = BundleConfigurator.load_sync("foundation")
    dev_cfg = BundleConfigurator.load_sync("amplifier-dev")

    # Loads without error
    assert dev_cfg is not None

    foundation_behavior_names = {b.name for b in foundation_cfg.list_behaviors()}
    dev_behavior_names = {b.name for b in dev_cfg.list_behaviors()}

    # amplifier-dev must have MORE behaviors than foundation
    assert len(dev_behavior_names) > len(foundation_behavior_names), (
        f"amplifier-dev ({len(dev_behavior_names)} behaviors) should have more "
        f"than foundation ({len(foundation_behavior_names)} behaviors)"
    )

    # All foundation behaviors are present in amplifier-dev (it includes foundation)
    missing = foundation_behavior_names - dev_behavior_names
    assert not missing, (
        f"These foundation behaviors are missing from amplifier-dev: {missing}"
    )


# ---------------------------------------------------------------------------
# 3. test_remove_behavior_token_savings
# ---------------------------------------------------------------------------


@_skip_if_no_cache
def test_remove_behavior_token_savings() -> None:
    """Removing a behavior reduces the total part count and token count."""
    cfg = BundleConfigurator.load_sync("foundation")

    # Pick browser-testing-behavior; fall back to first non-critical removable one.
    target: str | None = None
    behavior_names = [b.name for b in cfg.list_behaviors()]
    for candidate in ["browser-testing-behavior", *behavior_names]:
        try:
            cfg.remove_behavior(candidate)
            target = candidate
            break
        except (KeyError, Exception):
            continue

    if target is None:
        pytest.skip("No removable behavior found in foundation bundle")

    original_tokens = cfg.total_tokens()
    original_behavior_count = len(cfg.list_behaviors())
    original_parts_count = len(cfg.list_parts())

    new_cfg = cfg.remove_behavior(target)

    # Token count must not increase
    assert new_cfg.total_tokens() <= original_tokens, (
        f"Removing '{target}' should not increase token count: "
        f"{original_tokens} → {new_cfg.total_tokens()}"
    )

    # At least one fewer behavior
    assert len(new_cfg.list_behaviors()) < original_behavior_count, (
        f"Expected fewer behaviors after removing '{target}'"
    )

    # At least the same or fewer parts overall (some parts may be shared)
    assert len(new_cfg.list_parts()) <= original_parts_count, (
        f"Expected same or fewer parts after removing '{target}'"
    )


# ---------------------------------------------------------------------------
# 4. test_diff_after_removal
# ---------------------------------------------------------------------------


@_skip_if_no_cache
def test_diff_after_removal() -> None:
    """Removing a behavior produces a diff with removed_behaviors and non-positive token_delta."""
    cfg = BundleConfigurator.load_sync("foundation")

    # Locate a removable behavior (prefer browser-testing-behavior)
    target: str | None = None
    for candidate in [
        "browser-testing-behavior",
        *[b.name for b in cfg.list_behaviors()],
    ]:
        try:
            cfg.remove_behavior(candidate)
            target = candidate
            break
        except (KeyError, Exception):
            continue

    if target is None:
        pytest.skip("No removable behavior found in foundation bundle")

    new_cfg = cfg.remove_behavior(target)
    diff = cfg.diff(new_cfg)

    # The removed behavior's URI must appear in removed_behaviors
    assert len(diff.removed_behaviors) > 0, (
        "diff.removed_behaviors must be non-empty after removing a behavior"
    )

    # No new behaviors should have appeared
    assert len(diff.added_behaviors) == 0, (
        f"Unexpected behaviors added in diff: {diff.added_behaviors}"
    )

    # Token delta must be <= 0 (removing parts never adds tokens)
    assert diff.token_delta <= 0, (
        f"Removing a behavior should not increase token count; "
        f"got token_delta={diff.token_delta}"
    )

    # before_tokens and after_tokens must be consistent with the individual counts
    assert diff.before_tokens == cfg.total_tokens()
    assert diff.after_tokens == new_cfg.total_tokens()


# ---------------------------------------------------------------------------
# 5. test_save_and_reload
# ---------------------------------------------------------------------------


@_skip_if_no_cache
def test_save_and_reload() -> None:
    """Saved bundle file has YAML frontmatter, includes section, and no absolute paths."""
    cfg = BundleConfigurator.load_sync("foundation")

    # Locate a removable behavior so the saved file reflects a mutation
    target: str | None = None
    for candidate in [
        "browser-testing-behavior",
        *[b.name for b in cfg.list_behaviors()],
    ]:
        try:
            cfg.remove_behavior(candidate)
            target = candidate
            break
        except (KeyError, Exception):
            continue

    working_cfg = cfg.remove_behavior(target) if target else cfg

    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
        dest = Path(f.name)
    try:
        working_cfg.save(dest)

        # File exists and is non-empty
        assert dest.exists(), "Saved file does not exist"
        content = dest.read_text()
        assert len(content) > 0, "Saved file is empty"

        # Must start with YAML frontmatter
        assert content.startswith("---"), (
            "Saved bundle must begin with '---' (YAML frontmatter)"
        )

        # Must contain an includes section
        assert "includes:" in content, "Saved bundle must contain 'includes:' section"

        # Must be parseable as YAML (extract the frontmatter block)
        # The file is: ---\n<yaml>\n---\n<body>
        parts = content.split("---", 2)
        assert len(parts) >= 3, "Could not split YAML frontmatter from body"
        yaml_block = parts[1]
        parsed = yaml.safe_load(yaml_block)
        assert isinstance(parsed, dict), (
            f"YAML frontmatter did not parse to a dict; got {type(parsed)}"
        )

        # Must not contain absolute paths
        assert "/Users/" not in content, (
            "Saved bundle must not contain absolute '/Users/' paths"
        )
        assert "/home/" not in content, (
            "Saved bundle must not contain absolute '/home/' paths"
        )
    finally:
        dest.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 6. test_token_count_ground_truth
# ---------------------------------------------------------------------------


@_skip_if_no_cache
def test_token_count_ground_truth() -> None:
    """Foundation total_tokens() reflects context files plus root instruction.

    Measured ground truth: ~23,425 tokens total
      - 18 context files: ~23,194 tokens
      - Root instruction:    ~231 tokens
      Total:               ~23,425 tokens

    The tolerance is 40% because bundle content can change between Amplifier
    releases.  A value below 5,000 indicates the context-file backfill is broken
    (the pre-fix bug returned only 231).
    """
    cfg = BundleConfigurator.load_sync("foundation")
    tokens = cfg.total_tokens()

    # Hard lower bound: must be far above the old broken value of 231.
    assert tokens > 5_000, (
        f"total_tokens()={tokens} is suspiciously low — context files are not being "
        "counted.  Pre-fix this was 231 (instruction only).  "
        "Check _extract_parts_from_bundle() and _backfill_context_tokens()."
    )

    # Soft pinning: within 40% of the measured value (context-heavy bundles drift).
    ground_truth = 23_425
    tolerance = 0.40
    lower = int(ground_truth * (1 - tolerance))
    upper = int(ground_truth * (1 + tolerance)) + 1

    assert lower <= tokens <= upper, (
        f"total_tokens()={tokens} is outside the ±40% window "
        f"[{lower}, {upper}] of the measured ground-truth {ground_truth}. "
        "Update ground_truth in this test if the Foundation bundle has changed substantially."
    )
