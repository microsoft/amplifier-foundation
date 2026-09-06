"""Tests for context/shared/common-agent-base.md -- the shared agent preamble.

This file is @mentioned by every foundation agent and, via
context/shared/common-system-base.md, by the root system prompt. Anything wrong
in it is paid on every spawn and contradicts itself in front of the model.

Four defects are pinned here:

1. **Two commit footers in one session.** This file mandated the emoji form
   while bundle system prompts mandate the plain ``Generated with Amplifier``.
   The owner picked the plain form repo-wide; exactly one file may state it and
   the rest must reference it.
2. **The emoji contradiction.** "Use emojis only when the user explicitly
   requests them" sat 15 lines below a mandate to emit an emoji unprompted.
3. **A todo absolute.** "IMPORTANT: Always use the todo tool" restated a
   system-prompt operating rule as an absolute, in a file that is not the
   system prompt.
4. **A ``sources:`` example that overrides nothing.** The real loader reads
   ``sources`` -> ``modules`` (a two-level path). The example omitted the
   ``modules:`` level, so anyone copying it silently got no override at all --
   the kind of defect only a round-trip catches.
"""

import importlib
import importlib.util
import re
from pathlib import Path

import pytest
import yaml

# ── Constants ────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent
AGENT_BASE_FILE = REPO_ROOT / "context" / "shared" / "common-agent-base.md"

#: The one commit footer form mandated repo-wide (owner decision).
CANONICAL_FOOTER = "Generated with Amplifier"

#: The form that must no longer be mandated anywhere in the live composition.
EMOJI_FOOTER = "Generated with [Amplifier](https://github.com/microsoft/amplifier)"

#: The absolute that was deleted -- it restates a system-prompt operating rule.
TODO_ABSOLUTE = "IMPORTANT: Always use the todo tool"

#: The real loader's read path, quoted verbatim from
#: ``amplifier_app_cli/lib/settings.py::AppSettings.get_module_sources``::
#:
#:     return settings.get("sources", {}).get("modules", {})
#:
#: Two levels. A flat ``sources:`` mapping is read as zero overrides.
SOURCES_KEY_PATH = ("sources", "modules")

#: Directories excluded from the repo-wide footer sweep. ``experiments/`` is a
#: sandbox outside the live bundle composition; ``bundles/anchors*`` already use
#: the canonical form and are owned by a different lane.
FOOTER_SWEEP_EXCLUDES = ("experiments",)


@pytest.fixture
def content() -> str:
    """Read common-agent-base.md."""
    assert AGENT_BASE_FILE.exists(), f"Agent base file not found: {AGENT_BASE_FILE}"
    return AGENT_BASE_FILE.read_text(encoding="utf-8")


# ── Test 1: exactly one commit-footer form, repo-wide ────────────────────────


class TestCommitFooter:
    """One footer form. Stated once. Referenced everywhere else."""

    def test_agent_base_mandates_canonical_footer(self, content: str) -> None:
        """common-agent-base.md must mandate the plain form."""
        assert CANONICAL_FOOTER in content, (
            f"Canonical commit footer {CANONICAL_FOOTER!r} not found in "
            f"common-agent-base.md"
        )

    def test_agent_base_does_not_mandate_emoji_footer(self, content: str) -> None:
        """The emoji form must be gone from the shared agent preamble."""
        assert EMOJI_FOOTER not in content, (
            f"Emoji commit footer {EMOJI_FOOTER!r} still mandated in "
            f"common-agent-base.md -- two footers in one session"
        )

    def test_no_emoji_footer_in_live_composition(self) -> None:
        """No live markdown may mandate the emoji footer."""
        offenders = []
        for path in REPO_ROOT.rglob("*.md"):
            rel = path.relative_to(REPO_ROOT)
            if rel.parts and rel.parts[0] in FOOTER_SWEEP_EXCLUDES:
                continue
            if EMOJI_FOOTER in path.read_text(encoding="utf-8"):
                offenders.append(str(rel))
        assert not offenders, (
            f"These files still mandate the emoji commit footer instead of "
            f"referencing the canonical one: {offenders}"
        )

    def test_footer_stated_once_in_agent_base(self, content: str) -> None:
        """The footer belongs in one block, not restated through the file."""
        occurrences = content.count(CANONICAL_FOOTER)
        assert occurrences == 1, (
            f"Canonical footer appears {occurrences} times in common-agent-base.md; "
            f"it must be stated exactly once"
        )


# ── Test 2: the emoji instruction no longer contradicts itself ───────────────


class TestNoEmojiContradiction:
    """A file that forbids unprompted emojis must not emit one itself."""

    def test_emoji_rule_present(self, content: str) -> None:
        """The tone rule must still be there -- it is not the defect."""
        assert "Use emojis only when the user explicitly requests them" in content, (
            "The emoji tone rule was removed; only the contradiction should have been"
        )

    def test_no_emoji_in_mandated_footer(self, content: str) -> None:
        """The mandated footer must contain no emoji."""
        footer_block = re.search(
            r"```\n([^`]*Generated with Amplifier[^`]*)\n```", content
        )
        assert footer_block, "Commit footer code block not found"
        block = footer_block.group(1)
        assert not any(ord(ch) > 0x2100 for ch in block), (
            f"The mandated commit footer still contains an emoji, contradicting "
            f"the tone rule 15 lines below it: {block!r}"
        )


# ── Test 3: the todo absolute is gone ────────────────────────────────────────


class TestNoTodoAbsolute:
    """The shared agent preamble is not the system prompt."""

    def test_todo_absolute_absent(self, content: str) -> None:
        """'IMPORTANT: Always use the todo tool' must not be restated here."""
        assert TODO_ABSOLUTE not in content, (
            f"{TODO_ABSOLUTE!r} restates a system-prompt operating rule as an "
            f"absolute, in a file loaded by agents that have no todo tool"
        )


# ── Test 4: the sources: override example actually overrides something ───────


def _sources_example(content: str) -> dict:
    """Parse the ``sources:`` YAML example out of the doc."""
    match = re.search(r"```yaml\n(sources:\n(?:.+\n)+?)```", content)
    assert match, "No ```yaml block starting with `sources:` found in the doc"
    parsed = yaml.safe_load(match.group(1))
    assert isinstance(parsed, dict), f"sources: example did not parse to a dict: {parsed!r}"
    return parsed


class TestSourcesOverrideExampleShape:
    """Structural check -- runs everywhere, including CI.

    Guards the exact defect: a flat ``sources:`` mapping that the real loader
    reads as zero overrides.
    """

    def test_example_parses(self, content: str) -> None:
        """The documented example must be valid YAML."""
        assert _sources_example(content)

    def test_example_has_modules_level(self, content: str) -> None:
        """The example must nest overrides under the key path the loader reads."""
        node = _sources_example(content)
        for key in SOURCES_KEY_PATH:
            assert isinstance(node, dict) and key in node, (
                f"sources: example is missing the {key!r} level. The real loader "
                f"reads settings{list(SOURCES_KEY_PATH)}, so a flat mapping "
                f"silently overrides nothing."
            )
            node = node[key]
        assert node, "sources.modules is empty -- the example teaches nothing"

    def test_example_entries_are_uris(self, content: str) -> None:
        """Each documented override must map a module id to a source URI."""
        node = _sources_example(content)
        for key in SOURCES_KEY_PATH:
            node = node[key]
        for module_id, uri in node.items():
            assert isinstance(uri, str) and "://" in uri, (
                f"sources.modules[{module_id!r}] = {uri!r} is not a source URI"
            )


# ── Test 4b: round-trip through the REAL settings loader ─────────────────────

#: Why this may skip -- stated, never silent.
#:
#: amplifier-app-cli is not a dependency of amplifier-foundation, and it cannot
#: become one: app-cli's own metadata declares ``Requires-Dist:
#: amplifier-foundation``, so a dev-dependency here would be circular. This
#: round-trip therefore runs wherever the CLI happens to be installed alongside
#: the repo (any dev machine). CI covers the same defect structurally via
#: TestSourcesOverrideExampleShape, which pins the loader's read path as a
#: verbatim quote in SOURCES_KEY_PATH.
_REAL_LOADER_REASON = (
    "amplifier-app-cli not importable; it cannot be a dev-dependency of "
    "amplifier-foundation because app-cli itself Requires-Dist "
    "amplifier-foundation (circular). CI covers this defect structurally via "
    "TestSourcesOverrideExampleShape."
)
def _have_real_loader() -> bool:
    """True when the real settings loader is importable in this environment."""
    try:
        return importlib.util.find_spec("amplifier_app_cli.lib.settings") is not None
    except ModuleNotFoundError:
        # find_spec raises rather than returning None when the parent package
        # is absent, which is the normal case in this repo's own test env.
        return False


_HAVE_REAL_LOADER = _have_real_loader()


def _load_real_settings_module():
    """Import the real settings loader (only called when it is present)."""
    return importlib.import_module("amplifier_app_cli.lib.settings")


@pytest.mark.skipif(not _HAVE_REAL_LOADER, reason=_REAL_LOADER_REASON)
class TestSourcesOverrideExampleRoundTrip:
    """Feed the documented example to the real loader and read it back.

    This is the test that would have caught the defect: the old flat example
    parsed fine and returned ``{}`` from ``get_module_sources()``.
    """

    def test_documented_example_round_trips(self, content: str, tmp_path: Path) -> None:
        """The doc's example must survive the real loader unchanged."""
        example = _sources_example(content)

        global_settings = tmp_path / "global" / "settings.yaml"
        global_settings.parent.mkdir(parents=True)
        global_settings.write_text(yaml.safe_dump(example), encoding="utf-8")

        settings_mod = _load_real_settings_module()
        paths = settings_mod.SettingsPaths(
            global_settings=global_settings,
            project_settings=tmp_path / "project" / "settings.yaml",
            local_settings=tmp_path / "local" / "settings.local.yaml",
            session_settings=None,
        )
        loaded = settings_mod.AppSettings(paths).get_module_sources()

        # What a reader copying the doc would expect to take effect. Computed
        # defensively so a doc regressed to the flat shape fails with the real
        # message ("loader returned {}") instead of a bare KeyError.
        promised = example.get("sources", {})
        expected = (
            promised["modules"]
            if isinstance(promised, dict) and "modules" in promised
            else promised
        )

        assert loaded == expected, (
            f"The documented sources: example does not round-trip through the real "
            f"settings loader. Loader returned {loaded!r}, doc promised {expected!r}. "
            f"An example that overrides nothing is worse than no example."
        )
        assert loaded, "The documented example produced zero module overrides"

    def test_flat_example_would_have_failed(self, tmp_path: Path) -> None:
        """Prove the guard is real: the old flat shape returns no overrides."""
        flat = {"sources": {"tool-bash": "file:///home/user/repos/tool-bash"}}

        global_settings = tmp_path / "global" / "settings.yaml"
        global_settings.parent.mkdir(parents=True)
        global_settings.write_text(yaml.safe_dump(flat), encoding="utf-8")

        settings_mod = _load_real_settings_module()
        paths = settings_mod.SettingsPaths(
            global_settings=global_settings,
            project_settings=tmp_path / "project" / "settings.yaml",
            local_settings=tmp_path / "local" / "settings.local.yaml",
            session_settings=None,
        )
        loaded = settings_mod.AppSettings(paths).get_module_sources()

        assert loaded == {}, (
            f"The flat sources: shape was expected to override nothing, but the "
            f"loader returned {loaded!r} -- the documented defect may have been "
            f"fixed upstream, in which case this pin can be relaxed"
        )
