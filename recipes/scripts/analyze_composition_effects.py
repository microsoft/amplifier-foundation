#!/usr/bin/env python3
"""Deterministic composition effects analyzer for Amplifier bundle manifests.

Reads the JSON manifest from parse_bundle_composition.py via stdin and computes
composition effects: tool availability, delegation necessity, composition
loopholes, skill-mode associations, and agent-mode compatibility.

Usage:
    python parse_bundle_composition.py <bundle> <registry> | \\
        python analyze_composition_effects.py
"""

from __future__ import annotations

import json
import sys
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# All common Amplifier tools against which modes are evaluated.
ALL_TOOLS: list[str] = [
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "bash",
    "delegate",
    "recipes",
    "mode",
    "todo",
    "load_skill",
    "LSP",
    "python_check",
    "web_search",
    "web_fetch",
    "team_knowledge",
    "apply_patch",
]

# Operation categories mapped to the tools that enable them.
_OP_TOOLS: dict[str, list[str]] = {
    "file_modification": ["write_file", "edit_file"],
    "code_execution": ["bash"],
    "exploration": ["read_file", "glob", "grep"],
    "git_operations": ["bash"],
    "web_access": ["web_search", "web_fetch"],
    "skill_loading": ["load_skill"],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_mode_tools(
    mode_data: dict[str, Any],
) -> tuple[list[str], list[str], list[str], list[str], str]:
    """Return (safe, warn, confirm, explicit_block, default_action) from mode data.

    Handles both the ``tool_policies`` key (used in test fixtures) and the
    ``tool_categories`` key (emitted by parse_bundle_composition.py).
    """
    tool_data: dict[str, Any] = (
        mode_data.get("tool_policies") or mode_data.get("tool_categories") or {}
    )
    safe: list[str] = list(tool_data.get("safe") or [])
    warn: list[str] = list(tool_data.get("warn") or [])
    confirm: list[str] = list(tool_data.get("confirm") or [])
    block: list[str] = list(tool_data.get("block") or [])
    default_action: str = mode_data.get("default_action", "warn")
    return safe, warn, confirm, block, default_action


def _classify_tool(
    tool: str,
    safe: list[str],
    warn: list[str],
    confirm: list[str],
    block: list[str],
    default_action: str,
) -> str:
    """Return 'safe', 'warn', 'confirm', 'block', or the default_action for a tool."""
    if tool in safe:
        return "safe"
    if tool in warn:
        return "warn"
    if tool in confirm:
        return "confirm"
    if tool in block:
        return "block"
    return default_action


def _op_status(
    op: str,
    safe: list[str],
    warn: list[str],
    confirm: list[str],
    block: list[str],
    default_action: str,
    has_git_agent: bool = False,
) -> str:
    """Produce a human-readable status string for an operation."""
    tools = _OP_TOOLS[op]
    statuses = {
        _classify_tool(t, safe, warn, confirm, block, default_action) for t in tools
    }

    if op == "file_modification":
        if statuses <= {"block"}:
            return "MUST delegate (write_file/edit_file blocked)"
        if "safe" in statuses:
            return "CAN do directly (write_file/edit_file safe)"
        if "warn" in statuses:
            return "WARN (write_file/edit_file warns, can proceed after warning)"
        return "MUST delegate (write_file/edit_file blocked)"

    if op == "code_execution":
        s = _classify_tool("bash", safe, warn, confirm, block, default_action)
        if s == "safe":
            return "CAN do directly (bash safe)"
        if s == "warn":
            return "WARN (bash warns, can proceed after warning)"
        return "MUST delegate (bash blocked)"

    if op == "exploration":
        if all(
            _classify_tool(t, safe, warn, confirm, block, default_action) == "safe"
            for t in tools
        ):
            return "CAN do directly (read_file, glob, grep safe)"
        any_safe = any(
            _classify_tool(t, safe, warn, confirm, block, default_action) == "safe"
            for t in tools
        )
        if any_safe:
            return "PARTIAL (some exploration tools safe)"
        return "MUST delegate (exploration tools blocked)"

    if op == "git_operations":
        bash_s = _classify_tool("bash", safe, warn, confirm, block, default_action)
        git_suffix = " (bash warns, git-ops agent available)" if has_git_agent else ""
        if bash_s == "safe":
            return "CAN do directly (bash safe)"
        if bash_s == "warn":
            return f"WARN{git_suffix}"
        return "MUST delegate (bash blocked)"

    if op == "web_access":
        if all(
            _classify_tool(t, safe, warn, confirm, block, default_action) == "safe"
            for t in tools
        ):
            return "CAN do directly (web_search/web_fetch safe)"
        any_safe = any(
            _classify_tool(t, safe, warn, confirm, block, default_action) == "safe"
            for t in tools
        )
        if any_safe:
            return "PARTIAL (some web tools safe)"
        return "MUST delegate (web tools blocked)"

    if op == "skill_loading":
        s = _classify_tool("load_skill", safe, warn, confirm, block, default_action)
        if s == "safe":
            return "CAN do directly (load_skill safe)"
        if s == "warn":
            return "WARN (load_skill warns)"
        return "MUST delegate (load_skill blocked)"

    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Name-overlap skill ↔ mode matching
# ---------------------------------------------------------------------------


def _normalize_tokens(name: str) -> list[str]:
    """Split a kebab-case name into lowercased tokens."""
    return [t.lower() for t in name.split("-") if t]


def _tokens_related(a: str, b: str, min_len: int = 4, ratio: float = 0.8) -> bool:
    """Return True if token *a* and token *b* share a significant common prefix.

    Computes the length of the shared prefix and checks whether the ratio
    (prefix_len / shorter_token_len) is >= *ratio*.  This handles inflection
    variants such as "write"/"writing" and "execute"/"executing".

    Short tokens (< min_len chars) must match exactly to avoid false positives.
    """
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < min_len:
        return False
    common = 0
    for ca, cb in zip(short, long_):
        if ca == cb:
            common += 1
        else:
            break
    return (common / len(short)) >= ratio


def _names_overlap(mode_name: str, skill_name: str) -> bool:
    """Return True if mode_name and skill_name have significant token overlap."""
    m_tokens = _normalize_tokens(mode_name)
    s_tokens = _normalize_tokens(skill_name)
    if not m_tokens or not s_tokens:
        return False

    matched_m = 0
    for mt in m_tokens:
        for st in s_tokens:
            if _tokens_related(mt, st):
                matched_m += 1
                break

    # Overlap ratio relative to the shorter token list
    shorter = min(len(m_tokens), len(s_tokens))
    return matched_m >= max(1, shorter)


# ---------------------------------------------------------------------------
# Main computation functions
# ---------------------------------------------------------------------------


def compute_tool_availability_matrix(
    manifests: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build the tool availability matrix for every mode in manifests."""
    matrix: dict[str, dict[str, Any]] = {}
    for m in manifests:
        if m.get("component_type") != "mode":
            continue
        mode_data: dict[str, Any] = m.get("mode") or {}
        name: str = mode_data.get("name", "")
        if not name:
            continue
        safe, warn, confirm, block, default_action = _extract_mode_tools(mode_data)

        # Implicitly blocked: all common tools not categorised by any list
        # when default_action is block.
        categorised = set(safe) | set(warn) | set(confirm) | set(block)
        blocked_by_default: list[str] = (
            [t for t in ALL_TOOLS if t not in categorised]
            if default_action == "block"
            else []
        )

        matrix[name] = {
            "safe": safe,
            "warn": warn,
            "confirm": confirm,
            "blocked_by_default": blocked_by_default,
            "default_action": default_action,
        }
    return matrix


def compute_delegation_necessity_map(
    manifests: list[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    """Build the delegation necessity map for every mode in manifests."""
    # Check whether a git-ops style agent exists
    agent_names: set[str] = set()
    for m in manifests:
        if m.get("component_type") == "agent":
            agent_data: dict[str, Any] = m.get("agent") or {}
            aname = agent_data.get("name", "")
            if aname:
                agent_names.add(aname)
    has_git_agent = any("git" in n.lower() for n in agent_names)

    result: dict[str, dict[str, str]] = {}
    for m in manifests:
        if m.get("component_type") != "mode":
            continue
        mode_data: dict[str, Any] = m.get("mode") or {}
        name: str = mode_data.get("name", "")
        if not name:
            continue
        safe, warn, confirm, block, default_action = _extract_mode_tools(mode_data)

        entry: dict[str, str] = {}
        for op in _OP_TOOLS:
            entry[op] = _op_status(
                op,
                safe,
                warn,
                confirm,
                block,
                default_action,
                has_git_agent=(has_git_agent and op == "git_operations"),
            )
        result[name] = entry
    return result


def compute_composition_loopholes(
    manifests: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Identify modes where delegation/recipe tools bypass write/bash blocks."""
    loopholes: list[dict[str, str]] = []
    for m in manifests:
        if m.get("component_type") != "mode":
            continue
        mode_data: dict[str, Any] = m.get("mode") or {}
        name: str = mode_data.get("name", "")
        if not name:
            continue
        safe, warn, confirm, block, default_action = _extract_mode_tools(mode_data)

        # Determine restricted tools (blocked or default-blocked)
        categorised = set(safe) | set(warn) | set(confirm) | set(block)
        implicitly_blocked = (
            {t for t in ALL_TOOLS if t not in categorised}
            if default_action == "block"
            else set()
        )
        all_blocked = set(block) | implicitly_blocked
        all_warned = set(warn)

        restricted_write = bool(
            {"write_file", "edit_file"} & (all_blocked | all_warned)
        )
        restricted_bash = bool({"bash"} & (all_blocked | all_warned))

        if not (restricted_write or restricted_bash):
            continue

        for bypass_tool in ("delegate", "recipes"):
            if bypass_tool not in safe:
                continue
            if bypass_tool == "recipes":
                loophole_msg = (
                    f"{bypass_tool} tool is safe, enabling launch of "
                    "subagent-driven-development which performs full "
                    "implementation via sub-sessions that bypass "
                    "write_file/edit_file blocks"
                )
                enables_msg = (
                    "Full implementation pipeline (implementer agents in "
                    "sub-sessions have unrestricted tool access)"
                )
            else:
                loophole_msg = (
                    f"{bypass_tool} tool is safe, allowing delegation to "
                    "sub-agents that run in their own sessions with "
                    "unrestricted tool access"
                )
                enables_msg = (
                    "Sub-agent execution with full tool access, bypassing "
                    "calling mode's restrictions"
                )
            loopholes.append(
                {
                    "mode": name,
                    "loophole": loophole_msg,
                    "safe_tool": bypass_tool,
                    "enables": enables_msg,
                }
            )
    return loopholes


def compute_skill_mode_associations(
    manifests: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Match skills to modes using name-token overlap heuristics."""
    modes: list[str] = []
    skills: list[str] = []

    for m in manifests:
        ct = m.get("component_type")
        if ct == "mode":
            mode_data: dict[str, Any] = m.get("mode") or {}
            n = mode_data.get("name", "")
            if n:
                modes.append(n)
        elif ct == "skill":
            skill_data: dict[str, Any] = m.get("skill") or {}
            n = skill_data.get("name", "")
            if n:
                skills.append(n)

    associations: list[dict[str, str]] = []
    for mode_name in modes:
        for skill_name in skills:
            if _names_overlap(mode_name, skill_name):
                associations.append(
                    {
                        "mode": mode_name,
                        "likely_skill": skill_name,
                        "match_type": "name_overlap",
                    }
                )
    return associations


def compute_agent_mode_compatibility(
    manifests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Check if agents mentioned in modes can run with that mode's tool access."""
    # Collect agents with their tool module info
    agents: list[dict[str, Any]] = []
    for m in manifests:
        if m.get("component_type") != "agent":
            continue
        agent_data: dict[str, Any] = m.get("agent") or {}
        aname = agent_data.get("name", "")
        if not aname:
            continue
        # Bundle origin gives the agent's qualified name (e.g. foundation:explorer)
        bundle = m.get("bundle_origin", "")
        qualified = f"{bundle}:{aname}" if bundle else aname
        tool_modules: list[dict[str, Any]] = agent_data.get("tool_modules") or []
        agents.append(
            {
                "name": aname,
                "qualified": qualified,
                "tool_modules": tool_modules,
            }
        )

    if not agents:
        return []

    # Build a set of mode names and their descriptions / mentions
    mode_entries: list[dict[str, Any]] = []
    for m in manifests:
        if m.get("component_type") != "mode":
            continue
        mode_data: dict[str, Any] = m.get("mode") or {}
        mname = mode_data.get("name", "")
        if not mname:
            continue
        mentions = m.get("eager_mentions") or []
        description = m.get("description") or ""
        safe, warn, confirm, block, default_action = _extract_mode_tools(mode_data)
        mode_entries.append(
            {
                "name": mname,
                "mentions": mentions,
                "description": description,
                "safe": safe,
                "warn": warn,
                "confirm": confirm,
                "block": block,
                "default_action": default_action,
            }
        )

    compat: list[dict[str, Any]] = []
    for agent in agents:
        mentioned_in: list[str] = []
        for me in mode_entries:
            # Check if agent name appears in mode description or mentions
            desc_lower = me["description"].lower()
            if (
                agent["name"] in desc_lower
                or agent["qualified"] in desc_lower
                or any(agent["name"] in mn for mn in me["mentions"])
                or any(agent["qualified"] in mn for mn in me["mentions"])
            ):
                mentioned_in.append(me["name"])

        if not mentioned_in:
            continue

        # Determine required tools from tool_modules (module names → tool names)
        # tool_modules use module keys like "tool-read-file" → "read_file"
        requires: list[str] = []
        for tm in agent["tool_modules"]:
            mod = tm.get("module", "")
            # Map common module names to their tool function name
            tool_name = _module_to_tool(mod)
            if tool_name and tool_name not in requires:
                requires.append(tool_name)

        # Check which of those are blocked in the mentioned modes
        blocked_in: list[str] = []
        for mname in mentioned_in:
            me = next((x for x in mode_entries if x["name"] == mname), None)
            if not me:
                continue
            safe = me["safe"]
            warn = me["warn"]
            confirm = me["confirm"]
            block = me["block"]
            default_action = me["default_action"]
            categorised = set(safe) | set(warn) | set(confirm) | set(block)
            implicitly_blocked = (
                {t for t in ALL_TOOLS if t not in categorised}
                if default_action == "block"
                else set()
            )
            all_blocked = set(block) | implicitly_blocked
            for rt in requires:
                if rt in all_blocked and mname not in blocked_in:
                    blocked_in.append(mname)

        note = (
            f"{agent['name']} runs as sub-session with own tool access"
            if blocked_in
            else f"{agent['name']} tools are compatible with mentioned modes"
        )

        compat.append(
            {
                "agent": agent["qualified"],
                "mentioned_in_modes": mentioned_in,
                "requires_tools": requires,
                "blocked_in_modes": blocked_in,
                "note": note,
            }
        )
    return compat


def _module_to_tool(module: str) -> str | None:
    """Map a tool module name to its Amplifier tool function name."""
    _MAP: dict[str, str] = {
        "tool-read-file": "read_file",
        "tool-write-file": "write_file",
        "tool-edit-file": "edit_file",
        "tool-glob": "glob",
        "tool-grep": "grep",
        "tool-bash": "bash",
        "tool-delegate": "delegate",
        "tool-recipes": "recipes",
        "tool-mode": "mode",
        "tool-todo": "todo",
        "tool-load-skill": "load_skill",
        "tool-lsp": "LSP",
        "tool-python-check": "python_check",
        "tool-web-search": "web_search",
        "tool-web-fetch": "web_fetch",
        "tool-team-knowledge": "team_knowledge",
        "tool-apply-patch": "apply_patch",
    }
    # Try direct lookup first, then strip common prefixes and try fuzzy
    if module in _MAP:
        return _MAP[module]
    # Normalise: lowercase, replace hyphens with underscores
    norm = module.lower().replace("-", "_")
    if norm in {t.lower() for t in ALL_TOOLS}:
        # Find case-matching entry
        for t in ALL_TOOLS:
            if t.lower() == norm:
                return t
    return None


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def analyze(manifest: dict[str, Any]) -> dict[str, Any]:
    """Compute all composition effects from a bundle manifest dict."""
    manifests: list[dict[str, Any]] = manifest.get("manifests") or []
    return {
        "tool_availability_matrix": compute_tool_availability_matrix(manifests),
        "delegation_necessity_map": compute_delegation_necessity_map(manifests),
        "composition_loopholes": compute_composition_loopholes(manifests),
        "skill_mode_associations": compute_skill_mode_associations(manifests),
        "agent_mode_compatibility": compute_agent_mode_compatibility(manifests),
    }


def main() -> None:
    """Read JSON manifest from stdin and write composition effects JSON to stdout."""
    raw = sys.stdin.read()
    manifest = json.loads(raw)
    result = analyze(manifest)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
