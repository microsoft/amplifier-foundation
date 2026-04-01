"""Per-agent composition card DOT generator.

Converts an Amplifier agent ``.md`` file into a star-layout Graphviz DOT
diagram showing the central agent node (with model_role and description token
cost) and spokes to tools, context @mentions, and delegation targets.

Usage::

    from dot_docs.agent_to_dot import agent_to_dot
    dot_str = agent_to_dot("agents/zen-architect.md", repo_root=Path("."))

No LLM calls — pure deterministic function.  Only requires PyYAML and the
Python standard library.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from dot_docs.frontmatter import (
    extract_delegation_targets,
    extract_mentions,
    parse_frontmatter,
)
from dot_docs.token_cost import (
    color_tier,
    estimate_tokens,
)

# ── Visual constants ───────────────────────────────────────────────────────────

_COLOR_AGENT_CENTRAL = "#80cbc4"  # teal — central agent node border
_COLOR_TOOL = "#bbdefb"  # fixed blue — tool nodes
_COLOR_CONTEXT = "#e1bee7"  # purple base — context @mention nodes
_COLOR_DELEGATION = "#c8e6c9"  # green — delegation target nodes
_COLOR_LEGEND_FILL = "white"
_COLOR_LEGEND_BORDER = "#cccccc"


# ── Public API ─────────────────────────────────────────────────────────────────


def agent_to_dot(md_path: str | Path, *, repo_root: Path | None = None) -> str:
    """Convert an agent ``.md`` file to a per-agent composition card DOT diagram.

    Reads the file, extracts ``meta.name``, ``meta.description``,
    ``model_role``, ``tools``, body @mentions, and delegation targets, then
    emits a star-layout Graphviz DOT graph annotated with token cost estimates
    and colour-coded by element type.

    Args:
        md_path: Path to an agent ``.md`` file with YAML frontmatter.
        repo_root: Repository root used to resolve ``@namespace:path`` mentions
            for token estimation.  When omitted the function walks up from
            *md_path* looking for a ``.git`` directory or ``bundle.md``; if
            neither is found it falls back to *md_path*'s parent directory.

    Returns:
        Complete, valid DOT string suitable for passing to ``dot -Tsvg``.

    Raises:
        FileNotFoundError: If *md_path* does not exist.
    """
    md_path = Path(md_path)
    if not md_path.exists():
        raise FileNotFoundError(f"Agent file not found: {md_path}")

    # ── Infer repo_root ────────────────────────────────────────────────────
    if repo_root is None:
        candidate = md_path.parent
        while candidate != candidate.parent:
            if (candidate / ".git").exists() or (candidate / "bundle.md").exists():
                repo_root = candidate
                break
            candidate = candidate.parent
        else:
            repo_root = md_path.parent

    # ── Parse file ─────────────────────────────────────────────────────────
    data, body = parse_frontmatter(md_path)

    meta: dict = data.get("meta") or {}
    agent_name: str = meta.get("name") or md_path.stem
    description: str = meta.get("description") or ""

    # model_role: top-level or inside meta
    model_role_raw = data.get("model_role") or meta.get("model_role") or ""
    model_role_str = _format_model_role(model_role_raw)

    tools: list[dict] = data.get("tools") or []
    mentions: list[str] = extract_mentions(body) if body else []
    delegation_targets: list[str] = extract_delegation_targets(body) if body else []

    desc_tokens = estimate_tokens(description)
    graph_id = _sanitize_id(agent_name)

    types_used: set[str] = set()
    node_lines: list[str] = []
    edge_lines: list[str] = []

    # ── Central agent node ─────────────────────────────────────────────────
    agent_color = color_tier(desc_tokens, "agent_description")
    central_label = _q(
        f"{agent_name}\\nmodel_role: {model_role_str}\\ndescription: ~{desc_tokens} tok"
    )
    node_lines.append(
        f"    agent [label={central_label}, shape=box,"
        f' fillcolor="{agent_color}", style="filled,bold",'
        f' color="{_COLOR_AGENT_CENTRAL}"]'
    )

    # ── Tool nodes ─────────────────────────────────────────────────────────
    if tools:
        types_used.add("tool")
    for tool in tools:
        module = tool.get("module") or ""
        if not module:
            continue
        tid = f"tool_{_sanitize_id(module)}"
        label = _q(module)
        node_lines.append(
            f"    {tid} [label={label}, shape=box,"
            f' fillcolor="{_COLOR_TOOL}", style="filled,rounded"]'
        )
        edge_lines.append(f"    agent -> {tid}")

    # ── Context @mention nodes ─────────────────────────────────────────────
    ctx_seen: set[str] = set()
    if mentions:
        types_used.add("context")
    for mention in mentions:
        ctx_id = f"ctx_{_sanitize_id(mention)}"
        if ctx_id in ctx_seen:
            continue
        ctx_seen.add(ctx_id)
        ctx_tok = _estimate_context_tokens(mention, repo_root)
        ctx_color = color_tier(ctx_tok, "context_file")
        short = _short_path(mention)
        label = _q(f"{short}\\n~{ctx_tok} tok")
        node_lines.append(
            f"    {ctx_id} [label={label}, shape=note,"
            f' fillcolor="{ctx_color}", style="filled"]'
        )
        edge_lines.append(f"    agent -> {ctx_id}")

    # ── Delegation target nodes ────────────────────────────────────────────
    del_seen: set[str] = set()
    if delegation_targets:
        types_used.add("delegation")
    for target in delegation_targets:
        del_id = f"del_{_sanitize_id(target)}"
        if del_id in del_seen:
            continue
        del_seen.add(del_id)
        label = _q(target)
        node_lines.append(
            f"    {del_id} [label={label}, shape=box,"
            f' fillcolor="{_COLOR_DELEGATION}", style="filled,rounded,dashed"]'
        )
        edge_lines.append(f"    agent -> {del_id} [style=dashed]")

    # ── Assemble body ──────────────────────────────────────────────────────
    body_str = "\n".join(node_lines + [""] + edge_lines)

    # Source hash derived from structural body (before full graph header)
    structural_hash = hashlib.sha256(body_str.encode()).hexdigest()

    # ── Title ──────────────────────────────────────────────────────────────
    title = f"{agent_name} — {model_role_str}"

    # ── Full graph ─────────────────────────────────────────────────────────
    out: list[str] = [
        f"digraph {graph_id} {{",
        "    rankdir=TB",
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
        "",
        _build_legend(types_used),
        "}",
    ]

    return "\n".join(out)


def agents_topology_dot(repo_root: str | Path) -> str:  # noqa: ARG001
    """Generate a topology DOT diagram for all agents in a repo.

    .. note::
        This function is a placeholder for Task 9.  Calling it raises
        :exc:`NotImplementedError`.

    Args:
        repo_root: Repository root containing an ``agents/`` directory.

    Raises:
        NotImplementedError: Always — implementation deferred to Task 9.
    """
    raise NotImplementedError("agents_topology_dot() is not yet implemented (Task 9)")


# ── Internal helpers ───────────────────────────────────────────────────────────


def _format_model_role(raw: object) -> str:
    """Format model_role value as a display string.

    Handles both string (``"fast"``) and list (``["reasoning", "general"]``)
    values, returning a comma-separated string.

    Args:
        raw: The raw frontmatter value — may be ``str``, ``list``, or ``None``.

    Returns:
        Human-readable string such as ``"fast"`` or ``"reasoning, general"``.
    """
    if isinstance(raw, list):
        return ", ".join(str(v) for v in raw)
    if raw:
        return str(raw)
    return ""


def _sanitize_id(raw: str) -> str:
    """Make an arbitrary string safe for use as a DOT node identifier.

    * Replaces hyphens, spaces, dots, slashes, colons, and ``@`` with
      underscores.
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


def _estimate_context_tokens(ref: str, repo_root: Path) -> int:
    """Estimate token count for a context file reference.

    Attempts to read the file and applies
    :func:`~dot_docs.token_cost.estimate_tokens`.  Returns 0 if the file
    cannot be found or read.

    Args:
        ref: A ``@namespace:path`` reference string.
        repo_root: Repository root for path resolution.

    Returns:
        Integer token estimate, or 0 when the file is not locally available.
    """
    from dot_docs.frontmatter import resolve_local_mention

    mention = ref if ref.startswith("@") else f"@{ref}"
    local = resolve_local_mention(mention, repo_root)
    if local and local.exists():
        try:
            return estimate_tokens(local.read_text(encoding="utf-8"))
        except OSError:
            return 0
    return 0


def _build_legend(types_used: set[str]) -> str:
    """Build a DOT legend cluster for the element types actually present.

    Only element types that appear in the diagram are included.  The agent
    central node is always shown.  Entries are emitted in a stable order,
    connected by invisible edges to encourage horizontal layout.

    Args:
        types_used: Set of element type keys from the rendered body.  Known
            values: ``"tool"``, ``"context"``, ``"delegation"``.

    Returns:
        A DOT ``subgraph cluster_legend { ... }`` string.
    """
    # (node_id, label, shape, fillcolor, extra_style)
    all_entries: list[tuple[str, str, str, str, str]] = [
        ("leg_agent", "Agent", "box", _COLOR_AGENT_CENTRAL, ', style="filled,bold"'),
        ("leg_tool", "Tool", "box", _COLOR_TOOL, ""),
        ("leg_ctx", "Context File", "note", _COLOR_CONTEXT, ""),
        (
            "leg_del",
            "Delegation Target",
            "box",
            _COLOR_DELEGATION,
            ', style="filled,rounded,dashed"',
        ),
    ]

    type_check: dict[str, str] = {
        "leg_agent": "agent",  # always shown
        "leg_tool": "tool",
        "leg_ctx": "context",
        "leg_del": "delegation",
    }

    entries = [
        e
        for e in all_entries
        if type_check[e[0]] == "agent" or type_check[e[0]] in types_used
    ]

    lines: list[str] = [
        "    subgraph cluster_legend {",
        '        label="Legend"',
        '        style="filled,rounded"',
        f'        fillcolor="{_COLOR_LEGEND_FILL}"',
        f'        color="{_COLOR_LEGEND_BORDER}"',
        "        fontsize=10",
        "        node [shape=box, fontsize=9, width=1.6]",
    ]

    node_ids: list[str] = []
    for nid, label, shape, fillcolor, extra in entries:
        default_style = ', style="filled,rounded"' if not extra else extra
        lines.append(
            f"        {nid} [label={_q(label)}, shape={shape},"
            f' fillcolor="{fillcolor}"{default_style}]'
        )
        node_ids.append(nid)

    if len(node_ids) > 1:
        chain = " -> ".join(node_ids)
        lines.append(f"        {chain} [style=invis]")

    lines.append("    }")
    return "\n".join(lines)
