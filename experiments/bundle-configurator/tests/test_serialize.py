"""Tests for serialize.py — bundle serialization to .md format."""

from __future__ import annotations

import yaml

from amplifier_foundation.bundle import Bundle

from amplifier_configurator.models import (
    BehaviorInfo,
    PartKind,
    ProvenanceMap,
    TrackedPart,
)
from amplifier_configurator.serialize import serialize_bundle


def _tp(
    kind: PartKind,
    name: str,
    source: str | None = None,
    tokens: int = 0,
    config: dict | None = None,
    namespace_path: str | None = None,
) -> TrackedPart:
    return TrackedPart(
        kind=kind,
        name=name,
        source_behavior=source,
        tokens=tokens,
        config=config or {},
        namespace_path=namespace_path,
    )


def _simple_pmap() -> ProvenanceMap:
    """Build a simple ProvenanceMap for serialization tests."""
    leaf_tool = _tp(
        PartKind.TOOL,
        "tool-lsp",
        "leaf-behavior",
        config={
            "module": "tool-lsp",
            "source": "git+https://example.com/tool-lsp@main",
        },
    )
    leaf_ctx = _tp(
        PartKind.CONTEXT,
        "leaf-behavior:lsp-config",
        "leaf-behavior",
        tokens=100,
        config={"path": "/tmp/lsp.md"},
        namespace_path="leaf-behavior:lsp-config",
    )

    leaf = BehaviorInfo(
        name="leaf-behavior",
        uri="git+https://example.com/leaf@main",
        parts=(leaf_tool, leaf_ctx),
        raw_parts=(leaf_tool, leaf_ctx),
        total_tokens=100,
        depth=1,
        include_chain=("root", "mid", "leaf-behavior"),
    )

    mid_tool = _tp(
        PartKind.TOOL,
        "python_check",
        "mid-behavior",
        config={
            "module": "python_check",
            "source": "git+https://example.com/python-check@main",
        },
    )
    mid = BehaviorInfo(
        name="mid-behavior",
        uri="git+https://example.com/mid@main",
        parts=(mid_tool,),
        raw_parts=(mid_tool,),
        total_tokens=0,
        depth=0,
        include_chain=("root", "mid-behavior"),
    )

    root_parts = (
        _tp(
            PartKind.TOOL,
            "tool-bash",
            None,
            config={
                "module": "tool-bash",
                "source": "git+https://example.com/tool-bash@main",
            },
        ),
        _tp(
            PartKind.TOOL,
            "tool-filesystem",
            None,
            config={
                "module": "tool-filesystem",
                "source": "git+https://example.com/tool-fs@main",
            },
        ),
        _tp(
            PartKind.TOOL,
            "tool-search",
            None,
            config={
                "module": "tool-search",
                "source": "git+https://example.com/tool-search@main",
            },
        ),
    )

    all_parts = root_parts + (leaf_tool, leaf_ctx, mid_tool)
    composed = Bundle(name="my-bundle")

    return ProvenanceMap(
        root_name="my-bundle",
        root_uri="my-bundle",
        root_instruction="You are a helpful assistant.",
        root_instruction_tokens=7,
        behaviors={"leaf-behavior": leaf, "mid-behavior": mid},
        root_parts=root_parts,
        all_parts=all_parts,
        composed_bundle=composed,
        include_order=("leaf-behavior", "mid-behavior"),
        session_config={
            "orchestrator": {
                "module": "loop-streaming",
                "source": "git+https://example.com/orch@main",
            }
        },
        spawn_config=None,
        _raw_bundles={},
    )


# ---------------------------------------------------------------------------
# .md format
# ---------------------------------------------------------------------------


def test_serialize_starts_with_frontmatter() -> None:
    pmap = _simple_pmap()
    content = serialize_bundle(pmap)
    assert content.startswith("---\n")


def test_serialize_has_closing_frontmatter() -> None:
    pmap = _simple_pmap()
    content = serialize_bundle(pmap)
    # Should have a closing --- on its own line separating YAML from body
    assert "\n---\n" in content


def test_serialize_bundle_name() -> None:
    pmap = _simple_pmap()
    content = serialize_bundle(pmap)
    assert "my-bundle" in content


def test_serialize_includes_behavior_uris() -> None:
    pmap = _simple_pmap()
    content = serialize_bundle(pmap)
    assert "git+https://example.com/mid@main" in content


def test_serialize_session_config() -> None:
    pmap = _simple_pmap()
    content = serialize_bundle(pmap)
    assert "loop-streaming" in content


def test_serialize_instruction_in_body() -> None:
    """Root instruction appears in markdown body, not YAML."""
    pmap = _simple_pmap()
    content = serialize_bundle(pmap)
    # Instruction should be AFTER the closing ---
    parts = content.split("---")
    # parts[0] is empty (before first ---), parts[1] is YAML, parts[2+] is body
    assert len(parts) >= 3
    body = "---".join(parts[2:])
    assert "You are a helpful assistant." in body


def test_serialize_root_tools_in_yaml() -> None:
    """Root-level tools appear in the YAML section."""
    pmap = _simple_pmap()
    content = serialize_bundle(pmap)
    assert "tool-bash" in content
    assert "tool-filesystem" in content


def test_serialize_no_absolute_paths() -> None:
    """Context paths should be namespace syntax, not absolute."""
    pmap = _simple_pmap()
    content = serialize_bundle(pmap)
    # Should not contain /tmp or /Users
    assert "/tmp/" not in content
    assert "/Users/" not in content


def test_serialize_context_uses_namespace() -> None:
    pmap = _simple_pmap()
    content = serialize_bundle(pmap)
    assert "leaf-behavior:lsp-config" in content


def test_serialize_no_instruction_in_yaml() -> None:
    """instruction: key should NOT be in the YAML frontmatter."""
    pmap = _simple_pmap()
    content = serialize_bundle(pmap)
    # Parse just the YAML part
    parts = content.split("---")
    yaml_text = parts[1]
    parsed = yaml.safe_load(yaml_text)
    assert "instruction" not in parsed


def test_serialize_valid_yaml_frontmatter() -> None:
    pmap = _simple_pmap()
    content = serialize_bundle(pmap)
    parts = content.split("---")
    yaml_text = parts[1]
    parsed = yaml.safe_load(yaml_text)
    assert isinstance(parsed, dict)
    assert "bundle" in parsed


def test_serialize_no_spawn_when_none() -> None:
    pmap = _simple_pmap()
    pmap.spawn_config = None
    content = serialize_bundle(pmap)
    parts = content.split("---")
    yaml_text = parts[1]
    parsed = yaml.safe_load(yaml_text)
    assert "spawn" not in parsed


def test_serialize_with_spawn() -> None:
    pmap = _simple_pmap()
    pmap.spawn_config = {"exclude_tools": ["tool-delegate"]}
    content = serialize_bundle(pmap)
    assert "exclude_tools" in content


def test_serialize_with_warnings() -> None:
    """Warnings are included as YAML comments prefixed with '# WARNING:'."""
    pmap = _simple_pmap()
    content = serialize_bundle(
        pmap, warnings=["hooks-todo-reminder requires tool-todo"]
    )
    assert "hooks-todo-reminder" in content
    assert "# WARNING: hooks-todo-reminder" in content
