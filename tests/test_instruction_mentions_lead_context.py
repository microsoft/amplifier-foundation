"""The instruction's own @mentions must LEAD the system-prompt context block.

Defect this pins (measured, session 7132cbfd, bundle anchors-amp-dev):
``raw.system`` was one 98,896-char block whose first line was the literal
``@anchors-amp-dev:context/system.md``, followed by ``---`` and 23
``<context_file>`` blocks.  Blocks 1-22 were composed bundles' ``context:``
includes (peripheral awareness files).  Block 23 -- the LAST -- was the root
bundle's own ``context/system.md``, the file that says "You are Amplifier".
Byte offset 72,012 of 98,896: 73% into the prompt.

Cause: ``_create_system_prompt_factory`` added ``bundle.context`` includes to
the ``ContentDeduplicator`` BEFORE resolving the instruction's @mentions, and
``format_context_block`` emits in deduplicator insertion order
(``get_unique_files`` iterates ``_content_by_hash``, a plain insertion-ordered
dict -- verified by reading it; nothing sorts).

Fix: resolve instruction @mentions first, so the author's primary voice leads.

Test inventory, stated honestly:

* ``test_instruction_mention_is_the_first_context_block``     FAIL-BEFORE
* ``test_double_referenced_file_emitted_once_mention_leads``  FAIL-BEFORE
* ``test_no_instruction_mentions_output_is_byte_identical``   NO-REGRESSION GUARD
  -- this one passes on main *by construction*.  It asserts that bundles
  without instruction mentions are untouched by the reorder; a guard that
  failed on main would be pinning the bug, not the invariant.  The
  main-vs-branch byte comparison itself is in the PR body.
* ``test_mentions_resolved_payload_semantics_unchanged``      NO-REGRESSION GUARD

No shell, no platform-specific paths: runs identically on POSIX and Windows.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any, NamedTuple
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_foundation.bundle import Bundle
from amplifier_foundation.io.frontmatter import parse_frontmatter

_prep = importlib.import_module("amplifier_foundation.bundle._prepared")
PreparedBundle: Any = _prep.PreparedBundle
BundleModuleResolver: Any = _prep.BundleModuleResolver
MENTIONS_RESOLVED: str = _prep._MENTIONS_RESOLVED_EVENT

REPO_ROOT = Path(__file__).parent.parent
ANCHORS_DIR = REPO_ROOT / "bundles" / "anchors"
ANCHORS_BUNDLE = ANCHORS_DIR / "bundle.md"


# ── helpers ──────────────────────────────────────────────────────────────────


class Block(NamedTuple):
    """One parsed ``<context_file>`` block."""

    paths: str
    body: str

    @property
    def first_attribution(self) -> str:
        """The leading entry of the ``paths=`` attribute."""
        return self.paths.split(", ")[0]


_BLOCK_RE = re.compile(
    r'<context_file paths="(?P<paths>[^"]*)">\n(?P<body>.*?)\n</context_file>',
    re.DOTALL,
)


def _blocks(system_prompt: str) -> list[Block]:
    """Parse ``<context_file>`` blocks out of a system prompt, in emitted order."""
    return [
        Block(paths=m.group("paths"), body=m.group("body"))
        for m in _BLOCK_RE.finditer(system_prompt)
    ]


def _make_prepared(bundle: Bundle) -> Any:
    return PreparedBundle(
        mount_plan={},
        bundle=bundle,
        resolver=BundleModuleResolver(module_paths={}),
    )


def _make_mock_session() -> MagicMock:
    mock_hooks = AsyncMock()
    mock_hooks.list_handlers = MagicMock(return_value={})
    mock_hooks.register = MagicMock(return_value=MagicMock())
    coordinator = MagicMock()
    coordinator.hooks = mock_hooks
    session = MagicMock()
    session.coordinator = coordinator
    return session


def _shipped_anchors_bundle() -> Bundle:
    """Load the shipped anchors root instruction without activating its modules."""
    _frontmatter, instruction = parse_frontmatter(
        ANCHORS_BUNDLE.read_text(encoding="utf-8")
    )
    return Bundle(
        name="anchors",
        instruction=instruction,
        base_path=ANCHORS_DIR,
        source_base_paths={"anchors": ANCHORS_DIR},
    )


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_public_factory_uses_prepared_bundle_and_target_session(
    tmp_path: Path,
) -> None:
    """The public API keeps mention content and events owned by its target."""
    _write(tmp_path / "context" / "system.md", "TARGET SESSION BODY")
    bundle = Bundle(
        name="target-ns",
        base_path=tmp_path,
        instruction="@target-ns:context/system.md",
    )
    prepared = _make_prepared(bundle)
    target_session = _make_mock_session()

    factory = prepared.create_system_prompt_factory(
        target_session, session_cwd=tmp_path
    )
    prompt = await factory()

    assert "TARGET SESSION BODY" in prompt
    target_session.coordinator.hooks.emit.assert_called_once()
    event_name, payload = target_session.coordinator.hooks.emit.call_args.args
    assert event_name == MENTIONS_RESOLVED
    assert payload["resolutions"][0]["mention"] == "@target-ns:context/system.md"


# ── 1. FAIL-BEFORE: the instruction's mention leads the context block ────────


@pytest.mark.asyncio
async def test_instruction_mention_is_the_first_context_block(tmp_path: Path) -> None:
    """Instruction mention first, ``context:`` includes after -- in that order.

    This is the anchors-amp-dev shape in miniature: a root bundle whose whole
    instruction is a single @mention, plus composed bundles' awareness includes.
    """
    system_md = _write(
        tmp_path / "context" / "system.md",
        "You are Amplifier, configured for development OF the ecosystem itself.",
    )
    gitea = _write(tmp_path / "gitea-awareness.md", "# Gitea Environments")
    dtu = _write(tmp_path / "dtu-awareness.md", "# Digital Twin Universe")

    bundle = Bundle(
        name="ns",
        base_path=tmp_path,
        instruction="@ns:context/system.md",
        context={"gitea:context/gitea-awareness.md": gitea, "dtu:ctx.md": dtu},
    )
    prepared = _make_prepared(bundle)
    factory = prepared._create_system_prompt_factory(bundle, _make_mock_session())

    prompt = await factory()

    # The instruction itself is still the head, blocks still follow "---".
    assert prompt.startswith("@ns:context/system.md\n\n---\n\n")

    blocks = _blocks(prompt)
    assert len(blocks) == 3, f"expected 3 context_file blocks, got {len(blocks)}"

    # THE ASSERTION THIS TEST EXISTS FOR: the author's own file is block 1.
    assert blocks[0].body == system_md.read_text(encoding="utf-8")
    assert blocks[0].first_attribution.startswith("@ns:context/system.md")

    # Includes follow, in their declaration order.
    assert [b.body for b in blocks[1:]] == [
        gitea.read_text(encoding="utf-8"),
        dtu.read_text(encoding="utf-8"),
    ]

    # And "You are Amplifier" is near the head, not 73% in.
    assert "You are Amplifier" in prompt[:1500]


# ── 2. FAIL-BEFORE: double-referenced file -- one block, mention leads ───────


@pytest.mark.asyncio
async def test_double_referenced_file_emitted_once_mention_leads(
    tmp_path: Path,
) -> None:
    """A file that is BOTH a ``context:`` include and an instruction @mention.

    Emitted exactly once (content dedup), and the ``paths=`` label leads with
    the author's explicit @mention rather than the include's context name.
    """
    shared = _write(tmp_path / "context" / "system.md", "SHARED PRINCIPLES BODY")
    other = _write(tmp_path / "other.md", "unrelated awareness")

    bundle = Bundle(
        name="ns",
        base_path=tmp_path,
        instruction="@ns:context/system.md",
        context={"ns:context/system.md": shared, "other:ctx.md": other},
    )
    prepared = _make_prepared(bundle)
    factory = prepared._create_system_prompt_factory(bundle, _make_mock_session())

    prompt = await factory()
    blocks = _blocks(prompt)

    # Emitted ONCE despite two references.
    bodies = [b.body for b in blocks]
    assert bodies.count("SHARED PRINCIPLES BODY") == 1
    assert len(blocks) == 2

    shared_block = next(b for b in blocks if b.body == "SHARED PRINCIPLES BODY")

    # The mention wins the label: it leads, and it is present.
    assert shared_block.first_attribution.startswith("@ns:context/system.md")
    assert "@ns:context/system.md" in shared_block.paths

    # No information was dropped -- the include's own attribution still trails.
    assert "ns:context/system.md" in shared_block.paths

    # And it is still the FIRST block overall.
    assert blocks[0] is shared_block


# ── 3. NO-REGRESSION GUARD: no instruction mentions -> byte-identical ───────


@pytest.mark.asyncio
async def test_no_instruction_mentions_output_is_byte_identical(
    tmp_path: Path,
) -> None:
    """A bundle with an inline instruction and no @mentions is untouched.

    Passes on main by construction -- that is the point.  The reorder must be
    a no-op for every bundle whose instruction carries no @mentions, which is
    the overwhelming majority.
    """
    a = _write(tmp_path / "a.md", "AAA body")
    b = _write(tmp_path / "b.md", "BBB body")

    instruction = "You are a helpful assistant. No mentions here at all."
    bundle = Bundle(
        name="plain",
        base_path=tmp_path,
        instruction=instruction,
        context={"a:ctx.md": a, "b:ctx.md": b},
    )
    prepared = _make_prepared(bundle)
    factory = prepared._create_system_prompt_factory(bundle, _make_mock_session())

    prompt = await factory()

    expected = (
        f"{instruction}\n\n---\n\n"
        f'<context_file paths="a:ctx.md \u2192 {a.resolve()}">\nAAA body\n</context_file>'
        "\n\n"
        f'<context_file paths="b:ctx.md \u2192 {b.resolve()}">\nBBB body\n</context_file>'
    )
    assert prompt == expected


# ── 4. NO-REGRESSION GUARD: mentions:resolved semantics unchanged ───────────


@pytest.mark.asyncio
async def test_mentions_resolved_payload_semantics_unchanged(tmp_path: Path) -> None:
    """Same resolutions, same failed list.  Only the ORDER of resolutions moves.

    The event's set semantics are what downstream consumers key on; this pins
    that the reorder did not add, drop, or reclassify a single entry.
    """
    system_md = _write(tmp_path / "context" / "system.md", "PRINCIPLES")
    aware = _write(tmp_path / "aware.md", "AWARENESS")

    bundle = Bundle(
        name="ns",
        base_path=tmp_path,
        instruction="@ns:context/system.md and @ns:context/missing.md",
        context={"aware:ctx.md": aware},
    )
    prepared = _make_prepared(bundle)
    session = _make_mock_session()
    factory = prepared._create_system_prompt_factory(bundle, session)

    await factory()

    session.coordinator.hooks.emit.assert_called_once()
    event_name, payload = session.coordinator.hooks.emit.call_args.args
    assert event_name == MENTIONS_RESOLVED

    got = {(r["mention"], r["resolved_path"], r["is_new"]) for r in payload["resolutions"]}
    assert got == {
        ("@ns:context/system.md", str(system_md.resolve()), True),
        ("aware:ctx.md", str(aware.resolve()), True),
    }
    assert payload["failed"] == [
        {"mention": "@ns:context/missing.md", "reason": "not_found"}
    ]
    assert payload["deduplicated_count"] == 0

    # The instruction's mention now leads the resolutions list as well.
    assert payload["resolutions"][0]["mention"] == "@ns:context/system.md"


# ── 5. anchors: the shipped root instruction discovers standard AGENTS files ─


@pytest.mark.asyncio
async def test_shipped_anchors_root_loads_standard_agents_and_workspace_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shipped anchors root resolves system.md's AGENTS chain at session CWD."""
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    global_agents = _write(
        home / ".amplifier" / "AGENTS.md", "GLOBAL AGENTS SENTINEL"
    )
    local_agents = _write(
        workspace / ".amplifier" / "AGENTS.md", "LOCAL AGENTS SENTINEL"
    )
    workspace_agents = _write(
        workspace / "AGENTS.md", "WORKSPACE AGENTS SENTINEL\n@SCRATCH.md"
    )
    scratch = _write(workspace / "SCRATCH.md", "WORKSPACE SCRATCH SENTINEL")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    bundle = _shipped_anchors_bundle()
    factory = _make_prepared(bundle).create_system_prompt_factory(
        _make_mock_session(), session_cwd=workspace
    )
    prompt = await factory()

    # The actual root bundle instruction enters its namespaced system context,
    # whose standard AGENTS mentions resolve from the supplied session CWD.
    assert prompt.startswith(f"{bundle.instruction}\n\n---\n\n")
    assert [block.body for block in _blocks(prompt)] == [
        (ANCHORS_DIR / "context" / "system.md").read_text(encoding="utf-8"),
        global_agents.read_text(encoding="utf-8"),
        local_agents.read_text(encoding="utf-8"),
        workspace_agents.read_text(encoding="utf-8"),
        scratch.read_text(encoding="utf-8"),
    ]


@pytest.mark.asyncio
async def test_shipped_anchors_root_skips_absent_optional_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absent home and .amplifier AGENTS files do not prevent workspace discovery."""
    home = tmp_path / "empty-home"
    workspace = tmp_path / "workspace"
    workspace_agents = _write(
        workspace / "AGENTS.md", "WORKSPACE AGENTS SENTINEL\n@SCRATCH.md"
    )
    scratch = _write(workspace / "SCRATCH.md", "WORKSPACE SCRATCH SENTINEL")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    factory = _make_prepared(_shipped_anchors_bundle()).create_system_prompt_factory(
        _make_mock_session(), session_cwd=workspace
    )
    prompt = await factory()

    assert [block.body for block in _blocks(prompt)] == [
        (ANCHORS_DIR / "context" / "system.md").read_text(encoding="utf-8"),
        workspace_agents.read_text(encoding="utf-8"),
        scratch.read_text(encoding="utf-8"),
    ]
