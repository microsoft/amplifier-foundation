"""Integration tests for BundleConfigurator with real Foundation bundles.

All tests are marked with ``pytestmark = pytest.mark.integration`` and skip by
default in normal ``pytest`` runs.  Run them explicitly with::

    pytest -m integration

The lean bundle path comes from the ``lean_bundle_path`` session fixture in
``conftest.py``; individual tests are skipped automatically when the bundle is
absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amplifier_configurator import BundleConfigurator
from amplifier_configurator.dependencies import REQUIRED_PARTS
from amplifier_configurator.models import PartKind

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# 1. Load
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_lean_bundle(lean_bundle_path: Path) -> None:
    """Loading the lean bundle produces a configurator with root_name 'lean' and behaviors."""
    cfg = await BundleConfigurator.load(str(lean_bundle_path))

    assert cfg.provenance.root_name == "lean"
    assert len(cfg.provenance.behaviors) > 0


# ---------------------------------------------------------------------------
# 2. Tool names
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lean_bundle_has_tools(lean_bundle_path: Path) -> None:
    """tool-bash is present in the lean bundle's tool parts."""
    cfg = await BundleConfigurator.load(str(lean_bundle_path))

    tool_names = [p.name for p in cfg.list_parts(kind=PartKind.TOOL)]
    assert "tool-bash" in tool_names


# ---------------------------------------------------------------------------
# 3. Token count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lean_bundle_token_count(lean_bundle_path: Path) -> None:
    """Total token count for the lean bundle is positive."""
    cfg = await BundleConfigurator.load(str(lean_bundle_path))

    assert cfg.total_tokens() > 0


# ---------------------------------------------------------------------------
# 4. Tokens by behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lean_bundle_tokens_by_behavior(lean_bundle_path: Path) -> None:
    """tokens_by_behavior returns a non-empty mapping."""
    cfg = await BundleConfigurator.load(str(lean_bundle_path))

    breakdown = cfg.tokens_by_behavior()
    assert len(breakdown) > 0


# ---------------------------------------------------------------------------
# 5. Save round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lean_bundle_save_roundtrip(
    lean_bundle_path: Path, tmp_path: Path
) -> None:
    """Load → save → verify file on disk contains expected markers."""
    cfg = await BundleConfigurator.load(str(lean_bundle_path))
    dest = tmp_path / "saved_bundle.md"

    cfg.save(dest)

    assert dest.exists()
    content = dest.read_text()
    assert "---" in content
    assert "lean" in content


# ---------------------------------------------------------------------------
# 6. Remove behavior and diff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lean_bundle_remove_and_diff(lean_bundle_path: Path) -> None:
    """Remove a non-critical behavior and verify it appears in the diff."""
    cfg = await BundleConfigurator.load(str(lean_bundle_path))

    # Find a behavior whose parts don't include any required parts.
    removable_name: str | None = None
    removable_uri: str | None = None
    for uri, beh in cfg.provenance.behaviors.items():
        part_names = {p.name for p in beh.parts}
        if not (part_names & REQUIRED_PARTS):
            removable_name = beh.name
            removable_uri = uri
            break

    if removable_name is None:
        pytest.skip("No safely removable behavior found in the lean bundle")

    new_cfg = cfg.remove_behavior(removable_name)
    diff = cfg.diff(new_cfg)

    assert removable_uri in diff.removed_behaviors
