"""Structural guards for the anchors / anchors-amp-dev bundle pair.

`anchors-amp-dev` is `anchors` plus a thin Amplifier-ecosystem knowledge layer.
That relationship is only real if it is an *include*, not a copy: the moment the
two trees carry parallel copies of the same file, one side gets a fix and the
other silently ships the old text. That is exactly what happened between #327
(applied to the anchors-amp-dev copies only) and #341 (which swept the repo-root
`agents/` only) -- two sweeps, neither of which reached both trees.

Each test below pins one half of that invariant and fails loudly when a copy
reappears. All five fail on the pre-refactor tree; see
`docs/lanes/ux32-anchors-dry-refactor/DONE-NOTE.md` for the recorded
fail-before output.

Pure-Python and filesystem-only on purpose -- no shell, no `stat -c`, nothing
platform-dependent, so these run identically on Linux, macOS and CI.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
BUNDLES_DIR = REPO_ROOT / "bundles"
ANCHORS_DIR = BUNDLES_DIR / "anchors"
AMP_DEV_DIR = BUNDLES_DIR / "anchors-amp-dev"

# Files that are legitimately present in both bundle trees: every bundle needs
# its own entrypoint and its own README. Everything else must be inherited.
SHARED_PATH_ALLOWLIST = {"bundle.md", "README.md"}

# The Amplifier-ecosystem knowledge docs. Exactly one copy of each may exist on
# the shipped surface -- a second copy is the drift vector this refactor removes.
ECOSYSTEM_DOCS = ("ecosystem-map.md", "dev-workflows.md", "testing-patterns.md")

# `experiments/` holds the frozen pre-promotion originals (anchors was promoted
# out of `experiments/behavioral-anchor`). Nothing there is a registered bundle
# or composed at runtime, so its copies cannot drift into a session -- but they
# are copies, and they are named in the failure message rather than hidden, so
# a reader is never left wondering why the count looked wrong.
UNSHIPPED_TREES = (".git", "experiments")

# The anchors include must be a full git URL with a #subdirectory= fragment, not
# a bare `anchors` name. A bare name resolves only where the CLI happens to have
# registered that namespace; the full URL keeps the bundle liftable, which is
# the "self-contained by design" convention the anchors README states.
ANCHORS_INCLUDE_RE = re.compile(
    r"^git\+https://github\.com/microsoft/amplifier-foundation@[^#]+"
    r"#subdirectory=bundles/anchors(?:/bundle\.md)?$"
)

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


def _frontmatter(path: Path) -> dict:
    """Parse a markdown file's YAML frontmatter into a dict ({} when absent)."""
    match = _FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
    if not match:
        return {}
    return yaml.safe_load(match.group(1)) or {}


def _agent_files() -> list[Path]:
    """Every shipped agent description in the repo: root `agents/` + bundles."""
    found = sorted((REPO_ROOT / "agents").glob("*.md"))
    found += sorted(BUNDLES_DIR.glob("*/agents/*.md"))
    return found


def _relative_files(root: Path) -> set[str]:
    """POSIX-style relative paths, so the comparison reads the same on Windows."""
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


class TestAmpDevIsAThinVariant:
    """anchors-amp-dev declares a layer, not a second runtime."""

    def test_declares_no_session_tools_or_hooks(self) -> None:
        """The runtime is anchors'. Re-declaring it forks the mount plan.

        `session:`, `tools:` and `hooks:` are anchors' single source of truth.
        A duplicate block here is how the two bundles' orchestrator, context
        window, tool roster or hook set drift apart without anyone editing both.
        """
        frontmatter = _frontmatter(AMP_DEV_DIR / "bundle.md")
        declared = [k for k in ("session", "tools", "hooks") if k in frontmatter]
        assert not declared, (
            f"bundles/anchors-amp-dev/bundle.md re-declares {declared}; these "
            "must come from the anchors include so there is exactly one source "
            "of truth for the runtime."
        )

    def test_includes_anchors_by_full_url(self) -> None:
        """The anchors include is a full URL with a #subdirectory= fragment."""
        includes = _frontmatter(AMP_DEV_DIR / "bundle.md").get("includes") or []
        sources = [
            entry.get("bundle") if isinstance(entry, dict) else entry
            for entry in includes
        ]
        matching = [s for s in sources if isinstance(s, str) and ANCHORS_INCLUDE_RE.match(s)]
        assert matching, (
            "bundles/anchors-amp-dev/bundle.md must include anchors by full URL "
            "(git+https://github.com/microsoft/amplifier-foundation@main"
            "#subdirectory=bundles/anchors/bundle.md). "
            f"Found includes: {sources}"
        )


class TestNoParallelCopies:
    """The two bundle trees may not carry the same file twice."""

    def test_no_same_path_file_in_both_bundles(self) -> None:
        """A same-path file in both trees is a fix that will land on one side.

        #327 edited the anchors-amp-dev copies of six agents and system.md and
        left the anchors originals behind; the CLI default bundle then shipped
        the un-fixed text for weeks. This test makes that state unreachable.
        """
        shared = _relative_files(ANCHORS_DIR) & _relative_files(AMP_DEV_DIR)
        offenders = sorted(shared - SHARED_PATH_ALLOWLIST)
        assert not offenders, (
            "These paths exist under BOTH bundles/anchors/ and "
            f"bundles/anchors-amp-dev/: {offenders}. Only "
            f"{sorted(SHARED_PATH_ALLOWLIST)} may be duplicated -- everything "
            "else must be inherited via the anchors include."
        )

    def test_exactly_one_copy_of_each_ecosystem_doc(self) -> None:
        """Each Amplifier-ecosystem doc exists exactly once on the shipped surface.

        Copies under `experiments/` are excluded and reported, not counted --
        see UNSHIPPED_TREES for why.
        """
        for name in ECOSYSTEM_DOCS:
            found = sorted(
                p.relative_to(REPO_ROOT)
                for p in REPO_ROOT.rglob(name)
                if ".git" not in p.relative_to(REPO_ROOT).parts
            )
            # Compare on path *parts*, never on a string separator -- rglob
            # yields backslashes on Windows and this runs there too.
            unshipped = [r.as_posix() for r in found if r.parts[0] in UNSHIPPED_TREES]
            shipped = [
                r.as_posix() for r in found if r.parts[0] not in UNSHIPPED_TREES
            ]
            assert len(shipped) == 1, (
                f"Expected exactly one shipped copy of {name}, found "
                f"{len(shipped)}: {shipped}. Two copies drift; the canonical "
                "location is context/amplifier-dev/. "
                f"(Excluded, frozen and unshipped: {unshipped})"
            )


class TestAgentDescriptionsCarryNoExampleBlocks:
    """#340 rejects `<example>`/`<commentary>` in descriptions -- everywhere."""

    def test_no_example_or_commentary_in_any_agent_description(self) -> None:
        """Extends #341's sweep, which covered the repo-root `agents/` only.

        `description-authoring-principles.md` V3 is 'no `<example>` blocks --
        not "at most 2", zero'. #341 (1b16a7d) applied that to the 16 root
        agents and stopped there, leaving the bundle agent trees as the repo's
        last violators. The policy is repo-wide, so the check is too.
        """
        offenders: list[str] = []
        for agent_file in _agent_files():
            description = (_frontmatter(agent_file).get("meta") or {}).get(
                "description", ""
            )
            if not isinstance(description, str):
                continue
            tags = [
                tag
                for tag in ("<example>", "<commentary>")
                if tag in description.lower()
            ]
            if tags:
                rel = agent_file.relative_to(REPO_ROOT).as_posix()
                offenders.append(f"{rel} ({', '.join(tags)})")
        assert not offenders, (
            "Agent descriptions must contain no <example>/<commentary> blocks "
            "(description-authoring-principles.md V3, #340). Offenders: "
            + "; ".join(offenders)
        )
