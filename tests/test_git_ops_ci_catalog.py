"""Git-ops CI completion instructions reach the intended public catalogs."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_foundation.io.frontmatter import parse_frontmatter
from amplifier_foundation.modules.activator import ModuleActivator
from amplifier_foundation.registry import BundleRegistry
from amplifier_module_tool_delegate import DelegateTool

REPO_ROOT = Path(__file__).parent.parent
CATALOG_ROOT = Path(os.environ.get("GIT_OPS_CATALOG_ROOT", REPO_ROOT)).resolve()

SHARED_CI_COMPLETION_MARKERS = (
    "Post-Push / Post-PR CI Completion",
    'pushed_sha="$(git rev-parse HEAD)"',
    'gh workflow list',
    'gh run list --commit "$pushed_sha" --limit 100',
    "this ref is applicable",
    "pass/fail outcome",
    "gh run view <run-id> --log-failed",
    "**No CI**",
    "no CI on this repo",
    "**Not triggered**",
    "**Pending**",
    "**Timeout**",
    "**Failure**",
    "**Policy-only checks**",
    "successful workflow-trigger inspection",
    "commit check-run/status inspection",
    "PR checks when applicable",
    "only after successful discovery finds no workflows or checks",
    "matching workflow has no run yet",
    "only CLA/policy checks",
    "**Unable to verify**",
    "Discovery/API/auth errors",
    "never **No CI** or green",
)

CATALOG_CI_MARKERS = {
    "foundation:git-ops": ("default 20-minute cap",),
    "anchors:git-ops": (
        "Poll only when the caller expressly asks to wait",
        "gives a time budget",
        "never beyond a 20-minute cap",
        "requested wait budget ends",
    ),
}

CATALOG_ROUTING_MARKERS = {
    "foundation:git-ops": (),
    "anchors:git-ops": (
        "Git and GitHub mutations",
        "local read-only git status/diff/log",
        "work/lane/highway administration",
        "Delegate one requested Git lifecycle, not each subcommand.",
    ),
}

CATALOGS = (
    (
        "foundation:git-ops",
        ".",
        Path("agents/git-ops.md"),
        Path("bundles/anchors/agents/git-ops.md"),
    ),
    (
        "anchors:git-ops",
        "bundles/anchors",
        Path("bundles/anchors/agents/git-ops.md"),
        Path("agents/git-ops.md"),
    ),
)


def _agent_source(path: Path) -> tuple[str, str]:
    """Return the catalog description and body from one real agent file."""
    frontmatter, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    return frontmatter["meta"]["description"], body.strip()


async def _prepared_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, subdirectory: str
):
    """Load source content through BundleRegistry and prepare its mount plan.

    Module activation is outside this structural check; stubbing it leaves the
    production loading, source resolution, metadata loading, and prepare path
    intact without downloading any module.
    """
    uri = CATALOG_ROOT.as_uri()
    if subdirectory != ".":
        uri = f"{uri}#subdirectory={subdirectory}"

    registry = BundleRegistry(home=tmp_path / "home")
    bundle = await registry._load_single(uri, auto_include=False)
    bundle.load_agent_metadata()

    monkeypatch.setattr(ModuleActivator, "activate_all", AsyncMock(return_value={}))
    monkeypatch.setattr(ModuleActivator, "finalize", lambda self: None)
    return await bundle.prepare(install_deps=False)


def _delegate_description(mount_plan: dict) -> str:
    """Render the actual DelegateTool catalog from a prepared mount plan."""
    coordinator = MagicMock()
    coordinator.session_id = "git-ops-catalog-test"
    coordinator.config = mount_plan
    coordinator.session_state = {}
    coordinator._tool_dispatch_context = {}
    coordinator.get_capability = lambda _name: None
    coordinator.get = MagicMock(return_value=None)
    return DelegateTool(
        coordinator, {"features": {}, "settings": {"exclude_tools": []}}
    ).description


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("catalog_name", "subdirectory", "source_relative", "other_source_relative"),
    CATALOGS,
    ids=("foundation", "anchors"),
)
async def test_prepared_catalog_uses_its_own_git_ops_body_and_ci_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalog_name: str,
    subdirectory: str,
    source_relative: Path,
    other_source_relative: Path,
) -> None:
    """Each public catalog loads its own body, then DelegateTool renders it."""
    expected_description, expected_body = _agent_source(CATALOG_ROOT / source_relative)
    _other_description, other_body = _agent_source(CATALOG_ROOT / other_source_relative)

    prepared = await _prepared_catalog(tmp_path, monkeypatch, subdirectory)
    agent = prepared.mount_plan["agents"][catalog_name]

    assert agent["instruction"] == expected_body
    assert agent["instruction"] != other_body
    assert other_body not in agent["instruction"]
    normalized_instruction = " ".join(agent["instruction"].split())
    missing_ci_markers = [
        marker
        for marker in (
            *SHARED_CI_COMPLETION_MARKERS,
            *CATALOG_CI_MARKERS[catalog_name],
        )
        if marker not in normalized_instruction
    ]
    assert not missing_ci_markers, (
        f"missing CI completion markers: {missing_ci_markers}"
    )

    description = _delegate_description(prepared.mount_plan)
    assert f"  - {catalog_name}: {expected_description}" in description
    missing_routing_markers = [
        marker
        for marker in CATALOG_ROUTING_MARKERS[catalog_name]
        if marker not in description
    ]
    assert not missing_routing_markers, (
        f"missing routing markers: {missing_routing_markers}"
    )


@pytest.mark.asyncio
async def test_anchors_prepared_catalog_excludes_foundation_git_ops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anchors' independently live catalog does not inherit Foundation's one."""
    prepared = await _prepared_catalog(tmp_path, monkeypatch, "bundles/anchors")

    assert "anchors:git-ops" in prepared.mount_plan["agents"]
    assert "foundation:git-ops" not in prepared.mount_plan["agents"]
    assert "  - foundation:git-ops:" not in _delegate_description(prepared.mount_plan)