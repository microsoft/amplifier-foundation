"""Pin the compose semantics of the tool-skills DOUBLE MOUNT.

`tool-skills` is mounted twice in a foundation-root session:

1. `behaviors/agents.yaml` mounts it to register foundation's OWN skills
   directory (`config.skills` -> amplifier-foundation#subdirectory=skills).
2. The root `bundle.md` includes
   `amplifier-bundle-skills@main#subdirectory=behaviors/skills.yaml`, which
   mounts the same module id with the curated collection plus `visibility`.

Two mounts of one module id is only safe because `deep_merge` CONCATENATES
list-typed config values instead of replacing them (added in 70d521f, PR #120,
whose commit message names this exact silent-config-loss failure). If that
behavior ever regresses to "later list replaces earlier list", ONE of the two
skills directories is silently dropped and no error is raised anywhere -- the
skills simply stop being discoverable.

These tests assert the EFFECTIVE composed config, in both orders, so the
regression is caught at the seam rather than in a user's session.

Measured on main a0decc6: nothing is dropped. The double mount is a FINDING
(a load-bearing dependency on list-concat semantics), not a defect.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from amplifier_foundation.bundle import Bundle

REPO_ROOT = Path(__file__).parent.parent
AGENTS_BEHAVIOR = REPO_ROOT / "behaviors" / "agents.yaml"

FOUNDATION_SKILLS_SOURCE = (
    "git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=skills"
)
CURATED_SKILLS_SOURCE = (
    "git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=skills"
)
TOOL_SKILLS_MODULE_SOURCE = (
    "git+https://github.com/microsoft/amplifier-bundle-skills"
    "@main#subdirectory=modules/tool-skills"
)

# Verbatim copy of the tool block of
# amplifier-bundle-skills@main#subdirectory=behaviors/skills.yaml as read on
# 2026-09-06. Inlined rather than fetched so this test stays hermetic (no
# network, no cache dependency). If the upstream behavior changes shape, this
# fixture is what needs updating -- and the mismatch is the point of the test.
EXTERNAL_SKILLS_BEHAVIOR: dict[str, Any] = {
    "bundle": {"name": "skills-behavior", "version": "1.0.0"},
    "tools": [
        {
            "module": "tool-skills",
            "source": TOOL_SKILLS_MODULE_SOURCE,
            "config": {
                "skills": [CURATED_SKILLS_SOURCE],
                "visibility": {
                    "enabled": True,
                    "inject_role": "user",
                    "visibility_token_budget": 5000,
                    "ephemeral": True,
                    "priority": 20,
                },
            },
        }
    ],
}


def _load_yaml_bundle(path: Path) -> Bundle:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Bundle.from_dict(data, base_path=path.parent)


def _tool_skills_entries(bundle: Bundle) -> list[dict[str, Any]]:
    return [t for t in bundle.tools if t.get("module") == "tool-skills"]


def _skills_config(bundle: Bundle) -> list[str]:
    entries = _tool_skills_entries(bundle)
    assert len(entries) == 1, f"expected exactly one tool-skills mount, got {entries}"
    return list(entries[0].get("config", {}).get("skills", []))


# =============================================================================
# The declarations themselves (guards against a silent edit upstream of compose)
# =============================================================================


def test_agents_behavior_registers_foundation_skills_dir() -> None:
    """behaviors/agents.yaml must still be the mount that owns foundation's skills."""
    agents = _load_yaml_bundle(AGENTS_BEHAVIOR)
    entries = _tool_skills_entries(agents)
    assert len(entries) == 1, "behaviors/agents.yaml must mount tool-skills exactly once"
    assert entries[0]["config"]["skills"] == [FOUNDATION_SKILLS_SOURCE]


def test_both_mounts_agree_on_the_module_source() -> None:
    """Module `source` is a SCALAR -- deep_merge lets the later mount win.

    The two mounts are only order-insensitive because they name the SAME source.
    If they ever diverge, whichever composes last silently wins and the other
    declaration becomes dead text.
    """
    agents = _load_yaml_bundle(AGENTS_BEHAVIOR)
    agents_source = _tool_skills_entries(agents)[0]["source"]
    external_source = EXTERNAL_SKILLS_BEHAVIOR["tools"][0]["source"]
    assert agents_source == external_source == TOOL_SKILLS_MODULE_SOURCE


# =============================================================================
# The effective composed config -- the thing a session actually mounts
# =============================================================================


def test_double_mount_keeps_both_skills_dirs_in_bundle_md_order() -> None:
    """Root bundle.md order: behaviors/agents (line 27) BEFORE skills.yaml (line 33)."""
    agents = _load_yaml_bundle(AGENTS_BEHAVIOR)
    external = Bundle.from_dict(EXTERNAL_SKILLS_BEHAVIOR)

    composed = agents.compose(external)

    assert _skills_config(composed) == [
        FOUNDATION_SKILLS_SOURCE,
        CURATED_SKILLS_SOURCE,
    ]


def test_double_mount_keeps_both_skills_dirs_in_reverse_order() -> None:
    """Order must not decide which skills directory survives."""
    agents = _load_yaml_bundle(AGENTS_BEHAVIOR)
    external = Bundle.from_dict(EXTERNAL_SKILLS_BEHAVIOR)

    composed = external.compose(agents)

    assert set(_skills_config(composed)) == {
        FOUNDATION_SKILLS_SOURCE,
        CURATED_SKILLS_SOURCE,
    }


def test_double_mount_preserves_visibility_config_from_the_curated_mount() -> None:
    """A dict-typed config key contributed by only one mount must survive too."""
    agents = _load_yaml_bundle(AGENTS_BEHAVIOR)
    external = Bundle.from_dict(EXTERNAL_SKILLS_BEHAVIOR)

    composed = agents.compose(external)
    config = _tool_skills_entries(composed)[0]["config"]

    assert config["visibility"]["enabled"] is True
    assert config["visibility"]["visibility_token_budget"] == 5000


def test_double_mount_collapses_to_a_single_module_entry() -> None:
    """merge_module_lists keys by module id -- two declarations, one mount."""
    agents = _load_yaml_bundle(AGENTS_BEHAVIOR)
    external = Bundle.from_dict(EXTERNAL_SKILLS_BEHAVIOR)

    composed = agents.compose(external)

    assert len(_tool_skills_entries(composed)) == 1


def test_duplicate_source_is_deduplicated_not_repeated() -> None:
    """Composing a mount with itself must not double its skills list."""
    agents = _load_yaml_bundle(AGENTS_BEHAVIOR)
    twin = _load_yaml_bundle(AGENTS_BEHAVIOR)

    composed = agents.compose(twin)

    assert _skills_config(composed) == [FOUNDATION_SKILLS_SOURCE]
