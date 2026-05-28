#!/usr/bin/env python3
"""
Mechanical bundle generator for eval strategies.

Reads a JSON spec (from design step) + boilerplate header (infrastructure
YAML fragment), writes all bundle files deterministically.

No LLM. No token overflow. Produces output identical to hand-authored
bundles like critic-pipeline.

Usage:
    python3 generate_bundle.py <spec.json> <boilerplate.header> <bundle_dir>

Exit codes:
    0 = all structural checks pass
    1 = validation failure (details in stdout JSON)
"""

import json
import os
import sys

WORDS_PER_TOKEN = 0.75  # Approximate; varies by content

# Known behavior include URLs (optional includes the expert can select)
INCLUDE_URLS = {
    "apply-patch": "git+https://github.com/microsoft/amplifier-bundle-filesystem@main#subdirectory=behaviors/apply-patch.yaml",
}


def generate(spec_path: str, boilerplate_path: str, bundle_dir: str) -> dict:
    with open(spec_path) as f:
        spec = json.load(f)
    with open(boilerplate_path) as f:
        boilerplate = f.read().strip()

    name = spec["strategy_name"]
    desc = spec["description"]
    agents = spec["agents"]
    system_prompt = spec["system_prompt"]
    tool_sources = spec.get("tool_sources", {})
    root_tools = spec.get("root_tools", [])
    optional_includes = spec.get("optional_includes", [])
    delegate_config = spec.get("delegate_config", {})

    os.makedirs(os.path.join(bundle_dir, "context"), exist_ok=True)
    os.makedirs(os.path.join(bundle_dir, "agents"), exist_ok=True)

    # 1. context/system.md — verbatim from spec
    with open(os.path.join(bundle_dir, "context", "system.md"), "w") as f:
        f.write(system_prompt.strip() + "\n")

    # 2. Agent files
    for agent in agents:
        _write_agent(bundle_dir, name, agent, tool_sources)

    # 3. bundle.md — stitches boilerplate + spec-driven tools/includes + agent refs
    _write_bundle(
        bundle_dir,
        name,
        desc,
        boilerplate,
        agents,
        root_tools,
        tool_sources,
        optional_includes,
        delegate_config,
    )

    # 4. Structural validation
    results = _validate(
        bundle_dir, name, agents, system_prompt, root_tools, optional_includes
    )

    # 5. Cleanup temp files left by recipe steps
    for tmp in ["_spec.json", "bundle.md.header"]:
        p = os.path.join(bundle_dir, tmp)
        if os.path.exists(p):
            os.remove(p)

    return results


def _build_tools_block(
    root_tools: list, tool_sources: dict, delegate_config: dict
) -> str:
    """Build the YAML tools: block from spec-driven root_tools."""
    if not root_tools:
        return ""

    lines = ["tools:"]
    for tool in root_tools:
        src = tool_sources.get(tool, "")
        lines.append(f"  - module: {tool}")
        if src:
            lines.append(f"    source: {src}")

        # Known configs for specific tools
        if tool == "tool-skills":
            lines.extend(
                [
                    "    config:",
                    "      skills:",
                    '        - "git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=skills"',
                    "      visibility:",
                    "        enabled: false",
                ]
            )
        elif tool == "tool-delegate":
            max_turns = delegate_config.get("context_inheritance_max_turns", 10)
            exclude = delegate_config.get("exclude_tools", ["tool-delegate"])
            exclude_yaml = "[" + ", ".join(exclude) + "]"
            lines.extend(
                [
                    "    config:",
                    "      features:",
                    "        self_delegation:",
                    "          enabled: true",
                    "        session_resume:",
                    "          enabled: true",
                    "        context_inheritance:",
                    "          enabled: true",
                    f"          max_turns: {max_turns}",
                    "        provider_selection:",
                    "          enabled: true",
                    "      settings:",
                    f"        exclude_tools: {exclude_yaml}",
                ]
            )

    return "\n".join(lines)


def _build_optional_includes(optional_includes: list) -> str:
    """Build YAML include lines for optional behavior includes."""
    lines = []
    for inc in optional_includes:
        url = INCLUDE_URLS.get(inc, "")
        if url:
            lines.append(f"  - bundle: {url}")
    return "\n".join(lines)


def _build_todo_hooks_block() -> str:
    """Build the YAML block for todo-related hooks."""
    return """  - module: hooks-todo-reminder
    source: git+https://github.com/microsoft/amplifier-module-hooks-todo-reminder@main
    config:
      inject_role: user
      priority: 10
  - module: hooks-todo-display
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/hooks-todo-display
    config:
      show_progress_bar: true
      show_border: true"""


def _inject_into_boilerplate(
    boilerplate: str,
    tools_block: str,
    optional_includes_text: str,
    root_tools: list,
) -> str:
    """Inject spec-driven tools block, optional includes, and conditional hooks into boilerplate."""
    result = boilerplate

    # 1. Insert optional includes (contiguous with existing includes)
    if optional_includes_text:
        idx = result.find("\nsession:")
        if idx > 0:
            before = result[:idx].rstrip("\n")
            result = before + "\n" + optional_includes_text + "\n" + result[idx:]

    # 2. Insert tools block (before hooks:, after session block)
    if tools_block:
        idx = result.find("\nhooks:")
        if idx > 0:
            before = result[:idx].rstrip("\n")
            result = before + "\n\n" + tools_block + "\n" + result[idx:]

    # 3. Insert todo hooks (after hooks: line) only if tool-todo in root_tools
    if "tool-todo" in root_tools:
        idx = result.find("\nhooks:")
        if idx > 0:
            hooks_line_end = result.index("\n", idx + 1)
            before = result[:hooks_line_end]
            after = result[hooks_line_end:]
            result = before + "\n" + _build_todo_hooks_block() + after

    return result


def _write_bundle(
    bundle_dir: str,
    name: str,
    desc: str,
    boilerplate: str,
    agents: list,
    root_tools: list,
    tool_sources: dict,
    optional_includes: list,
    delegate_config: dict,
):
    agent_includes = "\n".join(f"    - {name}:agents/{a['name']}" for a in agents)

    # Build spec-driven blocks
    tools_block = _build_tools_block(root_tools, tool_sources, delegate_config)
    opt_includes = _build_optional_includes(optional_includes)

    # Inject into boilerplate
    infrastructure = _inject_into_boilerplate(
        boilerplate, tools_block, opt_includes, root_tools
    )

    content = f"""---
bundle:
  name: {name}
  version: 0.1.0
  description: |
    {desc}

{infrastructure}

agents:
  include:
{agent_includes}
---

# {name.replace("-", " ").title()}

{desc}

@{name}:context/system.md
"""
    with open(os.path.join(bundle_dir, "bundle.md"), "w") as f:
        f.write(content)


def _write_agent(bundle_dir: str, bundle_name: str, agent: dict, tool_sources: dict):
    name = agent["name"]
    role = agent["role"]
    model_role = agent.get("model_role", "general")
    tools = agent.get("tools", [])
    use_when = agent.get("use_when", "")
    not_when = agent.get("not_when", "")
    rules = agent.get("rules", [])
    output_contract = agent.get("output_contract", "")

    # model_role: list or scalar
    if isinstance(model_role, list):
        mr = "[" + ", ".join(model_role) + "]"
    else:
        mr = model_role

    # Tools block (only for agents that have specific tools)
    tools_block = ""
    if tools:
        lines = []
        for t in tools:
            src = tool_sources.get(t, "")
            lines.append(f"  - module: {t}")
            if src:
                lines.append(f"    source: {src}")
        tools_block = "\ntools:\n" + "\n".join(lines)

    # Rules as numbered list
    rules_md = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(rules))

    # Strip trailing periods to avoid double-periods
    role = role.rstrip(".")
    use_when = use_when.rstrip(".")
    not_when = not_when.rstrip(".")

    content = f"""---
meta:
  name: {name}
  description: |
    {role}.
    USE WHEN: {use_when}.
    DO NOT USE WHEN: {not_when}.
  model_role: {mr}
{tools_block}
---

# {name.replace("-", " ").title()}

## Rules

{rules_md}

## Output

{output_contract}
"""
    with open(os.path.join(bundle_dir, "agents", f"{name}.md"), "w") as f:
        f.write(content)


def _validate(
    bundle_dir: str,
    name: str,
    agents: list,
    system_prompt: str,
    root_tools: list,
    optional_includes: list,
) -> dict:
    results = {}
    with open(os.path.join(bundle_dir, "bundle.md")) as f:
        content = f.read()

    # UX includes (always required)
    for inc in ["streaming-ui", "status-context", "redaction", "logging"]:
        results[f"include:{inc}"] = inc in content

    # Optional includes (only validate if in spec)
    for inc in optional_includes:
        results[f"include:{inc}"] = inc in content

    # Session config
    results["session:raw"] = "raw: true" in content
    results["session:orchestrator"] = "loop-streaming" in content
    results["session:context"] = "context-simple" in content

    # Hooks — todo hooks only if tool-todo is in root_tools
    if "tool-todo" in root_tools:
        for hook in ["hooks-todo-reminder", "hooks-todo-display"]:
            results[f"hook:{hook}"] = hook in content
    for hook in ["hooks-session-naming", "hooks-approval"]:
        results[f"hook:{hook}"] = hook in content

    # System prompt token estimate
    words = len(system_prompt.split())
    token_est = int(words / WORDS_PER_TOKEN)
    results["system_prompt:token_estimate"] = token_est
    results["system_prompt:under_300"] = token_est <= 300

    # Agent files + includes
    for a in agents:
        aname = a["name"]
        results[f"agent:{aname}:file_exists"] = os.path.exists(
            os.path.join(bundle_dir, "agents", f"{aname}.md")
        )
        results[f"agent:{aname}:included"] = f"{name}:agents/{aname}" in content

    failures = [k for k, v in results.items() if v is False]
    results["_pass"] = len(failures) == 0
    results["_failures"] = failures
    return results


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(
            f"Usage: {sys.argv[0]} <spec.json> <boilerplate> <bundle_dir>",
            file=sys.stderr,
        )
        sys.exit(1)
    results = generate(sys.argv[1], sys.argv[2], sys.argv[3])
    print(json.dumps(results, indent=2))
    if not results["_pass"]:
        print(f"\nFAILED: {results['_failures']}", file=sys.stderr)
        sys.exit(1)
    print("\nPASS: All structural checks passed.", file=sys.stderr)
