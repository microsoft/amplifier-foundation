"""Bundle serialization — save to .md format with YAML frontmatter.

Uses the include-reference pattern: behaviors are referenced by URI,
not dumped flat. Context paths use namespace syntax, not absolute paths.
"""

from __future__ import annotations

import yaml

from .models import PartKind, ProvenanceMap


def serialize_bundle(
    pmap: ProvenanceMap,
    *,
    warnings: list[str] | None = None,
) -> str:
    """Serialize a ProvenanceMap to .md bundle format.

    Returns a string with YAML frontmatter + markdown instruction body.

    Parameters
    ----------
    pmap:
        The ProvenanceMap containing all bundle composition data.
    warnings:
        Optional list of warning strings to include as YAML comments
        prefixed with '# WARNING:'.

    Returns
    -------
    str
        Output format: '---\\n{warning_lines}{yaml}---\\n{body}'
        The YAML frontmatter contains bundle metadata, includes, session,
        spawn, tools, hooks, providers, agents, and context (namespace syntax).
        The markdown body contains the root instruction (if any).
    """
    data: dict = {}

    # Bundle metadata
    data["bundle"] = {"name": pmap.root_name, "version": "1.0.0"}

    # Includes — reference behaviors by URI (depth-0 only; sub-behaviors
    # are pulled in transitively when Foundation loads the bundle).
    includes: list[dict[str, str]] = []
    for beh_key in pmap.include_order:
        if beh_key in pmap.behaviors:
            beh = pmap.behaviors[beh_key]
            # Only include top-level behaviors (depth 0).
            # Sub-behaviors (depth >= 1) are pulled in transitively.
            if beh.depth == 0:
                includes.append({"bundle": beh.uri})
    if includes:
        data["includes"] = includes

    # Session config (infrastructure — always in root)
    if pmap.session_config:
        data["session"] = pmap.session_config

    # Spawn config (omit if None)
    if pmap.spawn_config:
        data["spawn"] = pmap.spawn_config

    # Root-level tools
    root_tools = [
        dict(p.config)
        for p in pmap.root_parts
        if p.kind == PartKind.TOOL and p.config.get("module")
    ]
    if root_tools:
        data["tools"] = root_tools

    # Root-level hooks
    root_hooks = [
        dict(p.config)
        for p in pmap.root_parts
        if p.kind == PartKind.HOOK and p.config.get("module")
    ]
    if root_hooks:
        data["hooks"] = root_hooks

    # Root-level providers
    root_providers = [
        dict(p.config)
        for p in pmap.root_parts
        if p.kind == PartKind.PROVIDER and p.config.get("module")
    ]
    if root_providers:
        data["providers"] = root_providers

    # Root-level agents
    root_agents = {
        p.name: dict(p.config) for p in pmap.root_parts if p.kind == PartKind.AGENT
    }
    if root_agents:
        data["agents"] = root_agents

    # Context — use namespace syntax (never absolute paths).
    # Uses namespace_path when available, falls back to the part name.
    # Absolute paths are never written to the output.
    context_refs: list[str] = []
    for part in pmap.all_parts:
        if part.kind == PartKind.CONTEXT:
            if part.namespace_path:
                context_refs.append(part.namespace_path)
            elif part.name:
                context_refs.append(part.name)
    if context_refs:
        data["context"] = {"include": context_refs}

    # Build YAML frontmatter
    yaml_str = yaml.dump(
        data, default_flow_style=False, sort_keys=False, allow_unicode=True
    )

    # Build warning comments (prefixed with '# WARNING:')
    warning_lines = ""
    if warnings:
        warning_lines = "\n".join(f"# WARNING: {w}" for w in warnings) + "\n"

    # Build markdown body — instruction goes AFTER the closing '---'
    body = ""
    if pmap.root_instruction:
        body = f"\n{pmap.root_instruction}\n"

    return f"---\n{warning_lines}{yaml_str}---\n{body}"
