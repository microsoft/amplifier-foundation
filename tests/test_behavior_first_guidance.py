"""Focused guards for behavior-first authoring examples and classifications."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).parent.parent
GUIDE = REPO_ROOT / "docs" / "BUNDLE_GUIDE.md"
ANCHORS_MANIFEST = REPO_ROOT / "bundles" / "anchors" / "bundle.md"
README = REPO_ROOT / "README.md"
APPLICATION_GUIDE = REPO_ROOT / "docs" / "APPLICATION_INTEGRATION_GUIDE.md"
API_REFERENCE = REPO_ROOT / "docs" / "API_REFERENCE.md"
ANCHORS_URI = (
    "git+https://github.com/microsoft/amplifier-foundation@main"
    "#subdirectory=bundles/anchors/bundle.md"
)


def _fenced_block_after(text: str, marker: str, language: str) -> str:
    """Return the first fenced block after an authoring-guide section marker."""
    match = re.search(
        rf"{re.escape(marker)}.*?```{language}\n(.*?)```",
        text,
        flags=re.DOTALL,
    )
    assert match, f"Expected a {language} example after {marker!r}"
    return match.group(1)


def _frontmatter(markdown: str) -> dict:
    match = re.match(r"\A---\n(.*?)\n---\n", markdown, flags=re.DOTALL)
    assert match, "Expected Markdown frontmatter"
    return yaml.safe_load(match.group(1))


def _joined_string_literals(source: str) -> str:
    """Normalize Python's adjacent multiline string-literal form for assertions."""
    return re.sub(r'"\s+"', "", source)


def test_primary_behavior_example_is_parseable_and_does_not_choose_a_host() -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    source = _fenced_block_after(guide, "### Primary deliverable: the behavior", "yaml")
    behavior = yaml.safe_load(source)

    assert behavior["bundle"]["name"] == "my-capability-behavior"
    assert behavior["agents"]["include"] == ["my-capability:my-agent"]
    assert behavior["context"]["include"] == ["my-capability:context/instructions.md"]
    assert not {"includes", "providers", "session"} & behavior.keys()
    assert "orchestrator" not in source
    assert ANCHORS_URI not in source


def test_supporting_root_example_is_parseable_and_wires_anchors_to_its_behavior() -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    root = _fenced_block_after(
        guide, "### Optional supporting root: Anchors for a new complete host", "markdown"
    )
    frontmatter = _frontmatter(root)
    includes = [entry["bundle"] for entry in frontmatter["includes"]]

    assert includes == [ANCHORS_URI, "my-capability:behaviors/my-capability"]
    assert "@anchors:context/system.md" in root
    assert not {"providers", "session", "tools", "hooks", "agents"} & frontmatter.keys()


def test_guide_classifies_behavior_before_conditional_supporting_root() -> None:
    guide = GUIDE.read_text(encoding="utf-8")

    assert guide.index("### Primary deliverable: the behavior") < guide.index(
        "### Optional supporting root: Anchors for a new complete host"
    )
    assert "you still build on `foundation`" not in guide
    assert "Behavior-first does\nnot ban roots" in guide


def test_anchors_difference_note_matches_the_actual_manifest() -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    manifest = _frontmatter(ANCHORS_MANIFEST.read_text(encoding="utf-8"))
    expected_agents = [
        "anchors:explorer",
        "anchors:architect",
        "anchors:builder",
        "anchors:debugger",
        "anchors:git-ops",
        "anchors:researcher",
    ]

    assert manifest["agents"]["include"] == expected_agents
    skills = next(tool for tool in manifest["tools"] if tool["module"] == "tool-skills")
    assert skills["config"]["visibility"]["enabled"] is False
    assert all(agent in guide for agent in expected_agents)
    assert "Bare Anchors keeps skills visibility\noff" in guide


def test_readme_quick_start_composes_anchors_with_existing_provider_partial() -> None:
    readme = README.read_text(encoding="utf-8")
    source = _fenced_block_after(readme, "### Load, Compose, and Execute", "python")
    normalized = _joined_string_literals(source)

    ast.parse(source)
    assert ANCHORS_URI in normalized
    assert (
        "git+https://github.com/microsoft/amplifier-foundation@main"
        "#subdirectory=providers/anthropic-sonnet.yaml"
    ) in normalized
    assert "composed = anchors.compose(provider)" in source
    assert "examples/07_full_workflow.py" in readme


def test_application_declarative_example_is_a_valid_anchors_root_composition() -> None:
    application_guide = APPLICATION_GUIDE.read_text(encoding="utf-8")
    source = _fenced_block_after(
        application_guide, "### Declarative (YAML Includes Chain)", "yaml"
    )
    frontmatter = _frontmatter(source)

    assert [entry["bundle"] for entry in frontmatter["includes"]] == [
        ANCHORS_URI,
        "my-app:behaviors/domain-expert",
    ]
    assert "session" not in frontmatter
    assert "behavior:" not in source
    body = source.split("---\n", 2)[2]
    assert body.index("@anchors:context/system.md") < body.index(
        "You are a helpful domain expert."
    )
    assert body.count("@anchors:context/system.md") == 1


def test_application_hook_propagation_example_uses_an_anchors_based_parent() -> None:
    application_guide = APPLICATION_GUIDE.read_text(encoding="utf-8")
    source = _fenced_block_after(
        application_guide, "### Hook Propagation to Spawned Children", "python"
    )

    assert "composed = anchors.compose(provider).compose(observability)" in source
    assert "foundation.compose(provider)" not in source


def test_api_reference_composition_example_loads_anchors_as_its_base() -> None:
    reference = API_REFERENCE.read_text(encoding="utf-8")
    source = _fenced_block_after(reference, "### Compose Bundles", "python")
    normalized = _joined_string_literals(source)

    ast.parse(source)
    assert "from amplifier_foundation import load_bundle" in source
    assert ANCHORS_URI in normalized
    assert 'load_bundle("foundation")' not in source