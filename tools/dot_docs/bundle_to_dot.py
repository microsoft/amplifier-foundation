"""Bundle/behavior YAML to DOT overview diagrams.

Generates repository-wide Graphviz DOT diagrams showing all bundle and behavior
files in a repository, with edges representing include relationships and nodes
annotated with per-request token cost estimates.

Usage::

    from dot_docs.bundle_to_dot import bundle_overview_dot
    dot_str = bundle_overview_dot(repo_root=Path("."))

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


def bundle_overview_dot(repo_root: str | Path) -> str:
    """Generate an overview DOT diagram for all bundle/behavior files in a repo.

    Discovers ``bundle.md`` at the repo root, all ``behaviors/*.yaml`` /
    ``behaviors/*.yml`` files, and ``bundles/*.yaml`` / ``bundles/*.md``
    standalone bundles.  Each file becomes a single node annotated with its
    aggregate token cost.  Edges represent ``includes:`` relationships.

    This is the "table of contents" view — a lightweight map of every
    component and how they connect.

    Args:
        repo_root: Repository root to scan for bundle and behavior files.

    Returns:
        Complete, valid DOT string suitable for passing to ``dot -Tsvg``.
    """
    repo_root = Path(repo_root)

    # ── Discover files ──────────────────────────────────────────────────────
    # (role, path): role is "root", "behavior", or "bundle"
    files: list[tuple[str, Path]] = []

    root_bundle = repo_root / "bundle.md"
    if root_bundle.exists():
        files.append(("root", root_bundle))

    behaviors_dir = repo_root / "behaviors"
    if behaviors_dir.is_dir():
        for p in sorted(behaviors_dir.glob("*.yaml")):
            files.append(("behavior", p))
        for p in sorted(behaviors_dir.glob("*.yml")):
            files.append(("behavior", p))

    bundles_dir = repo_root / "bundles"
    if bundles_dir.is_dir():
        for p in sorted(bundles_dir.glob("*.yaml")):
            files.append(("bundle", p))
        for p in sorted(bundles_dir.glob("*.md")):
            files.append(("bundle", p))

    # ── Build file → node-id mapping (use resolved paths as keys) ──────────
    file_node_ids: dict[Path, str] = {}
    file_names: dict[Path, str] = {}
    file_roles: dict[Path, str] = {}

    for role, path in files:
        resolved = path.resolve()
        try:
            data, _ = parse_frontmatter(path)
        except Exception:
            data = {}
        bundle_info: dict = data.get("bundle") or {}
        name: str = bundle_info.get("name") or path.stem
        node_id = _sanitize_id(f"file_{name}")
        file_node_ids[resolved] = node_id
        file_names[resolved] = name
        file_roles[resolved] = role

    # ── Build nodes and edges ───────────────────────────────────────────────
    # Pre-compute per-file own token counts for aggregate computation of "bundle" role files.
    file_own_tokens: dict[Path, int] = {}
    for _role, _path in files:
        _resolved = _path.resolve()
        _raw = _path.read_text(encoding="utf-8")
        file_own_tokens[_resolved] = estimate_tokens(_raw)

    node_lines: list[str] = []
    edge_lines: list[str] = []
    # ext_id → node definition line (deduplicated externals)
    external_nodes: dict[str, str] = {}

    for role, path in files:
        resolved = path.resolve()
        name = file_names[resolved]
        node_id = file_node_ids[resolved]

        own_tok = file_own_tokens[resolved]

        # For standalone "bundle" files: aggregate = own tok + sum of same-repo local includes.
        # Behaviors and root use only their own raw content tokens.
        if role == "bundle":
            aggregate_tok = own_tok
            try:
                inc_data, _ = parse_frontmatter(path)
            except Exception:
                inc_data = {}
            for inc in inc_data.get("includes") or []:
                ref = str(inc.get("bundle") or "")
                if not ref:
                    continue
                local_path = _resolve_local_include(ref, repo_root)
                if local_path is not None and local_path in file_own_tokens:
                    aggregate_tok += file_own_tokens[local_path]
            tok = aggregate_tok
        else:
            tok = own_tok

        # Base fill: root=deep teal, others=light teal
        if role == "root":
            base_color = _COLOR_BUNDLE_ROOT
            style = '"filled,rounded,bold"'
        else:
            base_color = _COLOR_BEHAVIOR_LOCAL
            style = '"filled,rounded"'

        # Token tier can override the fill when cost is elevated
        tier_color = color_tier(tok, "bundle_body")
        fill_color = tier_color if tier_color != "#c8e6c9" else base_color

        label = _q(f"{name}\\n~{tok} tok")
        node_lines.append(
            f"    {node_id} [label={label}, shape=box,"
            f' fillcolor="{fill_color}", style={style}]'
        )

        # Parse includes and emit edges
        try:
            data, _ = parse_frontmatter(path)
        except Exception:
            data = {}
        includes: list[dict] = data.get("includes") or []

        for inc in includes:
            ref = str(inc.get("bundle") or "")
            if not ref:
                continue
            local_path = _resolve_local_include(ref, repo_root)
            if local_path is not None and local_path in file_node_ids:
                target_id = file_node_ids[local_path]
                edge_lines.append(f"    {node_id} -> {target_id}")
            else:
                eid = f"ext_{_sanitize_id(ref)}"
                if eid not in external_nodes:
                    disp = _extract_external_name(ref)
                    ext_label = _q(f"{disp}\\n(external)")
                    external_nodes[eid] = (
                        f"    {eid} [label={ext_label}, shape=box,"
                        f' fillcolor="{_COLOR_EXTERNAL_INCLUDE}",'
                        ' style="filled,rounded,dashed"]'
                    )
                edge_lines.append(f"    {node_id} -> {eid} [style=dashed]")

    # Append external nodes after all file nodes
    for ext_def in external_nodes.values():
        node_lines.append(ext_def)

    # ── Source hash ─────────────────────────────────────────────────────────
    body_str = "\n".join(node_lines + [""] + edge_lines)
    structural_hash = hashlib.sha256(body_str.encode()).hexdigest()

    # ── Graph title ─────────────────────────────────────────────────────────
    root_name = "bundle-overview"
    if root_bundle.exists():
        try:
            data, _ = parse_frontmatter(root_bundle)
            root_name = (data.get("bundle") or {}).get("name") or "bundle-overview"
        except Exception:
            pass
    graph_id = _sanitize_id(root_name)
    title = f"{root_name} \u2014 bundle overview"

    # ── Assemble full graph ─────────────────────────────────────────────────
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
        "}",
    ]

    return "\n".join(out)


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


