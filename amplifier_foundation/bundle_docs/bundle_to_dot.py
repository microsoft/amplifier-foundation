"""Bundle/behavior YAML to DOT repository diagrams.

Generates repository-wide Graphviz DOT diagrams showing all bundle and behavior
files in a repository, with 7 cluster categories, edges representing include
relationships, and nodes annotated with per-request token cost estimates.

Usage::

    from amplifier_foundation.bundle_docs.bundle_to_dot import bundle_repo_dot
    dot_str = bundle_repo_dot(repo_root=Path("."))

No LLM calls — pure deterministic function.  Only requires PyYAML and the
Python standard library.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .frontmatter import (
    extract_mentions,
    parse_frontmatter,
    resolve_local_mention,
)
from .token_cost import (
    estimate_tokens,
)
from .tool_schema import estimate_module_tool_tokens

# ── Visual constants ───────────────────────────────────────────────────────────

_COLOR_BUNDLE_ROOT = "#80cbc4"
_COLOR_BEHAVIOR_LOCAL = "#e0f2f1"
_COLOR_EXTERNAL_INCLUDE = "#f5f5f5"
_COLOR_TOOL = "#bbdefb"
_COLOR_AGENT_BASE = "#c8e6c9"
_COLOR_HOOK = "#ffe0b2"
_COLOR_CONTEXT = "#e1bee7"
_COLOR_SESSION = "#e0e0e0"
_COLOR_CLUSTER_FILL = "#f9f9f9"
_COLOR_CLUSTER_BORDER = "#999999"
_COLOR_LEGEND_FILL = "white"
_COLOR_LEGEND_BORDER = "#cccccc"
_COLOR_SUMMARY = "#eceff1"

# New B3 colors
_COLOR_STANDALONE = "#80cbc4"
_COLOR_EXPERIMENT = "#e1bee7"
_COLOR_PROVIDER = "#e0e0e0"
_COLOR_EXTERNAL_COST = "red"
_COLOR_EXTERNAL_MUTED = "#f5f5f5"
_MUTED_COLORS = {
    "behavior": "#f5fafa",
    "agent": "#e8f5e9",
    "module": "#e3f2fd",
    "provider": "#f5f5f5",
    "context": "#f3e5f5",
}


# ── Public API ─────────────────────────────────────────────────────────────────


def bundle_repo_dot(repo_root: str | Path) -> str:
    """Generate a full B3-enhanced DOT diagram for a bundle repository.

    Discovers all components across 7 cluster categories matching the
    validate-bundle-repo Phase 1 discovery: behaviors, standalones, agents,
    modules, providers, experiments, and context files.

    Node labels are annotated with token cost estimates. External references
    are visually distinguished by cost impact. Edges carry verb labels
    (composes, owns, uses, extends, forks).

    Args:
        repo_root: Repository root to scan for bundle and behavior files.

    Returns:
        Complete, valid DOT string suitable for passing to ``dot -Tsvg``.
    """
    repo_root = Path(repo_root)
    root_bundle = repo_root / "bundle.md"

    # ── Discovery ──────────────────────────────────────────────────────────────

    # 1. Behaviors: behaviors/*.yaml
    behaviors: list[Path] = []
    beh_dir = repo_root / "behaviors"
    if beh_dir.is_dir():
        for p in sorted(beh_dir.glob("*.yaml")):
            behaviors.append(p)
        for p in sorted(beh_dir.glob("*.yml")):
            behaviors.append(p)

    # 2. Standalones: bundles/*.yaml + bundles/*.md
    standalones: list[Path] = []
    bun_dir = repo_root / "bundles"
    if bun_dir.is_dir():
        for p in sorted(bun_dir.glob("*.yaml")) + sorted(bun_dir.glob("*.md")):
            standalones.append(p)

    # 3. Agents: agents/*.md only
    agents: list[Path] = []
    agt_dir = repo_root / "agents"
    if agt_dir.is_dir():
        for p in sorted(agt_dir.glob("*.md")):
            agents.append(p)

    # 4. Modules: modules/*/ dirs with amplifier_module_*/__init__.py
    modules: list[Path] = []
    mod_dir = repo_root / "modules"
    if mod_dir.is_dir():
        for d in sorted(mod_dir.iterdir()):
            if d.is_dir() and list(d.glob("amplifier_module_*/__init__.py")):
                modules.append(d)

    # 5. Providers: providers/*.yaml
    providers: list[Path] = []
    prov_dir = repo_root / "providers"
    if prov_dir.is_dir():
        for p in sorted(prov_dir.glob("*.yaml")):
            providers.append(p)

    # 6. Experiments: experiments/*.yaml + experiments/*.md + experiments/*/bundle.md
    experiments: list[Path] = []
    exp_dir = repo_root / "experiments"
    if exp_dir.is_dir():
        for p in sorted(exp_dir.glob("*.yaml")) + sorted(exp_dir.glob("*.md")):
            experiments.append(p)
        for d in sorted(exp_dir.iterdir()):
            if d.is_dir():
                bundle_md = d / "bundle.md"
                if bundle_md.exists():
                    experiments.append(bundle_md)

    # 7. Context files: from behaviors' context.include + root bundle @mentions
    # resolved_path -> ref string for display
    context_files: dict[Path, str] = {}
    _collect_context_files_all(root_bundle, behaviors, repo_root, context_files)

    # ── Parse root bundle ──────────────────────────────────────────────────────

    root_name = "bundle"
    root_version = ""
    root_data: dict = {}
    root_body = ""
    if root_bundle.exists():
        try:
            root_data, root_body = parse_frontmatter(root_bundle)
            bundle_meta = root_data.get("bundle") or {}
            root_name = bundle_meta.get("name") or "bundle"
            root_version = str(bundle_meta.get("version") or "")
        except Exception:
            pass

    root_tools_list = root_data.get("tools") or []
    root_agents_list = (root_data.get("agents") or {}).get("include") or []
    n_root_tools = len(root_tools_list)
    n_root_agents = len(root_agents_list)

    # Root aggregate: own tokens + agent descriptions + context @mentions in body
    root_content = (
        root_bundle.read_text(encoding="utf-8") if root_bundle.exists() else ""
    )
    root_own_tok = estimate_tokens(root_content)
    root_agent_tok = sum(_estimate_agent_tokens(a, repo_root) for a in root_agents_list)
    root_ctx_tok = 0
    ctx_seen: set[Path] = set()
    for mention in extract_mentions(root_body):
        root_ctx_tok += _estimate_context_tokens_recursive(mention, repo_root, ctx_seen)
    root_agg_tok = root_own_tok + root_agent_tok + root_ctx_tok

    version_str = f" v{root_version}" if root_version else ""
    root_label = (
        f"{root_name}{version_str}\\n"
        f"{n_root_tools} tools · {n_root_agents} agents\\n"
        f"~{root_agg_tok} tok aggregate"
    )
    root_id = _sanitize_id(f"root_{root_name}")

    # ── Build node and edge lines ──────────────────────────────────────────────

    lines: list[str] = []
    edge_lines: list[str] = []

    # Root node (not in a cluster)
    lines.append(
        f"    {root_id} [label={_q(root_label)}, shape=box,"
        f' fillcolor="{_COLOR_BUNDLE_ROOT}", style="filled,rounded,bold", penwidth=2]'
    )
    lines.append("")

    # ── Cluster: behaviors ─────────────────────────────────────────────────────

    if behaviors:
        lines.append("    subgraph cluster_behaviors {")
        lines.append('        label="Behaviors"')
        lines.append('        style="filled"')
        lines.append(f'        fillcolor="{_COLOR_CLUSTER_FILL}"')
        lines.append(f'        color="{_COLOR_CLUSTER_BORDER}"')
        lines.append("")

        for beh_path in behaviors:
            beh_data: dict = {}
            beh_body = ""
            try:
                beh_data, beh_body = parse_frontmatter(beh_path)
            except Exception:
                pass

            beh_meta = beh_data.get("bundle") or {}
            beh_name = beh_meta.get("name") or beh_path.stem

            beh_tools_list = beh_data.get("tools") or []
            n_beh_tools = len(beh_tools_list)

            beh_hooks_list = beh_data.get("hooks") or []
            n_beh_total_tools = n_beh_tools + len(beh_hooks_list)

            beh_ctx_list = (beh_data.get("context") or {}).get("include") or []
            n_beh_ctx = len(beh_ctx_list)

            beh_agents_list = (beh_data.get("agents") or {}).get("include") or []

            # Aggregate: own + context (recursive) + agent descriptions
            beh_content = beh_path.read_text(encoding="utf-8")
            beh_own_tok = estimate_tokens(beh_content)

            beh_ctx_tok = 0
            seen_beh: set[Path] = set()
            for ctx_ref in beh_ctx_list:
                beh_ctx_tok += _estimate_context_tokens_recursive(
                    ctx_ref, repo_root, seen_beh
                )
            for mention in extract_mentions(beh_body):
                beh_ctx_tok += _estimate_context_tokens_recursive(
                    mention, repo_root, seen_beh
                )

            beh_agent_tok = sum(
                _estimate_agent_tokens(a, repo_root) for a in beh_agents_list
            )
            beh_agg_tok = beh_own_tok + beh_ctx_tok + beh_agent_tok

            # Build label
            detail_parts = []
            if n_beh_total_tools:
                detail_parts.append(f"{n_beh_total_tools} tools")
            if n_beh_ctx:
                detail_parts.append(f"{n_beh_ctx} ctx")
            detail = " · ".join(detail_parts)
            if detail:
                beh_label = f"{beh_name}\\n{detail}\\n~{beh_agg_tok} tok"
            else:
                beh_label = f"{beh_name}\\n~{beh_agg_tok} tok"

            beh_id = _sanitize_id(f"beh_{beh_name}")
            lines.append(
                f"        {beh_id} [label={_q(beh_label)}, shape=box,"
                f' fillcolor="{_COLOR_BEHAVIOR_LOCAL}", style="filled,rounded"]'
            )

        lines.append("    }")
        lines.append("")

    # ── Cluster: standalones ───────────────────────────────────────────────────

    if standalones:
        lines.append("    subgraph cluster_standalones {")
        lines.append('        label="Standalone Bundles"')
        lines.append('        style="filled"')
        lines.append(f'        fillcolor="{_COLOR_CLUSTER_FILL}"')
        lines.append(f'        color="{_COLOR_CLUSTER_BORDER}"')
        lines.append("")

        for sta_path in standalones:
            sta_data: dict = {}
            try:
                sta_data, _ = parse_frontmatter(sta_path)
            except Exception:
                pass
            sta_name = (sta_data.get("bundle") or {}).get("name") or sta_path.stem
            sta_id = _sanitize_id(f"sta_{sta_name}")
            lines.append(
                f"        {sta_id} [label={_q(sta_name)}, shape=box,"
                f' fillcolor="{_COLOR_STANDALONE}", style="filled,rounded"]'
            )

        lines.append("    }")
        lines.append("")

    # ── Cluster: agents ────────────────────────────────────────────────────────

    if agents:
        lines.append("    subgraph cluster_agents {")
        lines.append('        label="Agents"')
        lines.append('        style="filled"')
        lines.append(f'        fillcolor="{_COLOR_CLUSTER_FILL}"')
        lines.append(f'        color="{_COLOR_CLUSTER_BORDER}"')
        lines.append("")

        for agt_path in agents:
            agt_data: dict = {}
            try:
                agt_data, _ = parse_frontmatter(agt_path)
            except Exception:
                pass
            meta = agt_data.get("meta") or {}
            agt_name = meta.get("name") or agt_path.stem
            description = meta.get("description") or ""
            agt_tok = estimate_tokens(description)
            agt_id = _sanitize_id(f"agt_{agt_name}")
            agt_label = f"{agt_name}\\n~{agt_tok} tok desc"
            lines.append(
                f"        {agt_id} [label={_q(agt_label)}, shape=box,"
                f' fillcolor="{_COLOR_AGENT_BASE}", style="filled,rounded"]'
            )

        lines.append("    }")
        lines.append("")

    # ── Cluster: modules ───────────────────────────────────────────────────────

    if modules:
        lines.append("    subgraph cluster_modules {")
        lines.append('        label="Modules"')
        lines.append('        style="filled"')
        lines.append(f'        fillcolor="{_COLOR_CLUSTER_FILL}"')
        lines.append(f'        color="{_COLOR_CLUSTER_BORDER}"')
        lines.append("")

        for mod_path in modules:
            mod_name = mod_path.name
            result = estimate_module_tool_tokens(mod_path)
            mod_id = _sanitize_id(f"mod_{mod_name}")
            if result:
                n_tools = result["tool_count"]
                tok = result["total_tokens"]
                mod_label = f"{mod_name}\\n{n_tools} tool · ~{tok} tok"
            else:
                mod_label = mod_name
            lines.append(
                f"        {mod_id} [label={_q(mod_label)}, shape=box,"
                f' fillcolor="{_COLOR_TOOL}", style="filled,rounded"]'
            )

        lines.append("    }")
        lines.append("")

    # ── Cluster: providers ─────────────────────────────────────────────────────

    if providers:
        lines.append("    subgraph cluster_providers {")
        lines.append('        label="Providers"')
        lines.append('        style="filled"')
        lines.append(f'        fillcolor="{_COLOR_CLUSTER_FILL}"')
        lines.append(f'        color="{_COLOR_CLUSTER_BORDER}"')
        lines.append("")

        for prv_path in providers:
            prv_name = prv_path.stem
            prv_id = _sanitize_id(f"prv_{prv_name}")
            lines.append(
                f"        {prv_id} [label={_q(prv_name)}, shape=box,"
                f' fillcolor="{_COLOR_PROVIDER}", style="filled,rounded"]'
            )

        lines.append("    }")
        lines.append("")

    # ── Cluster: experiments ───────────────────────────────────────────────────

    if experiments:
        lines.append("    subgraph cluster_experiments {")
        lines.append('        label="Experiments"')
        lines.append('        style="filled"')
        lines.append(f'        fillcolor="{_COLOR_CLUSTER_FILL}"')
        lines.append(f'        color="{_COLOR_CLUSTER_BORDER}"')
        lines.append("")

        seen_exp_ids: set[str] = set()
        for exp_path in experiments:
            exp_data: dict = {}
            try:
                exp_data, _ = parse_frontmatter(exp_path)
            except Exception:
                pass
            if exp_path.name == "bundle.md":
                exp_name = exp_path.parent.name
            else:
                exp_name = (exp_data.get("bundle") or {}).get("name") or exp_path.stem
            exp_id = _sanitize_id(f"exp_{exp_name}")
            if exp_id not in seen_exp_ids:
                seen_exp_ids.add(exp_id)
                lines.append(
                    f"        {exp_id} [label={_q(exp_name)}, shape=box,"
                    f' fillcolor="{_COLOR_EXPERIMENT}", style="filled,rounded"]'
                )

        lines.append("    }")
        lines.append("")

    # ── Cluster: context ───────────────────────────────────────────────────────

    if context_files:
        lines.append("    subgraph cluster_context {")
        lines.append('        label="Context Files"')
        lines.append('        style="filled"')
        lines.append(f'        fillcolor="{_COLOR_CLUSTER_FILL}"')
        lines.append(f'        color="{_COLOR_CLUSTER_BORDER}"')
        lines.append("")

        seen_ctx_ids: set[str] = set()
        for ctx_path in context_files:
            ctx_display = ctx_path.name
            ctx_id = _sanitize_id(f"ctx_{ctx_display}")
            if ctx_id in seen_ctx_ids:
                continue
            seen_ctx_ids.add(ctx_id)
            try:
                ctx_content = ctx_path.read_text(encoding="utf-8")
                ctx_tok = estimate_tokens(ctx_content)
            except OSError:
                ctx_tok = 0
            ctx_label = f"{ctx_display}\\n~{ctx_tok} tok"
            lines.append(
                f"        {ctx_id} [label={_q(ctx_label)}, shape=box,"
                f' fillcolor="{_COLOR_CONTEXT}", style="filled,rounded"]'
            )

        lines.append("    }")
        lines.append("")

    # ── Legend cluster ─────────────────────────────────────────────────────────

    lines.extend(_build_legend_lines())

    # ── Disclaimer note node ───────────────────────────────────────────────────

    disclaimer_text = (
        "Token estimates: ~4 chars/token\\n"
        "Solid border = local (counted)\\n"
        "Dashed + red = external, hidden cost (not counted)\\n"
        "Dashed + muted = external, no cost\\n"
        "Excludes: sub-session costs, runtime-dynamic"
    )
    lines.append(
        f"    disclaimer [label={_q(disclaimer_text)}, shape=note,"
        f' fillcolor="{_COLOR_SUMMARY}", style="filled", fontsize=9]'
    )
    lines.append("")

    # ── Edges ──────────────────────────────────────────────────────────────────

    # Build agent stem → id lookup
    agent_stem_to_id: dict[str, str] = {}
    for agt_path in agents:
        agt_data2: dict = {}
        try:
            agt_data2, _ = parse_frontmatter(agt_path)
        except Exception:
            pass
        meta2 = agt_data2.get("meta") or {}
        agt_name2 = meta2.get("name") or agt_path.stem
        agt_id2 = _sanitize_id(f"agt_{agt_name2}")
        agent_stem_to_id[agt_path.stem] = agt_id2
        # Also map by name from meta (in case they differ)
        if agt_name2 != agt_path.stem:
            agent_stem_to_id[agt_name2] = agt_id2

    # Build module name → path lookup
    module_name_to_path: dict[str, Path] = {m.name: m for m in modules}

    # Root → includes
    if root_bundle.exists():
        try:
            inc_data, _ = parse_frontmatter(root_bundle)
        except Exception:
            inc_data = {}
        seen_ext_ids: set[str] = set()
        for inc in inc_data.get("includes") or []:
            ref = str(inc.get("bundle") or "")
            if not ref:
                continue
            local_path = _resolve_local_include(ref, repo_root)
            if local_path is not None:
                # Find matching behavior
                for beh_path in behaviors:
                    if beh_path.resolve() == local_path:
                        beh_d: dict = {}
                        try:
                            beh_d, _ = parse_frontmatter(beh_path)
                        except Exception:
                            pass
                        beh_n = (beh_d.get("bundle") or {}).get("name") or beh_path.stem
                        beh_i = _sanitize_id(f"beh_{beh_n}")
                        edge_lines.append(
                            f'    {root_id} -> {beh_i} [label="composes"]'
                        )
                        break
            else:
                # External include — classify by cost impact
                eid = f"ext_{_sanitize_id(ref)}"
                if eid not in seen_ext_ids:
                    seen_ext_ids.add(eid)
                    disp = _extract_external_name(ref)
                    is_high_cost = _is_external_high_cost(ref)
                    if is_high_cost:
                        ext_label = _q(f"{disp}\\n(external, cost)")
                        ext_node = (
                            f"    {eid} [label={ext_label}, shape=box,"
                            f' fillcolor="{_COLOR_BUNDLE_ROOT}",'
                            f' style="dashed", color="{_COLOR_EXTERNAL_COST}", penwidth=2]'
                        )
                    else:
                        ext_label = _q(f"{disp}\\n(external)")
                        ext_node = (
                            f"    {eid} [label={ext_label}, shape=box,"
                            f' fillcolor="{_COLOR_EXTERNAL_MUTED}",'
                            f' style="dashed"]'
                        )
                    lines.append(ext_node)
                edge_lines.append(f"    {root_id} -> {eid} [style=dashed]")

    # Per-behavior edges
    for beh_path in behaviors:
        beh_d2: dict = {}
        beh_b2 = ""
        try:
            beh_d2, beh_b2 = parse_frontmatter(beh_path)
        except Exception:
            pass
        beh_meta2 = beh_d2.get("bundle") or {}
        beh_name2 = beh_meta2.get("name") or beh_path.stem
        beh_id2 = _sanitize_id(f"beh_{beh_name2}")

        # Behavior → agent (owns)
        for agt_ref in (beh_d2.get("agents") or {}).get("include") or []:
            agt_stem2 = agt_ref.split(":")[-1] if ":" in agt_ref else agt_ref
            if agt_stem2 in agent_stem_to_id:
                edge_lines.append(
                    f'    {beh_id2} -> {agent_stem_to_id[agt_stem2]} [label="owns"]'
                )

        # Behavior → module (uses) — from tools: and hooks:
        for tool_entry in (beh_d2.get("tools") or []) + (beh_d2.get("hooks") or []):
            mod_name2 = tool_entry.get("module") or ""
            if mod_name2 and mod_name2 in module_name_to_path:
                mod_id2 = _sanitize_id(f"mod_{mod_name2}")
                edge_lines.append(
                    f'    {beh_id2} -> {mod_id2} [label="uses", penwidth=0.8]'
                )

        # Behavior → context (dotted purple)
        beh_ctx_list2 = (beh_d2.get("context") or {}).get("include") or []
        for ctx_ref in beh_ctx_list2:
            local_ctx = _resolve_local_include(ctx_ref, repo_root)
            if local_ctx is not None and local_ctx in context_files:
                ctx_id2 = _sanitize_id(f"ctx_{local_ctx.name}")
                edge_lines.append(
                    f"    {beh_id2} -> {ctx_id2} [style=dotted, color=purple]"
                )

    # Root → context (from @mentions in bundle body)
    for mention in extract_mentions(root_body):
        local_ctx3 = resolve_local_mention(mention, repo_root)
        if local_ctx3 is not None and local_ctx3 in context_files:
            ctx_id3 = _sanitize_id(f"ctx_{local_ctx3.name}")
            edge_lines.append(
                f"    {root_id} -> {ctx_id3} [style=dotted, color=purple]"
            )

    # Standalone → root (extends, dashed)
    for sta_path in standalones:
        sta_d2: dict = {}
        try:
            sta_d2, _ = parse_frontmatter(sta_path)
        except Exception:
            pass
        sta_name2 = (sta_d2.get("bundle") or {}).get("name") or sta_path.stem
        sta_id2 = _sanitize_id(f"sta_{sta_name2}")
        edge_lines.append(f'    {sta_id2} -> {root_id} [label="extends", style=dashed]')

    # Experiment → root (forks, dashed)
    seen_exp_edge_ids: set[str] = set()
    for exp_path in experiments:
        exp_d2: dict = {}
        try:
            exp_d2, _ = parse_frontmatter(exp_path)
        except Exception:
            pass
        if exp_path.name == "bundle.md":
            exp_name2 = exp_path.parent.name
        else:
            exp_name2 = (exp_d2.get("bundle") or {}).get("name") or exp_path.stem
        exp_id2 = _sanitize_id(f"exp_{exp_name2}")
        if exp_id2 not in seen_exp_edge_ids:
            seen_exp_edge_ids.add(exp_id2)
            edge_lines.append(
                f'    {exp_id2} -> {root_id} [label="forks", style=dashed]'
            )

    # ── Source hash ────────────────────────────────────────────────────────────

    body_str = "\n".join(lines + [""] + edge_lines)
    structural_hash = hashlib.sha256(body_str.encode()).hexdigest()

    # ── Graph title ────────────────────────────────────────────────────────────

    title = f"{root_name}{version_str} \u2014 bundle repo"
    graph_id = _sanitize_id(root_name)

    # ── Assemble ───────────────────────────────────────────────────────────────

    out: list[str] = [
        f"digraph {graph_id} {{",
        "    rankdir=LR",
        '    fontname="Helvetica"',
        "    fontsize=12",
        f"    label={_q(title)}",
        "    labelloc=t",
        "    labeljust=c",
        "    nodesep=0.6",
        "    ranksep=0.7",
        '    bgcolor="white"',
        f'    source_hash="{structural_hash}"',
        "",
        '    node [fontname="Helvetica", fontsize=11, style="filled,rounded"]',
        '    edge [fontname="Helvetica", fontsize=9]',
        "",
        body_str,
        "}",
    ]

    return "\n".join(out)


# ── Internal helpers ───────────────────────────────────────────────────────────


def _estimate_context_tokens_recursive(
    ref: str, repo_root: Path, seen: set[Path]
) -> int:
    """Estimate total tokens for a context ref, recursively following @mentions.

    Resolves *ref* to a local file, reads its content, estimates tokens,
    then recursively follows any @mentions found inside.  Uses *seen* for
    deduplication to prevent infinite loops.

    Args:
        ref: Context reference — either ``@namespace:path`` or
            ``namespace:path`` form, or a ``git+`` URL.
        repo_root: Repository root for path resolution.
        seen: Set of already-visited resolved paths (mutated in place).

    Returns:
        Integer token estimate for this file and all recursively referenced
        files not already in *seen*.  Returns 0 if the file cannot be
        resolved or read.
    """
    local_path = _resolve_local_include(ref, repo_root)
    if local_path is None:
        return 0
    if local_path in seen:
        return 0
    seen.add(local_path)

    try:
        content = local_path.read_text(encoding="utf-8")
    except OSError:
        return 0

    total = estimate_tokens(content)

    # Recursively follow @mentions in this file
    for mention in extract_mentions(content):
        total += _estimate_context_tokens_recursive(mention, repo_root, seen)

    return total


def _collect_context_files_all(
    root_bundle: Path,
    behaviors: list[Path],
    repo_root: Path,
    context_files: dict[Path, str],
) -> None:
    """Collect all unique context files referenced in behaviors and root bundle.

    Populates *context_files* with ``{resolved_path: ref_string}`` entries
    from ``context.include`` in each behavior and from @mentions in the root
    bundle body.

    Args:
        root_bundle: Root bundle file (may not exist).
        behaviors: List of behavior YAML file paths.
        repo_root: Repository root for path resolution.
        context_files: Dict to populate (mutated in place).
    """
    # From behaviors' context.include
    for beh_path in behaviors:
        try:
            beh_data, _ = parse_frontmatter(beh_path)
        except Exception:
            continue
        for ctx_ref in (beh_data.get("context") or {}).get("include") or []:
            local_path = _resolve_local_include(ctx_ref, repo_root)
            if local_path is not None and local_path not in context_files:
                context_files[local_path] = ctx_ref

    # From root bundle @mentions in body
    if root_bundle.exists():
        try:
            _, body = parse_frontmatter(root_bundle)
        except Exception:
            return
        for mention in extract_mentions(body):
            local_path = resolve_local_mention(mention, repo_root)
            if local_path is not None and local_path not in context_files:
                context_files[local_path] = mention


def _build_legend_lines() -> list[str]:
    """Build the legend cluster lines for the DOT graph."""
    return [
        "    subgraph cluster_legend {",
        '        label="Legend"',
        '        style="filled"',
        f'        fillcolor="{_COLOR_LEGEND_FILL}"',
        f'        color="{_COLOR_LEGEND_BORDER}"',
        "        fontsize=9",
        "",
        f'        leg_root [label="root bundle", shape=box, fillcolor="{_COLOR_BUNDLE_ROOT}", style="filled,rounded,bold", fontsize=9]',
        f'        leg_behavior [label="behavior", shape=box, fillcolor="{_COLOR_BEHAVIOR_LOCAL}", style="filled,rounded", fontsize=9]',
        f'        leg_agent [label="agent", shape=box, fillcolor="{_COLOR_AGENT_BASE}", style="filled,rounded", fontsize=9]',
        f'        leg_module [label="module", shape=box, fillcolor="{_COLOR_TOOL}", style="filled,rounded", fontsize=9]',
        f'        leg_provider [label="provider", shape=box, fillcolor="{_COLOR_PROVIDER}", style="filled,rounded", fontsize=9]',
        f'        leg_context [label="context", shape=box, fillcolor="{_COLOR_CONTEXT}", style="filled,rounded", fontsize=9]',
        f'        leg_standalone [label="standalone", shape=box, fillcolor="{_COLOR_STANDALONE}", style="filled,rounded", fontsize=9]',
        f'        leg_experiment [label="experiment", shape=box, fillcolor="{_COLOR_EXPERIMENT}", style="filled,rounded", fontsize=9]',
        f'        leg_ext_cost [label="ext+cost", shape=box, fillcolor="{_COLOR_BUNDLE_ROOT}", style="dashed", color="{_COLOR_EXTERNAL_COST}", penwidth=2, fontsize=9]',
        f'        leg_ext_muted [label="ext+no-cost", shape=box, fillcolor="{_COLOR_EXTERNAL_MUTED}", style="dashed", fontsize=9]',
        "    }",
        "",
    ]


def _is_external_high_cost(ref: str) -> bool:
    """Return True if an external include reference is likely to add hidden cost.

    External references that point to behavior YAML files or full bundles
    (which typically bring agents and context) are considered high-cost.
    References to pure module or provider packages are low-cost.

    Args:
        ref: External include reference string (git+ URL).

    Returns:
        True when the reference is likely a behavior/bundle with hidden cost.
    """
    if "behaviors/" in ref:
        return True
    # Full-bundle refs (no subdirectory) are ambiguous but likely bring behaviors
    if "subdirectory=" not in ref:
        # Heuristic: if the URL has "bundle" in the repo name, likely high cost
        if "-bundle-" in ref or "/amplifier-" in ref.lower():
            return True
    # Module-only references (subdirectory points to modules/)
    if "modules/" in ref or "-module-" in ref:
        return False
    return True


def _sanitize_id(raw: str) -> str:
    """Make an arbitrary string safe for use as a DOT node identifier.

    * Replaces hyphens, spaces, dots, slashes, and colons with underscores.
    * Strips any remaining non-alphanumeric/underscore characters.
    * Prefixes with ``n_`` if the result starts with a digit or is empty.

    Examples::

        _sanitize_id("tool-delegate")          # "tool_delegate"
        _sanitize_id("foundation:my-agent")    # "foundation_my_agent"
        _sanitize_id("@foundation:ctx/f.md")   # "foundation_ctx_f_md"
    """
    s = re.sub(r"[-\s./:\\@]", "_", raw)
    s = re.sub(r"[^\w]", "", s)
    s = s.strip("_")
    if not s:
        s = "node"
    if s[0].isdigit():
        s = "n_" + s
    return s


def _q(s: str) -> str:
    """Wrap *s* in DOT double-quotes, escaping internal double-quotes.

    Note: ``\\n`` inside *s* is preserved as a literal DOT newline escape
    (two characters: backslash + n), which Graphviz renders as a line break.

    Args:
        s: Any string value (may contain ``\\n`` DOT newline sequences).

    Returns:
        A DOT-safe double-quoted string.
    """
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    # Restore \\n sequences that were doubled by the backslash escape above
    escaped = escaped.replace("\\\\n", "\\n")
    return f'"{escaped}"'


def _short_path(ref: str) -> str:
    """Return a short display label (filename) for a mention or context ref.

    Examples::

        _short_path("@foundation:context/agents/delegation-instructions.md")
        # "delegation-instructions.md"
        _short_path("foundation:context/file.md")
        # "file.md"
    """
    bare = ref.lstrip("@")
    if ":" in bare:
        path_part = bare.split(":", 1)[1]
    else:
        path_part = bare
    return Path(path_part).name or ref


def _get_repo_git_url(repo_root: Path) -> str | None:
    """Read the git remote origin URL for same-repo detection.

    Handles both normal ``.git`` directories and submodule ``.git`` files that
    point to a ``gitdir:`` location.

    Args:
        repo_root: Repository root to inspect.

    Returns:
        The remote origin URL string, or ``None`` if not found.
    """
    import configparser

    git_path = repo_root / ".git"
    if git_path.is_file():
        # Submodule: .git is a file containing "gitdir: <relative-path>"
        content = git_path.read_text(encoding="utf-8").strip()
        if content.startswith("gitdir:"):
            gitdir_rel = content[len("gitdir:") :].strip()
            gitdir = Path(gitdir_rel)
            if not gitdir.is_absolute():
                gitdir = (repo_root / gitdir).resolve()
        else:
            return None
    elif git_path.is_dir():
        gitdir = git_path
    else:
        return None

    git_config = gitdir / "config"
    if not git_config.exists():
        return None

    config = configparser.ConfigParser()
    config.read(git_config)
    for section in config.sections():
        if section.startswith('remote "') and "url" in config[section]:
            return config[section]["url"]
    return None


def _normalize_git_url(url: str) -> str:
    """Normalize a git URL for comparison.

    Strips the ``git+`` prefix, ``@ref`` suffix, ``#fragment``, and trailing
    ``.git`` so that an include URL and a clone URL for the same repo compare
    equal.

    Examples::

        _normalize_git_url(
            "git+https://github.com/microsoft/amplifier-foundation@main"
            "#subdirectory=behaviors/foo.yaml"
        )
        # "https://github.com/microsoft/amplifier-foundation"

        _normalize_git_url("https://github.com/microsoft/amplifier-foundation.git")
        # "https://github.com/microsoft/amplifier-foundation"

    Args:
        url: Raw git URL string.

    Returns:
        Normalized URL string suitable for equality comparison.
    """
    url = url.removeprefix("git+")
    url = url.split("#")[0]
    if "://" in url:
        # Strip @ref suffix that appears after the path (e.g. @main, @v1.0)
        url = re.sub(r"@[^/]+$", "", url)
    url = url.removesuffix(".git")
    return url.rstrip("/")


def _is_same_repo_include(ref: str, repo_root: Path) -> Path | None:
    """Detect and resolve a same-repo ``git+`` include URL to a local path.

    Returns the local :class:`~pathlib.Path` when *ref* is a ``git+`` URL
    whose base matches this repository's remote origin **and** the
    ``#subdirectory=`` fragment points to an existing local file.  Returns
    ``None`` in all other cases (different repo, no subdirectory, file absent).

    Args:
        ref: Bundle include reference string (may be a ``git+`` URL or any
            other form).
        repo_root: Repository root used to read ``.git/config`` and resolve
            local paths.

    Returns:
        Resolved absolute :class:`~pathlib.Path`, or ``None``.
    """
    if not ref.startswith("git+"):
        return None
    repo_url = _get_repo_git_url(repo_root)
    if not repo_url:
        return None
    if _normalize_git_url(ref) != _normalize_git_url(repo_url):
        return None
    if "#subdirectory=" not in ref:
        return None
    subdir = ref.split("#subdirectory=", 1)[1].split("&")[0]
    candidate = repo_root / subdir
    if candidate.exists():
        return candidate.resolve()
    # Try with common extensions in case the ref omits the suffix
    for ext in (".yaml", ".yml", ".md"):
        with_ext = repo_root / f"{subdir}{ext}"
        if with_ext.exists():
            return with_ext.resolve()
    return None


def _resolve_local_include(ref: str, repo_root: Path) -> Path | None:
    """Resolve a ``namespace:path`` include ref (without ``@``) to a local file.

    Handles both bare ``namespace:path`` forms (from ``includes:`` lists) and
    ``@namespace:path`` forms.  For ``git+`` URLs, attempts same-repo
    detection via :func:`_is_same_repo_include` before returning ``None``.

    Args:
        ref: Include reference string such as ``"foundation:behaviors/logging"``
            or ``"git+https://github.com/..."``.
        repo_root: Repository root for path resolution.

    Returns:
        Resolved absolute :class:`~pathlib.Path`, or ``None``.
    """
    if not ref:
        return None
    if ref.startswith("git+") or ref.startswith("http"):
        return _is_same_repo_include(ref, repo_root)
    mention = ref if ref.startswith("@") else f"@{ref}"
    return resolve_local_mention(mention, repo_root)


def _extract_external_name(ref: str) -> str:
    """Extract a human-readable name from an external git URL.

    Prefers the ``#subdirectory=`` tail (filename), then falls back to the
    repository name from the URL path.

    Examples::

        _extract_external_name(
            "git+https://github.com/microsoft/amplifier-bundle-recipes@main"
            "#subdirectory=behaviors/recipes.yaml"
        )
        # "recipes.yaml"

        _extract_external_name(
            "git+https://github.com/microsoft/amplifier-bundle-python-dev@main"
        )
        # "amplifier-bundle-python-dev"
    """
    if "#subdirectory=" in ref:
        subdir = ref.split("#subdirectory=", 1)[1]
        return Path(subdir).name or subdir
    # Strip git+ prefix, then extract repo name before @branch
    clean = ref.replace("git+", "")
    clean = re.sub(r"@[^/]*$", "", clean)
    return Path(clean).name or ref


def _estimate_agent_tokens(agent_ref: str, repo_root: Path) -> int:
    """Estimate description token count for an agent reference.

    Looks up ``agents/<agent-name>.md`` relative to *repo_root*, parses its
    YAML frontmatter, and estimates tokens from the ``meta.description`` field
    only.  This mirrors what actually gets injected into every LLM request via
    the delegate tool's agent list.  Returns 0 if the file does not exist or
    the description field is absent.

    Args:
        agent_ref: Agent reference such as ``"foundation:session-analyst"``.
        repo_root: Repository root containing an ``agents/`` directory.

    Returns:
        Integer token estimate, or 0 when the agent file or description is not
        found.
    """
    if ":" not in agent_ref:
        return 0
    _ns, agent_name = agent_ref.split(":", 1)
    agent_path = repo_root / "agents" / f"{agent_name}.md"
    if agent_path.exists():
        try:
            data, _body = parse_frontmatter(agent_path)
            description = data.get("meta", {}).get("description", "")
            return estimate_tokens(description)
        except OSError:
            return 0
    return 0
