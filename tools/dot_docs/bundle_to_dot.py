"""Bundle/behavior YAML to DOT composition diagram.

Converts Amplifier behavior ``.yaml`` files (and ``.md`` bundle files with
YAML frontmatter) into Graphviz DOT composition diagrams that show tools,
hooks, agent references, context files, and nested includes — all annotated
with per-request token cost estimates.

Usage::

    from dot_docs.bundle_to_dot import bundle_to_dot
    dot_str = bundle_to_dot("behaviors/agents.yaml", repo_root=Path("."))

No LLM calls — pure deterministic function.  Only requires PyYAML and the
Python standard library.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from dot_docs.frontmatter import (
    extract_mentions,
    parse_frontmatter,
    resolve_local_mention,
)
from dot_docs.token_cost import (
    color_tier,
    estimate_tokens,
)

# ── Visual constants ──────────────────────────────────────────────────────────

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


# ── Public API ────────────────────────────────────────────────────────────────


def bundle_to_dot(yaml_path: str | Path, *, repo_root: Path | None = None) -> str:
    """Convert a bundle/behavior YAML or .md file to a DOT composition diagram.

    Reads the file, extracts tools, hooks, agent references, context includes,
    and nested includes, then emits a Graphviz DOT graph annotated with token
    cost estimates and colour-coded by cost tier.

    Args:
        yaml_path: Path to a ``.yaml`` behavior file or ``.md`` bundle file
            with YAML frontmatter.
        repo_root: Repository root used to resolve ``namespace:path`` mentions
            and local include references.  When omitted the function walks up
            from *yaml_path* looking for a ``.git`` directory or ``bundle.md``;
            if neither is found it falls back to *yaml_path*'s parent directory.

    Returns:
        Complete, valid DOT string suitable for passing to ``dot -Tsvg``.

    Raises:
        FileNotFoundError: If *yaml_path* does not exist.
    """
    yaml_path = Path(yaml_path)
    if not yaml_path.exists():
        raise FileNotFoundError(f"Bundle file not found: {yaml_path}")

    # ── Infer repo_root ───────────────────────────────────────────────────────
    if repo_root is None:
        candidate = yaml_path.parent
        while candidate != candidate.parent:
            if (candidate / ".git").exists() or (candidate / "bundle.md").exists():
                repo_root = candidate
                break
            candidate = candidate.parent
        else:
            repo_root = yaml_path.parent

    # ── Parse file ────────────────────────────────────────────────────────────
    data, body = parse_frontmatter(yaml_path)
    bundle_info: dict = data.get("bundle") or {}
    name: str = bundle_info.get("name") or yaml_path.stem
    graph_id = _sanitize_id(name)

    # Body token cost — for .yaml files use raw content; for .md use body text
    raw_content = yaml_path.read_text(encoding="utf-8")
    body_source = body if body else raw_content
    body_tokens = estimate_tokens(body_source)
    body_color = color_tier(body_tokens, "bundle_body")

    # ── Extract elements ──────────────────────────────────────────────────────
    tools: list[dict] = data.get("tools") or []
    hooks: list[dict] = data.get("hooks") or []
    agents_info: dict = data.get("agents") or {}
    agent_includes: list[str] = agents_info.get("include") or []
    ctx_info: dict = data.get("context") or {}
    ctx_includes: list[str] = ctx_info.get("include") or []
    includes: list[dict] = data.get("includes") or []
    session_cfg: dict = data.get("session") or {}

    # @mentions from markdown body
    body_mentions: list[str] = extract_mentions(body) if body else []

    types_used: set[str] = set()
    node_lines: list[str] = []
    edge_lines: list[str] = []

    # ── Root node ─────────────────────────────────────────────────────────────
    has_includes = bool(includes)
    root_style = '"filled,rounded,bold"' if has_includes else '"filled,rounded"'
    root_label = _q(f"{name}\\n~{body_tokens} tok")
    node_lines.append(
        f"    root [label={root_label}, shape=box,"
        f' fillcolor="{body_color}", style={root_style},'
        f' color="{_COLOR_BUNDLE_ROOT}"]'
    )

    total_tokens = body_tokens
    token_breakdown: dict[str, int] = {"body": body_tokens}

    # ── Tool nodes ─────────────────────────────────────────────────────────────
    if tools:
        types_used.add("tool")
    for i, tool in enumerate(tools):
        module = tool.get("module") or f"tool_{i}"
        tid = f"tool_{_sanitize_id(module)}"
        label = _q(module)
        node_lines.append(
            f"    {tid} [label={label}, shape=box,"
            f' fillcolor="{_COLOR_TOOL}", style="filled,rounded"]'
        )
        edge_lines.append(f"    root -> {tid}")

    # ── Hook nodes ─────────────────────────────────────────────────────────────
    if hooks:
        types_used.add("hook")
    for i, hook in enumerate(hooks):
        module = hook.get("module") or f"hook_{i}"
        hid = f"hook_{_sanitize_id(module)}"
        source = str(hook.get("source") or "")
        config_str = str(hook.get("config") or "")
        hook_tok = estimate_tokens(module + source + config_str)
        label = _q(f"{module}\\n~{hook_tok} tok")
        node_lines.append(
            f"    {hid} [label={label}, shape=box,"
            f' fillcolor="{_COLOR_HOOK}", style="filled,rounded"]'
        )
        edge_lines.append(f"    root -> {hid}")
        total_tokens += hook_tok
        token_breakdown["hooks"] = token_breakdown.get("hooks", 0) + hook_tok

    # ── Agent reference nodes ─────────────────────────────────────────────────
    if agent_includes:
        types_used.add("agent")
    for ref in agent_includes:
        aid = f"agent_{_sanitize_id(ref)}"
        agent_tok = _estimate_agent_tokens(ref, repo_root)
        agent_color = color_tier(agent_tok, "agent_description")
        label = _q(f"{ref}\\n~{agent_tok} tok")
        node_lines.append(
            f"    {aid} [label={label}, shape=box,"
            f' fillcolor="{agent_color}", style="filled,rounded"]'
        )
        edge_lines.append(f"    root -> {aid}")
        total_tokens += agent_tok
        token_breakdown["agents"] = token_breakdown.get("agents", 0) + agent_tok

    # ── Context file nodes ─────────────────────────────────────────────────────
    ctx_seen: set[str] = set()
    if ctx_includes:
        types_used.add("context")
    for ref in ctx_includes:
        ctx_id = f"ctx_{_sanitize_id(ref)}"
        if ctx_id in ctx_seen:
            continue
        ctx_seen.add(ctx_id)
        ctx_tok = _estimate_context_tokens(ref, repo_root)
        ctx_color = color_tier(ctx_tok, "context_file")
        short_label = _short_path(ref)
        label = _q(f"{short_label}\\n~{ctx_tok} tok")
        node_lines.append(
            f"    {ctx_id} [label={label}, shape=note,"
            f' fillcolor="{ctx_color}", style="filled"]'
        )
        edge_lines.append(f"    root -> {ctx_id}")
        total_tokens += ctx_tok
        token_breakdown["context"] = token_breakdown.get("context", 0) + ctx_tok

    # ── @mention nodes from markdown body ────────────────────────────────────
    if body_mentions:
        types_used.add("context")
    for mention in body_mentions:
        ctx_id = f"ctx_{_sanitize_id(mention)}"
        if ctx_id in ctx_seen:
            continue
        ctx_seen.add(ctx_id)
        ctx_tok = _estimate_context_tokens(mention, repo_root)
        ctx_color = color_tier(ctx_tok, "context_file")
        short_label = _short_path(mention)
        label = _q(f"{short_label}\\n~{ctx_tok} tok")
        node_lines.append(
            f"    {ctx_id} [label={label}, shape=note,"
            f' fillcolor="{ctx_color}", style="filled"]'
        )
        edge_lines.append(f"    root -> {ctx_id} [style=dashed]")
        total_tokens += ctx_tok
        token_breakdown["context"] = token_breakdown.get("context", 0) + ctx_tok

    # ── Session config node ───────────────────────────────────────────────────
    if session_cfg:
        types_used.add("session")
        sess_tok = estimate_tokens(str(session_cfg))
        label = _q(f"session\\n~{sess_tok} tok")
        node_lines.append(
            f"    session_cfg [label={label}, shape=box,"
            f' fillcolor="{_COLOR_SESSION}", style="filled,rounded,dotted"]'
        )
        edge_lines.append("    root -> session_cfg")
        total_tokens += sess_tok
        token_breakdown["session"] = sess_tok

    # ── Local/external includes ───────────────────────────────────────────────
    local_cluster_lines: list[str] = []
    for idx, inc in enumerate(includes):
        ref = str(inc.get("bundle") or "")
        if not ref:
            continue
        local_path = _resolve_local_include(ref, repo_root)
        if local_path is not None:
            types_used.add("local_include")
            cluster_lines, cluster_tokens, _ = _render_behavior_cluster(
                local_path, repo_root, idx
            )
            if cluster_lines:
                local_cluster_lines.extend(cluster_lines)
                # Edge to the cluster root node
                cluster_root_id = f"cluster_inc_{idx}_root"
                edge_lines.append(
                    f"    root -> {cluster_root_id} [lhead=cluster_inc_{idx}]"
                )
                total_tokens += cluster_tokens
                token_breakdown["includes"] = (
                    token_breakdown.get("includes", 0) + cluster_tokens
                )
        else:
            types_used.add("external_include")
            disp = _extract_external_name(ref)
            eid = f"ext_{_sanitize_id(ref)}_{idx}"
            label = _q(f"{disp}\\n(external)")
            node_lines.append(
                f"    {eid} [label={label}, shape=box,"
                f' fillcolor="{_COLOR_EXTERNAL_INCLUDE}",'
                ' style="filled,rounded,dashed"]'
            )
            edge_lines.append(f"    root -> {eid}")

    # ── Summary node ──────────────────────────────────────────────────────────
    breakdown_parts = [
        f"{k}: {v}" for k, v in sorted(token_breakdown.items()) if k != "body" and v > 0
    ]
    summary_color = color_tier(total_tokens, "all_context")
    summary_label = f"Total: ~{total_tokens} tok"
    if breakdown_parts:
        summary_label += "\\n" + ", ".join(breakdown_parts)
    node_lines.append(
        f"    summary [label={_q(summary_label)}, shape=box,"
        f' fillcolor="{summary_color}", style="filled,rounded",'
        " peripheries=2]"
    )
    edge_lines.append("    root -> summary [style=dashed, arrowhead=none]")

    # ── Assemble body ─────────────────────────────────────────────────────────
    body_str = "\n".join(node_lines + [""] + edge_lines)

    # Source hash derived from structural body (before full graph header)
    structural_hash = hashlib.sha256(body_str.encode()).hexdigest()

    # ── Title ─────────────────────────────────────────────────────────────────
    title = name
    description = bundle_info.get("description") or ""
    if description:
        first_sentence = re.split(r"[.\n]", description.strip())[0].strip()
        if len(first_sentence) > 80:
            first_sentence = first_sentence[:77] + "..."
        if first_sentence:
            title = f"{name} \u2014 {first_sentence}"

    # ── Full graph ────────────────────────────────────────────────────────────
    out: list[str] = [
        f"digraph {graph_id} {{",
        "    rankdir=LR",
        '    fontname="Helvetica"',
        "    fontsize=12",
        f"    label={_q(title)}",
        "    labelloc=t",
        "    labeljust=c",
        "    compound=true",
        "    nodesep=0.6",
        "    ranksep=0.7",
        '    bgcolor="white"',
        f'    source_hash="{structural_hash}"',
        "",
        '    node [fontname="Helvetica", fontsize=11, style="filled,rounded"]',
        '    edge [fontname="Helvetica", fontsize=9]',
        "",
        body_str,
    ]

    if local_cluster_lines:
        out.append("")
        out.extend(local_cluster_lines)

    out.extend(
        [
            "",
            _build_legend(types_used),
            "}",
        ]
    )

    return "\n".join(out)


def bundle_overview_dot(
    bundles_dir: str | Path, *, repo_root: Path | None = None
) -> str:
    """Generate an overview DOT diagram across all behaviors in a directory.

    Not yet implemented — see Task 7.

    Raises:
        NotImplementedError: Always.
    """
    raise NotImplementedError("bundle_overview_dot is not yet implemented (Task 7)")


# ── Internal helpers ──────────────────────────────────────────────────────────


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


def _resolve_local_include(ref: str, repo_root: Path) -> Path | None:
    """Resolve a ``namespace:path`` include ref (without ``@``) to a local file.

    Handles both bare ``namespace:path`` forms (from ``includes:`` lists) and
    ``@namespace:path`` forms.  Returns ``None`` for external ``git+`` URLs.

    Args:
        ref: Include reference string such as ``"foundation:behaviors/logging"``
            or ``"git+https://github.com/..."``.
        repo_root: Repository root for path resolution.

    Returns:
        Resolved absolute :class:`~pathlib.Path`, or ``None``.
    """
    if not ref or ref.startswith("git+") or ref.startswith("http"):
        return None
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


def _estimate_context_tokens(ref: str, repo_root: Path) -> int:
    """Estimate token count for a context file reference.

    Attempts to read the file and applies :func:`~dot_docs.token_cost.estimate_tokens`.
    Returns 0 if the file cannot be found or read.

    Args:
        ref: A ``namespace:path`` or ``@namespace:path`` reference string.
        repo_root: Repository root for path resolution.

    Returns:
        Integer token estimate, or 0 when the file is not locally available.
    """
    mention = ref if ref.startswith("@") else f"@{ref}"
    local = resolve_local_mention(mention, repo_root)
    if local and local.exists():
        try:
            return estimate_tokens(local.read_text(encoding="utf-8"))
        except OSError:
            return 0
    return 0


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


def _render_behavior_cluster(
    path: Path,
    repo_root: Path,
    idx: int,
) -> tuple[list[str], int, set[str]]:
    """Render a behavior file as a DOT cluster subgraph.

    Parses the behavior at *path* and emits a ``subgraph cluster_inc_<idx>``
    block containing the behavior root node plus its tools, hooks, and agent
    references (one level deep — nested includes are not recursed).

    Args:
        path: Absolute path to the included behavior file.
        repo_root: Repository root for agent file lookups.
        idx: Unique integer index used to namespace cluster/node IDs.

    Returns:
        A 3-tuple of ``(lines, total_tokens, types_used)``:

        * *lines* — DOT fragment lines for the cluster subgraph.
        * *total_tokens* — Aggregate token estimate for all elements.
        * *types_used* — Set of element type keys seen (currently unused,
          reserved for future legend integration).
    """
    try:
        data, body = parse_frontmatter(path)
    except Exception:
        return [], 0, set()

    bundle_info: dict = data.get("bundle") or {}
    name: str = bundle_info.get("name") or path.stem

    raw_content = path.read_text(encoding="utf-8")
    body_source = body if body else raw_content
    body_tokens = estimate_tokens(body_source)
    total_tokens = body_tokens

    cluster_id = f"cluster_inc_{idx}"
    root_id = f"{cluster_id}_root"

    lines: list[str] = []
    lines.append(f"    subgraph {cluster_id} {{")
    lines.append(f"        label={_q(name)}")
    lines.append('        style="filled,rounded"')
    lines.append(f'        fillcolor="{_COLOR_CLUSTER_FILL}"')
    lines.append(f'        color="{_COLOR_CLUSTER_BORDER}"')
    lines.append("")

    body_color = color_tier(body_tokens, "bundle_body")
    root_label = _q(f"{name}\\n~{body_tokens} tok")
    lines.append(
        f"        {root_id} [label={root_label}, shape=box,"
        f' fillcolor="{body_color}", style="filled,rounded"]'
    )

    # Tools
    tools: list[dict] = data.get("tools") or []
    for i, tool in enumerate(tools):
        module = tool.get("module") or f"tool_{i}"
        tid = f"{cluster_id}_tool_{_sanitize_id(module)}"
        label = _q(module)
        lines.append(
            f"        {tid} [label={label}, shape=box,"
            f' fillcolor="{_COLOR_TOOL}", style="filled,rounded"]'
        )
        lines.append(f"        {root_id} -> {tid}")

    # Hooks
    hooks: list[dict] = data.get("hooks") or []
    for i, hook in enumerate(hooks):
        module = hook.get("module") or f"hook_{i}"
        hid = f"{cluster_id}_hook_{_sanitize_id(module)}"
        source = str(hook.get("source") or "")
        config_str = str(hook.get("config") or "")
        hook_tok = estimate_tokens(module + source + config_str)
        total_tokens += hook_tok
        label = _q(f"{module}\\n~{hook_tok} tok")
        lines.append(
            f"        {hid} [label={label}, shape=box,"
            f' fillcolor="{_COLOR_HOOK}", style="filled,rounded"]'
        )
        lines.append(f"        {root_id} -> {hid}")

    # Agent references
    agents_info: dict = data.get("agents") or {}
    agent_includes: list[str] = agents_info.get("include") or []
    for ref in agent_includes:
        aid = f"{cluster_id}_agent_{_sanitize_id(ref)}"
        agent_tok = _estimate_agent_tokens(ref, repo_root)
        total_tokens += agent_tok
        agent_color = color_tier(agent_tok, "agent_description")
        label = _q(f"{ref}\\n~{agent_tok} tok")
        lines.append(
            f"        {aid} [label={label}, shape=box,"
            f' fillcolor="{agent_color}", style="filled,rounded"]'
        )
        lines.append(f"        {root_id} -> {aid}")

    lines.append("    }")
    return lines, total_tokens, set()


def _build_legend(types_used: set[str]) -> str:
    """Build a DOT legend cluster for the element types actually present.

    Only element types that appear in the diagram are included.  Entries are
    emitted in a stable order, connected by invisible edges to encourage
    horizontal layout.

    Args:
        types_used: Set of element type keys from the rendered body.  Known
            values: ``"tool"``, ``"agent"``, ``"hook"``, ``"context"``,
            ``"external_include"``, ``"local_include"``, ``"session"``.

    Returns:
        A DOT ``subgraph cluster_legend { ... }`` string.
    """
    # (node_id, label, shape, fillcolor, extra_style_fragment)
    all_entries: list[tuple[str, str, str, str, str]] = [
        ("leg_root", "Bundle Root", "box", _COLOR_BUNDLE_ROOT, ""),
        ("leg_tool", "Tool", "box", _COLOR_TOOL, ""),
        ("leg_agent", "Agent", "box", _COLOR_AGENT_BASE, ""),
        ("leg_hook", "Hook", "box", _COLOR_HOOK, ""),
        ("leg_ctx", "Context File", "note", _COLOR_CONTEXT, ""),
        (
            "leg_ext",
            "External Include",
            "box",
            _COLOR_EXTERNAL_INCLUDE,
            ', style="filled,rounded,dashed"',
        ),
        ("leg_local", "Local Include", "box", _COLOR_BEHAVIOR_LOCAL, ""),
        (
            "leg_sess",
            "Session Config",
            "box",
            _COLOR_SESSION,
            ', style="filled,rounded,dotted"',
        ),
    ]

    type_check: dict[str, str] = {
        "leg_root": "root",  # always shown
        "leg_tool": "tool",
        "leg_agent": "agent",
        "leg_hook": "hook",
        "leg_ctx": "context",
        "leg_ext": "external_include",
        "leg_local": "local_include",
        "leg_sess": "session",
    }

    entries = [
        e
        for e in all_entries
        if type_check[e[0]] == "root" or type_check[e[0]] in types_used
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
