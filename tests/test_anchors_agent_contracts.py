"""Runtime contract checks for the shared Anchors spawned-agent baseline."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from amplifier_foundation.bundle import Bundle, PreparedBundle
from amplifier_foundation.mentions.loader import expand_mentions_in_instruction
from amplifier_foundation.mentions.parser import parse_mentions
from amplifier_foundation.mentions.resolver import BaseMentionResolver
from tests.agent_catalog_support import prepare_agent_catalog, render_delegate_catalog

REPO_ROOT = Path(__file__).parent.parent
ANCHORS_DIR = REPO_ROOT / "bundles" / "anchors"
AMP_DEV_DIR = REPO_ROOT / "bundles" / "anchors-amp-dev"
BASELINE_PATH = ANCHORS_DIR / "context" / "agent-baseline.md"
BASELINE_MENTION = "@anchors:context/agent-baseline.md"

ANCHORS_AGENTS = (
    "architect",
    "builder",
    "debugger",
    "explorer",
    "git-ops",
    "researcher",
)
ALL_AGENTS = (*ANCHORS_AGENTS, "amplifier-dev-expert")


async def _prepared_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bundle_dir: Path
) -> tuple[PreparedBundle, Bundle]:
    """Use the production loader and prepare path without external module fetches.

    The catalog source is local and each loaded bundle registers its own local
    namespace root. Includes stay disabled because these checks target each
    catalog's source body, while avoiding network access to its runtime modules.
    """
    return await prepare_agent_catalog(
        tmp_path, monkeypatch, bundle_dir.joinpath("bundle.md").as_uri()
    )


async def _catalogs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[
    dict[str, dict],
    dict[str, Bundle],
    dict[str, PreparedBundle],
]:
    """Load both public catalogs and retain the real namespace source mappings."""
    anchors_prepared, anchors = await _prepared_catalog(tmp_path, monkeypatch, ANCHORS_DIR)
    amp_dev_prepared, amp_dev = await _prepared_catalog(tmp_path, monkeypatch, AMP_DEV_DIR)
    agents = {
        **anchors_prepared.mount_plan["agents"],
        **amp_dev_prepared.mount_plan["agents"],
    }
    return (
        agents,
        {"anchors": anchors, "anchors-amp-dev": amp_dev},
        {"anchors": anchors_prepared, "anchors-amp-dev": amp_dev_prepared},
    )


@pytest.mark.asyncio
async def test_every_named_agent_body_points_once_to_the_shared_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public prepared catalogs carry one pointer, not seven copies."""
    agents, _namespace_bundles, _prepared_catalogs = await _catalogs(
        tmp_path, monkeypatch
    )

    assert set(agents) == {
        *(f"anchors:{name}" for name in ANCHORS_AGENTS),
        "anchors-amp-dev:amplifier-dev-expert",
    }
    for name in ALL_AGENTS:
        catalog_name = (
            f"anchors:{name}"
            if name in ANCHORS_AGENTS
            else "anchors-amp-dev:amplifier-dev-expert"
        )
        assert agents[catalog_name]["instruction"].count(BASELINE_MENTION) == 1


@pytest.mark.asyncio
async def test_real_mention_loader_expands_the_baseline_once_per_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production mention expansion receives the one shared baseline on spawn."""
    agents, namespace_bundles, _prepared_catalogs = await _catalogs(
        tmp_path, monkeypatch
    )
    baseline = BASELINE_PATH.read_text(encoding="utf-8")
    resolver = BaseMentionResolver(bundles=namespace_bundles, base_path=tmp_path)

    for name in ALL_AGENTS:
        catalog_name = (
            f"anchors:{name}"
            if name in ANCHORS_AGENTS
            else "anchors-amp-dev:amplifier-dev-expert"
        )
        expanded = await expand_mentions_in_instruction(
            agents[catalog_name]["instruction"], resolver=resolver
        )
        assert expanded.count(baseline) == 1, catalog_name


def test_baseline_has_no_eager_nested_mentions() -> None:
    """The shared file is a leaf, so loading it cannot pull in another prompt."""
    assert parse_mentions(BASELINE_PATH.read_text(encoding="utf-8")) == []


def test_baseline_preserves_the_approved_operating_rules() -> None:
    """Content reaching an agent must still contain the essential safeguards."""
    baseline = " ".join(BASELINE_PATH.read_text(encoding="utf-8").split())
    for marker in (
        "Investigate before acting",
        "smallest change justified by the task and evidence",
        "caller-authorized scope",
        "Report verified evidence honestly",
        "say blocked or not verified",
        "defensive-security work only; refuse malicious work and protect secrets",
        "discover applicable user, workspace, repository, and subdirectory conventions",
        "`AGENTS.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `CONTRIBUTING.md`, and `README.md`",
        "discover phase-relevant contextual and verification rules",
        "Do not assume a parent’s instructions are inherited",
        "Re-read applicable conventions when the work phase changes",
    ):
        assert marker in baseline


@pytest.mark.asyncio
async def test_child_prompt_does_not_eagerly_load_local_sentinels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The composed spawned-agent prompt excludes parent AGENTS and SCRATCH files."""
    agents, _namespace_bundles, prepared_catalogs = await _catalogs(
        tmp_path, monkeypatch
    )
    root_instructions = [
        prepared_catalogs["anchors"].bundle.instruction,
        prepared_catalogs["anchors-amp-dev"].bundle.instruction,
    ]
    assert all(BASELINE_MENTION not in instruction for instruction in root_instructions)

    agents_sentinel = "fixture AGENTS sentinel must not be loaded"
    scratch_sentinel = "fixture SCRATCH sentinel must not be loaded"
    (tmp_path / "AGENTS.md").write_text(
        agents_sentinel + "\n@SCRATCH.md\n", encoding="utf-8"
    )
    (tmp_path / "SCRATCH.md").write_text(scratch_sentinel, encoding="utf-8")
    child_bundle = Bundle(
        name="anchors:builder", instruction=agents["anchors:builder"]["instruction"]
    )
    child_prepared = await prepared_catalogs["anchors"].bundle.compose(
        child_bundle
    ).prepare(install_deps=False)
    child_session = MagicMock()
    child_session.coordinator.hooks.emit = AsyncMock()
    child_prompt = await child_prepared.create_system_prompt_factory(
        child_session, session_cwd=tmp_path
    )()

    assert agents_sentinel not in child_prompt
    assert scratch_sentinel not in child_prompt
    assert child_prompt.count(BASELINE_PATH.read_text(encoding="utf-8")) == 1


@pytest.mark.asyncio
async def test_amp_dev_authoring_references_are_conditional_not_eager_docs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The expert retains three ecosystem references and adds only soft guidance."""
    agents, _namespace_bundles, _prepared_catalogs = await _catalogs(
        tmp_path, monkeypatch
    )
    instruction = agents["anchors-amp-dev:amplifier-dev-expert"]["instruction"]

    for reference in (
        "`foundation:docs/BUNDLE_GUIDE.md`",
        "`foundation:docs/AGENT_AUTHORING.md`",
        "`foundation:context/shared/description-authoring-principles.md`",
    ):
        assert reference in instruction
    assert parse_mentions(instruction) == [
        "@foundation:context/amplifier-dev/ecosystem-map.md",
        "@foundation:context/amplifier-dev/dev-workflows.md",
        "@foundation:context/amplifier-dev/testing-patterns.md",
        BASELINE_MENTION,
    ]


@pytest.mark.asyncio
async def test_delegate_catalog_descriptions_preserve_architect_and_debugger_routing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Routing remains visible in the actual DelegateTool catalog."""
    prepared, _anchors = await _prepared_catalog(tmp_path, monkeypatch, ANCHORS_DIR)
    catalog = render_delegate_catalog(
        prepared.mount_plan, session_id="anchors-contract-test"
    )

    for marker in (
        "DO NOT USE WHEN: a clear spec already exists and code just needs writing -- use builder.",
        "the cause and required change are already understood -- use builder;",
        "healthy deterministic job only needs running or monitoring",
        "caller or operations workflow owns it.",
    ):
        assert marker in catalog


@pytest.mark.asyncio
async def test_approved_agent_body_contract_markers_survive_normalized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whitespace changes do not weaken the builder, debugger, or architect rules."""
    agents, _namespace_bundles, _prepared_catalogs = await _catalogs(
        tmp_path, monkeypatch
    )
    markers = {
        "anchors:architect": (
            "REVIEW mode reports findings and does not modify reviewed product code, "
            "configuration, or documentation.",
            "Explicitly requested design or review artifacts are permitted.",
            "Reviews must cite specific `file_path:line_number` evidence read via a tool "
            "call in THIS session.",
        ),
        "anchors:builder": (
            "Write tests alongside implementation unless the caller explicitly excludes them.",
            "Run tests before returning unless execution is prohibited, blocked, or explicitly parent-owned.",
            "Do not invent pass/fail results.",
            "Report NOT RUN with the reason and the known exact command and owner; say "
            "unknown rather than inventing either.",
        ),
        "anchors:debugger": (
            "For a testable defect, add or update a focused regression test. Otherwise, report the "
            "verification limitation.",
        ),
    }
    for catalog_name, required in markers.items():
        instruction = " ".join(agents[catalog_name]["instruction"].split())
        for marker in required:
            assert " ".join(marker.split()) in instruction


@pytest.mark.asyncio
async def test_amp_dev_readme_include_example_matches_runtime_source_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The documented include order is parsed and compared to the loaded bundle."""
    _prepared, bundle = await _prepared_catalog(tmp_path, monkeypatch, AMP_DEV_DIR)
    readme = (AMP_DEV_DIR / "README.md").read_text(encoding="utf-8")
    match = re.search(r"```yaml\n(?P<yaml>includes:\n.*?\n)```", readme, re.DOTALL)
    assert match, "README must contain a YAML includes example"
    documented = yaml.safe_load(match.group("yaml"))["includes"]

    assert documented == bundle.includes