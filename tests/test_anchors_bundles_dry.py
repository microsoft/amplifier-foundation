"""Structural guards for the anchors / anchors-amp-dev bundle pair.

`anchors-amp-dev` is `anchors` plus a thin Amplifier-ecosystem knowledge layer.
That relationship is only real if it is an *include*, not a copy: the moment the
two trees carry parallel copies of the same file, one side gets a fix and the
other silently ships the old text. That is exactly what happened between #327
(applied to the anchors-amp-dev copies only) and #341 (which swept the repo-root
`agents/` only) -- two sweeps, neither of which reached both trees.

Each test below pins one half of that invariant and fails loudly when a copy
reappears. All five fail on the pre-refactor tree; see
`docs/lanes/ux32-anchors-dry-refactor/DONE-NOTE.md` for the recorded
fail-before output.

Pure-Python and filesystem-only on purpose -- no shell, no `stat -c`, nothing
platform-dependent, so these run identically on Linux, macOS and CI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
BUNDLES_DIR = REPO_ROOT / "bundles"
ANCHORS_DIR = BUNDLES_DIR / "anchors"
AMP_DEV_DIR = BUNDLES_DIR / "anchors-amp-dev"

# Files that are legitimately present in both bundle trees: every bundle needs
# its own entrypoint and its own README. Everything else must be inherited.
SHARED_PATH_ALLOWLIST = {"bundle.md", "README.md"}

# The Amplifier-ecosystem knowledge docs. Exactly one copy of each may exist on
# the shipped surface -- a second copy is the drift vector this refactor removes.
ECOSYSTEM_DOCS = ("ecosystem-map.md", "dev-workflows.md", "testing-patterns.md")

# `experiments/` used to hold the frozen pre-promotion originals (anchors was
# promoted out of `experiments/behavioral-anchor`). Nothing there is a registered
# bundle or composed at runtime, so its copies cannot drift into a session -- but
# they are copies, and they are named in the failure message rather than hidden,
# so a reader is never left wondering why the count looked wrong.
#
# `test_exactly_one_copy_of_each_ecosystem_doc_repo_wide` (guard d', #362-6phe)
# now forbids that second copy outright: a frozen copy still rots, and a reader
# who greps for `ecosystem-map.md` still finds two answers. This exclusion is
# kept only so the *shipped-surface* guard below keeps reporting an unshipped
# copy by name instead of merging it into the shipped count.
UNSHIPPED_TREES = (".git", "experiments")

# The validator's own agent-discovery scope, copied deliberately rather than
# approximated. `@foundation:recipes/validate-bundle-repo.yaml`'s
# `agent-description-validation` step globs
# `rglob("*/agents/*.md") + glob("agents/*.md")` and drops any path containing
# one of these parts. Two validators disagreeing about which files exist is
# exactly how the 11 `experiments/` violators survived #341 and ux32:
# `validate-agents` discovers 23 agents and never walks `experiments/`, while
# `validate-bundle-repo` discovers 45 and does. This guard follows the wider
# one. `docs` is added on top of the validator's set: `docs/` holds frozen lane
# records that quote violations verbatim as evidence and must never be rewritten.
AGENT_SCAN_EXCLUDED_PARTS = {
    "test-fixtures",
    "tests",
    "node_modules",
    ".git",
    ".venv",
    "docs",
}

# The anchors include must be a full git URL with a #subdirectory= fragment, not
# a bare `anchors` name. A bare name resolves only where the CLI happens to have
# registered that namespace; the full URL keeps the bundle liftable, which is
# the "self-contained by design" convention the anchors README states.
ANCHORS_INCLUDE_RE = re.compile(
    r"^git\+https://github\.com/microsoft/amplifier-foundation@[^#]+"
    r"#subdirectory=bundles/anchors(?:/bundle\.md)?$"
)

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


def _frontmatter(path: Path) -> dict:
    """Parse a markdown file's YAML frontmatter into a dict ({} when absent)."""
    match = _FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
    if not match:
        return {}
    return yaml.safe_load(match.group(1)) or {}


def _agent_candidate_files() -> list[Path]:
    """Every file the validator's SCAN reaches, before classification.

    Not "root `agents/` + bundles" -- that narrower scope is what let the
    `experiments/` copies keep their `<example>` blocks through two sweeps.
    See AGENT_SCAN_EXCLUDED_PARTS for the exclusions and why `docs` joins them.
    """
    candidates = list(REPO_ROOT.rglob("*/agents/*.md")) + list(
        (REPO_ROOT / "agents").glob("*.md")
    )
    seen: set[Path] = set()
    found: list[Path] = []
    for candidate in candidates:
        relative = candidate.relative_to(REPO_ROOT)
        if AGENT_SCAN_EXCLUDED_PARTS & set(relative.parts):
            continue
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        found.append(candidate)
    return sorted(found)


def _is_agent_file(path: Path) -> bool:
    """Is this scanned file an agent DEFINITION, or merely under `agents/`?

    Keys on `meta:` -- the loader's own contract, quoted from
    `docs/AGENT_AUTHORING.md`: "Agents ARE bundles. They use the same file
    format and are loaded via load_bundle(). The only difference is the
    frontmatter key (`meta:` vs `bundle:`)."

    NOT the directory name. `context/agents/*.md` are context documents loaded
    via `context:` (`behaviors/agents.yaml`, `behaviors/tasks.yaml`); nothing
    spawns them and they carry no frontmatter because they are not agents. The
    directory collides on the word "agents", which is how the widened
    validator came to report four NO_FRONTMATTER errors against files that
    were never agents.

    Frontmatter that exists but will not parse counts as an agent on purpose --
    a file plainly trying to be one should reach the validator that reports the
    YAML error, not be quietly reclassified out of the run.
    """
    match = _FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
    if not match:
        return False
    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return True
    return isinstance(frontmatter, dict) and "meta" in frontmatter


def _agent_files() -> list[Path]:
    """Every agent DEFINITION in the repo: scanned, then classified."""
    return [path for path in _agent_candidate_files() if _is_agent_file(path)]


def _relative_files(root: Path) -> set[str]:
    """POSIX-style relative paths, so the comparison reads the same on Windows."""
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


class TestAmpDevIsAThinVariant:
    """anchors-amp-dev declares a layer, not a second runtime."""

    def test_declares_no_session_tools_or_hooks(self) -> None:
        """The runtime is anchors'. Re-declaring it forks the mount plan.

        `session:`, `tools:` and `hooks:` are anchors' single source of truth.
        A duplicate block here is how the two bundles' orchestrator, context
        window, tool roster or hook set drift apart without anyone editing both.
        """
        frontmatter = _frontmatter(AMP_DEV_DIR / "bundle.md")
        declared = [k for k in ("session", "tools", "hooks") if k in frontmatter]
        assert not declared, (
            f"bundles/anchors-amp-dev/bundle.md re-declares {declared}; these "
            "must come from the anchors include so there is exactly one source "
            "of truth for the runtime."
        )

    def test_includes_anchors_by_full_url(self) -> None:
        """The anchors include is a full URL with a #subdirectory= fragment."""
        includes = _frontmatter(AMP_DEV_DIR / "bundle.md").get("includes") or []
        sources = [
            entry.get("bundle") if isinstance(entry, dict) else entry
            for entry in includes
        ]
        matching = [s for s in sources if isinstance(s, str) and ANCHORS_INCLUDE_RE.match(s)]
        assert matching, (
            "bundles/anchors-amp-dev/bundle.md must include anchors by full URL "
            "(git+https://github.com/microsoft/amplifier-foundation@main"
            "#subdirectory=bundles/anchors/bundle.md). "
            f"Found includes: {sources}"
        )


class TestNoParallelCopies:
    """The two bundle trees may not carry the same file twice."""

    def test_no_same_path_file_in_both_bundles(self) -> None:
        """A same-path file in both trees is a fix that will land on one side.

        #327 edited the anchors-amp-dev copies of six agents and system.md and
        left the anchors originals behind; the CLI default bundle then shipped
        the un-fixed text for weeks. This test makes that state unreachable.
        """
        shared = _relative_files(ANCHORS_DIR) & _relative_files(AMP_DEV_DIR)
        offenders = sorted(shared - SHARED_PATH_ALLOWLIST)
        assert not offenders, (
            "These paths exist under BOTH bundles/anchors/ and "
            f"bundles/anchors-amp-dev/: {offenders}. Only "
            f"{sorted(SHARED_PATH_ALLOWLIST)} may be duplicated -- everything "
            "else must be inherited via the anchors include."
        )

    def test_exactly_one_copy_of_each_ecosystem_doc(self) -> None:
        """Each Amplifier-ecosystem doc exists exactly once on the shipped surface.

        Copies under `experiments/` are excluded and reported, not counted --
        see UNSHIPPED_TREES for why.
        """
        for name in ECOSYSTEM_DOCS:
            found = sorted(
                p.relative_to(REPO_ROOT)
                for p in REPO_ROOT.rglob(name)
                if ".git" not in p.relative_to(REPO_ROOT).parts
            )
            # Compare on path *parts*, never on a string separator -- rglob
            # yields backslashes on Windows and this runs there too.
            unshipped = [r.as_posix() for r in found if r.parts[0] in UNSHIPPED_TREES]
            shipped = [
                r.as_posix() for r in found if r.parts[0] not in UNSHIPPED_TREES
            ]
            assert len(shipped) == 1, (
                f"Expected exactly one shipped copy of {name}, found "
                f"{len(shipped)}: {shipped}. Two copies drift; the canonical "
                "location is context/amplifier-dev/. "
                f"(Excluded, frozen and unshipped: {unshipped})"
            )

    def test_exactly_one_copy_of_each_ecosystem_doc_repo_wide(self) -> None:
        """Guard d': one copy repo-wide, `experiments/` included.

        The guard above excludes `experiments/` and merely *names* the copies
        it finds there. That exclusion is precisely why a third copy of every
        ecosystem doc survived the ux32 refactor: `experiments/behavioral-
        anchor-amplifier-dev/context/amplifier-dev/` still carried the
        pre-#341 text, including the deleted "Always delegate" line, and the
        guard reported it as an excluded footnote rather than a failure.

        "Frozen" is not a safety property. A stale copy is still the second
        answer a `grep` returns and the second file a reader may edit. Git
        history preserves the original; the working tree does not need to.
        """
        for name in ECOSYSTEM_DOCS:
            found = sorted(
                p.relative_to(REPO_ROOT).as_posix()
                for p in REPO_ROOT.rglob(name)
                if ".git" not in p.relative_to(REPO_ROOT).parts
            )
            assert len(found) == 1, (
                f"Expected exactly one copy of {name} anywhere in the repo, "
                f"found {len(found)}: {found}. The canonical location is "
                "context/amplifier-dev/. A copy under experiments/ counts: "
                "frozen text still drifts and still answers a grep."
            )


class TestAgentDescriptionsCarryNoExampleBlocks:
    """#340 rejects `<example>`/`<commentary>` in descriptions -- everywhere."""

    def test_no_example_or_commentary_in_any_agent_description(self) -> None:
        """Extends #341's sweep, which covered the repo-root `agents/` only.

        `description-authoring-principles.md` V3 is 'no `<example>` blocks --
        not "at most 2", zero'. #341 (1b16a7d) applied that to the 16 root
        agents; ux32 (200dfe6) added `bundles/**/agents`; neither reached
        `experiments/`, where 11 violators sat until
        `validate-bundle-repo.yaml` -- which discovers 45 agents where
        `validate-agents.yaml` discovers 23 -- raised `example_block_present`
        on them.

        The scope is now the validator's own (see `_agent_files`), not a
        hand-listed set of directories, so a new `*/agents/` tree anywhere in
        the repo is covered the day it is created rather than the day someone
        remembers to add it here.
        """
        offenders: list[str] = []
        for agent_file in _agent_files():
            description = (_frontmatter(agent_file).get("meta") or {}).get(
                "description", ""
            )
            if not isinstance(description, str):
                continue
            tags = [
                tag
                for tag in ("<example>", "<commentary>")
                if tag in description.lower()
            ]
            if tags:
                rel = agent_file.relative_to(REPO_ROOT).as_posix()
                offenders.append(f"{rel} ({', '.join(tags)})")
        assert not offenders, (
            "Agent descriptions must contain no <example>/<commentary> blocks "
            "(description-authoring-principles.md V3, #340). Offenders: "
            + "; ".join(offenders)
        )


# --- Discovery-scope parity (xe1u) -----------------------------------------
# The three places in this repo that answer "which files are agent
# definitions?" must give the SAME answer. When they did not, the cost was a
# three-sweep recurrence: `validate-agents` walked three hardcoded directories
# and found 23 agents; `validate-bundle-repo` and `_agent_files` above globbed
# the repo and found 32 (45 before the `experiments/` deletion). The 11
# `<example>` violators that lived in the 9-file difference survived #341 and
# ux32 because the recipe most people run never looked at them -- and reported
# a clean "23 found, 3 locations" the whole time.
#
# 6phe closed the gap in the guard; xe1u closed it in `validate-agents`
# (v1.6.0). These tests are the tripwire that keeps it closed. A shared
# discovery *implementation* was considered and declined -- see
# `docs/lanes/xe1u-validate-agents-discovery/DONE-NOTE.md` -- so proven parity
# is the mechanism standing in for it.
#
# 39z0 (v1.7.0) added the classifier layer and the Windows fix, and split the
# parity claim in two accordingly: the SCAN must reach the same files, and the
# CLASSIFIER must then judge the same subset of them agents. Both are asserted
# separately below, on purpose -- a classifier that quietly narrowed the walk
# would still satisfy an agent-set comparison if the guard narrowed with it.
# The Windows defect these guards caught is pinned twice more: once at its
# cause (no path interpolated into Python source) and once by reproducing an
# escape-shaped path on every platform.
VALIDATE_AGENTS_RECIPE = REPO_ROOT / "recipes" / "validate-agents.yaml"
VALIDATE_BUNDLE_REPO_RECIPE = REPO_ROOT / "recipes" / "validate-bundle-repo.yaml"

# `validate-bundle-repo` excludes the five; the guard and `validate-agents`
# add `docs` on top, because `docs/` holds frozen lane records that quote
# violations verbatim as evidence and must never be rewritten. That one
# documented delta is asserted explicitly below rather than papered over --
# it excludes zero files today (there is no `docs/**/agents/` directory), so
# all three walks still return the same set.
DOCUMENTED_EXCLUSION_DELTA = {"docs"}


def _literal_set(text: str, name: str) -> list[set[str]]:
    """Every `NAME = {...}` set literal in `text`, in source order."""
    import ast

    return [
        set(ast.literal_eval(match.group(1)))
        for match in re.finditer(rf"\b{name}\s*=\s*(\{{[^}}]*\}})", text)
    ]


# The environment variable `validate-agents` v1.7.0 passes the repo path
# through. It is NOT interpolated into the Python heredoc, and must not be:
# `Path("{{repo_path}}")` puts a filesystem path inside a Python string
# literal, where every backslash becomes an escape sequence. On a GitHub
# Actions Windows runner (`D:\a\<repo>\<repo>`) `\a` became `\x07`, the path
# did not exist, and discovery printed `agents_found: []` and exited 0 --
# a PASS over zero files. These guards are what caught it, on Windows
# 3.11/3.12/3.13, while every POSIX leg stayed green.
DISCOVERY_REPO_PATH_ENV = "VALIDATE_AGENTS_REPO_PATH"


# `validate-bundle-repo` v3.13.0 passes the repo path through this variable,
# for the same reason and after the same measured failure: ALL TEN of its
# Python steps wrote `repo_path = "{{repo_path}}"`, so every one of them walked
# an empty tree on Windows and reported a clean result over zero files.
REPO_RECIPE_PATH_ENV = "VALIDATE_BUNDLE_REPO_PATH"

# The agent classifier's source span, shared by both recipes. Bounded by its
# first and last lines rather than by line numbers, so an edit above or below
# it does not silently change what is being compared.
_CLASSIFIER_FIRST_LINE = "FRONTMATTER_RE = re.compile("
_CLASSIFIER_LAST_LINE = 'return False, "frontmatter_without_meta"'


def _step_body(recipe: Path, step_id: str) -> str:
    """One bash step's Python heredoc, verbatim and de-indented."""
    lines = recipe.read_text(encoding="utf-8").splitlines()
    step = next(i for i, line in enumerate(lines) if line.strip() == f'- id: "{step_id}"')
    start = next(
        i for i in range(step, len(lines)) if lines[i].rstrip().endswith("<< 'EOF'")
    )
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "EOF")
    return "\n".join(line[6:] for line in lines[start + 1 : end])


def _python_step_bodies(recipe: Path) -> dict[str, str]:
    """Every `<< 'EOF'` heredoc in a recipe, keyed by the step id it belongs to."""
    lines = recipe.read_text(encoding="utf-8").splitlines()
    bodies: dict[str, str] = {}
    current = None
    index = 0
    while index < len(lines):
        match = re.match(r'\s*- id: "([^"]+)"', lines[index])
        if match:
            current = match.group(1)
        if lines[index].rstrip().endswith("<< 'EOF'") and current is not None:
            indent = len(lines[index]) - len(lines[index].lstrip())
            index += 1
            collected = []
            while lines[index].strip() != "EOF":
                collected.append(lines[index][indent:])
                index += 1
            bodies[current] = "\n".join(collected)
        index += 1
    return bodies


def _classifier_source(recipe: Path, step_id: str) -> str:
    """The agent classifier block, verbatim, from one step of one recipe."""
    lines = _step_body(recipe, step_id).splitlines()
    start = next(
        i for i, line in enumerate(lines) if line.strip().startswith(_CLASSIFIER_FIRST_LINE)
    )
    end = next(
        i for i in range(start, len(lines)) if lines[i].strip() == _CLASSIFIER_LAST_LINE
    )
    return "\n".join(lines[start : end + 1])


def _discovery_step_body() -> str:
    """The agent-discovery step's Python heredoc, verbatim and de-indented."""
    return _step_body(VALIDATE_AGENTS_RECIPE, "agent-discovery")


def _run_repo_recipe_step(step_id: str, repo_path: Path | None = None) -> dict:
    """Execute one `validate-bundle-repo` step and return its JSON payload.

    Executed, not re-implemented -- same discipline as
    `_run_recipe_discovery_step`, and for the same reason: a test that
    reimplements the walk agrees with itself while the recipe drifts.
    """
    import json
    import os
    import subprocess
    import sys
    import tempfile

    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(_step_body(VALIDATE_BUNDLE_REPO_RECIPE, step_id))
        script = handle.name
    env = dict(os.environ)
    env[REPO_RECIPE_PATH_ENV] = str(repo_path if repo_path is not None else REPO_ROOT)
    completed = subprocess.run(
        [sys.executable, script], capture_output=True, text=True, check=True, env=env
    )
    return json.loads(completed.stdout)


def _run_recipe_discovery_step(repo_path: Path | None = None) -> dict:
    """Execute `validate-agents`' agent-discovery step and return its payload.

    Executed, not re-implemented. A test that reimplements the glob would pass
    while the recipe diverged, which is the failure mode being guarded.

    The repo path is handed over the way the recipe hands it over -- through
    the environment -- so this harness exercises the real mechanism rather than
    a friendlier substitute.
    """
    import json
    import os
    import subprocess
    import sys
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as handle:
        handle.write(_discovery_step_body())
        script = handle.name
    env = dict(os.environ)
    env[DISCOVERY_REPO_PATH_ENV] = str(repo_path if repo_path is not None else REPO_ROOT)
    completed = subprocess.run(
        [sys.executable, script], capture_output=True, text=True, check=True, env=env
    )
    return json.loads(completed.stdout)


class TestDiscoveryScopeParity:
    """One answer to "which files are agents?", across all three scopes."""

    def test_exclusion_sets_agree_across_both_recipes_and_this_guard(self) -> None:
        """A fourth exclusion list is how the first divergence started."""
        agents_sets = _literal_set(
            VALIDATE_AGENTS_RECIPE.read_text(encoding="utf-8"), "EXCLUDED_PARTS"
        )
        assert len(agents_sets) == 1, (
            "validate-agents.yaml must declare exactly one EXCLUDED_PARTS set, "
            f"found {len(agents_sets)}"
        )
        assert agents_sets[0] == AGENT_SCAN_EXCLUDED_PARTS, (
            "validate-agents.yaml's EXCLUDED_PARTS must equal this module's "
            f"AGENT_SCAN_EXCLUDED_PARTS. recipe={sorted(agents_sets[0])} "
            f"guard={sorted(AGENT_SCAN_EXCLUDED_PARTS)}"
        )

        repo_sets = _literal_set(
            VALIDATE_BUNDLE_REPO_RECIPE.read_text(encoding="utf-8"), "EXCLUDED_DIRS"
        )
        assert repo_sets, "validate-bundle-repo.yaml declares no EXCLUDED_DIRS set"
        assert all(found == repo_sets[0] for found in repo_sets), (
            "validate-bundle-repo.yaml's EXCLUDED_DIRS literals disagree with each "
            f"other: {[sorted(found) for found in repo_sets]}"
        )
        assert AGENT_SCAN_EXCLUDED_PARTS - repo_sets[0] == DOCUMENTED_EXCLUSION_DELTA, (
            "The only permitted difference between validate-bundle-repo's "
            "EXCLUDED_DIRS and the wider scope is the documented "
            f"{sorted(DOCUMENTED_EXCLUSION_DELTA)}. Actual extra: "
            f"{sorted(AGENT_SCAN_EXCLUDED_PARTS - repo_sets[0])}"
        )
        assert not repo_sets[0] - AGENT_SCAN_EXCLUDED_PARTS, (
            "validate-bundle-repo excludes something the wider scope does not: "
            f"{sorted(repo_sets[0] - AGENT_SCAN_EXCLUDED_PARTS)}"
        )

    def test_validate_agents_scans_exactly_the_guards_candidate_files(self) -> None:
        """Parity of the SCAN, before either side classifies anything.

        Kept separate from the agent-set parity below on purpose: a classifier
        that quietly narrowed the walk would still satisfy an agent-set
        comparison if both sides narrowed together. This pins the wider claim
        v1.6.0 established -- the same FILES are reached -- so adding a
        classifier on top cannot be used to shrink coverage unnoticed.
        """
        payload = _run_recipe_discovery_step()
        from_recipe = {agent["relative_path"] for agent in payload["agents_found"]} | {
            entry["relative_path"] for entry in payload["non_agents_found"]
        }
        from_guard = {
            path.relative_to(REPO_ROOT).as_posix() for path in _agent_candidate_files()
        }
        assert from_recipe == from_guard, (
            "validate-agents' scan and this guard's _agent_candidate_files must "
            "reach the same files. In recipe not guard: "
            f"{sorted(from_recipe - from_guard)}; in guard not recipe: "
            f"{sorted(from_guard - from_recipe)}"
        )
        assert payload["candidates_scanned"] == len(from_guard), (
            f"candidates_scanned={payload['candidates_scanned']} disagrees with "
            f"the {len(from_guard)} files this guard scanned"
        )

    def test_validate_agents_discovers_exactly_the_guards_agent_files(self) -> None:
        """Proven parity, by running the recipe's own step -- not asserted."""
        payload = _run_recipe_discovery_step()
        from_recipe = {agent["relative_path"] for agent in payload["agents_found"]}
        from_guard = {path.relative_to(REPO_ROOT).as_posix() for path in _agent_files()}
        assert from_recipe == from_guard, (
            "validate-agents' discovery and this guard's _agent_files must find "
            "the same files. In recipe not guard: "
            f"{sorted(from_recipe - from_guard)}; in guard not recipe: "
            f"{sorted(from_guard - from_recipe)}"
        )
        assert from_recipe, "discovery found no agents at all -- a PASS over nothing"

    def test_classifier_keys_on_meta_not_on_the_directory_name(self) -> None:
        """`context/agents/*.md` are context documents, not agents.

        They are loaded as `context:` by `behaviors/agents.yaml` and
        `behaviors/tasks.yaml`; nothing spawns them, and they carry no
        frontmatter because they are not agents. Classifying by directory name
        made them four NO_FRONTMATTER ERRORs and flipped the verdict on a repo
        with no real agent defects.

        They must be classified out AND named -- an unexplained drop is the
        same silence that let 11 violators survive two sweeps.
        """
        payload = _run_recipe_discovery_step()
        agents = {agent["relative_path"] for agent in payload["agents_found"]}
        non_agents = {entry["relative_path"]: entry["reason"] for entry in payload["non_agents_found"]}

        context_docs = {
            path.relative_to(REPO_ROOT).as_posix()
            for path in (REPO_ROOT / "context" / "agents").glob("*.md")
        }
        assert context_docs, "expected context/agents/*.md to exist in this repo"
        assert not (context_docs & agents), (
            "context/agents/*.md were classified as AGENTS: "
            f"{sorted(context_docs & agents)}. They declare no `meta:` -- see "
            "docs/AGENT_AUTHORING.md."
        )
        assert context_docs <= set(non_agents), (
            "context/agents/*.md must be REPORTED as non-agents, not silently "
            f"dropped. Missing from non_agents_found: {sorted(context_docs - set(non_agents))}"
        )
        for path in sorted(context_docs):
            assert non_agents[path] == "no_frontmatter", (
                f"{path} was excluded for {non_agents[path]!r}; expected "
                "'no_frontmatter'"
            )

    def test_discovery_never_interpolates_the_repo_path_into_python_source(self) -> None:
        """The Windows defect, pinned at its cause rather than its symptom.

        `Path("{{repo_path}}")` makes every backslash in a filesystem path an
        escape sequence. `D:\\a\\repo\\repo` -- a GitHub Actions Windows
        checkout -- became `D:\\x07epo...`, did not exist, and discovery
        returned an empty set and exited 0.
        """
        body = _discovery_step_body()
        # Comment lines are exempt: the step documents the defect it fixes, and
        # substitution into a comment cannot mangle a path anyone uses.
        code = [
            line
            for line in body.splitlines()
            if not line.lstrip().startswith("#")
        ]
        offenders = [line for line in code if "{{repo_path}}" in line]
        assert not offenders, (
            "The agent-discovery heredoc interpolates {{repo_path}} into Python "
            "source. A path inside a string literal is escape-processed, which "
            f"silently destroys every Windows path. Offending line(s): {offenders}. "
            f"Pass it through ${DISCOVERY_REPO_PATH_ENV} instead."
        )
        assert DISCOVERY_REPO_PATH_ENV in body, (
            f"The agent-discovery step must read the repo path from "
            f"${DISCOVERY_REPO_PATH_ENV}"
        )

    def test_discovery_survives_a_repo_path_containing_escape_sequences(self) -> None:
        """The Windows defect, reproduced on every platform.

        A directory literally named `a\\test` gives a repo path whose string
        form carries `\\a` and `\\t` -- the exact shape of `D:\\a\\...` on a
        Windows runner. Under the v1.6.0 interpolation this found zero agents
        and reported success; it must now find the file that is really there.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "a\\test\\nested"
            (repo / "agents").mkdir(parents=True)
            (repo / "agents" / "probe.md").write_text(
                '---\nmeta:\n  name: probe\n  description: "probe agent"\n---\nbody\n',
                encoding="utf-8",
            )
            (repo / "context" / "agents").mkdir(parents=True)
            (repo / "context" / "agents" / "notes.md").write_text(
                "# Notes\n\nA context document, not an agent.\n", encoding="utf-8"
            )

            payload = _run_recipe_discovery_step(repo)

        assert payload["errors"] == [], (
            f"discovery reported errors on an escape-shaped path: {payload['errors']}"
        )
        assert {a["relative_path"] for a in payload["agents_found"]} == {"agents/probe.md"}, (
            "discovery lost the agent under a path containing backslash escape "
            f"sequences -- found {payload['agents_found']}"
        )
        assert {n["relative_path"] for n in payload["non_agents_found"]} == {
            "context/agents/notes.md"
        }

    def test_discovery_fails_loudly_on_a_missing_repo_path(self) -> None:
        """Never an empty payload with exit 0 -- that is the vacuous PASS."""
        import subprocess

        with pytest.raises(subprocess.CalledProcessError):
            _run_recipe_discovery_step(REPO_ROOT / "no-such-directory-exists-here")

    # ------------------------------------------------------------------
    # validate-bundle-repo's half of the same question (v3.13.0, lane dfni)
    # ------------------------------------------------------------------

    def test_both_recipes_carry_the_same_classifier_source(self) -> None:
        """The classifier is ONE implementation, present in two files.

        Recipe steps are self-contained heredocs with no import mechanism
        between them, so a literally shared function is not available (lane
        xe1u declined that route with the reason). Byte-identity is the
        strongest substitute: two copies that must agree character for
        character cannot drift into two different answers, which is the
        asymmetry the whole #341 -> ux32 -> 6phe -> xe1u -> 39z0 chain existed
        to remove.
        """
        from_agents = _classifier_source(VALIDATE_AGENTS_RECIPE, "agent-discovery")
        from_repo = _classifier_source(
            VALIDATE_BUNDLE_REPO_RECIPE, "agent-description-validation"
        )
        assert from_agents, "validate-agents.yaml carries no classifier block"
        assert from_agents == from_repo, (
            "The agent classifier has diverged between validate-agents.yaml and "
            "validate-bundle-repo.yaml. It is copied byte-for-byte on purpose; "
            "edit one and copy it to the other, never edit them separately.\n"
            f"--- validate-agents ---\n{from_agents}\n"
            f"--- validate-bundle-repo ---\n{from_repo}"
        )

    def test_validate_bundle_repo_classifies_exactly_the_guards_agent_files(self) -> None:
        """Both recipes report the SAME population, proven by running both.

        Before v3.13.0 this recipe counted every candidate under an `agents/`
        directory: `agents_checked: 32` where the true agent count is 28.
        """
        payload = _run_repo_recipe_step("agent-description-validation")

        scanned = {detail["file"] for detail in payload["agent_details"]} | {
            entry["file"] for entry in payload["non_agents_found"]
        }
        from_guard_candidates = {
            path.relative_to(REPO_ROOT).as_posix() for path in _agent_candidate_files()
        }
        assert scanned == from_guard_candidates, (
            "validate-bundle-repo's scan and this guard's _agent_candidate_files "
            f"must reach the same files. In recipe not guard: {sorted(scanned - from_guard_candidates)}; "
            f"in guard not recipe: {sorted(from_guard_candidates - scanned)}"
        )

        agents = {detail["file"] for detail in payload["agent_details"]}
        from_guard_agents = {
            path.relative_to(REPO_ROOT).as_posix() for path in _agent_files()
        }
        assert agents == from_guard_agents, (
            "validate-bundle-repo's classifier and this guard's _agent_files must "
            f"agree. In recipe not guard: {sorted(agents - from_guard_agents)}; "
            f"in guard not recipe: {sorted(from_guard_agents - agents)}"
        )
        assert agents, "the recipe classified no agents at all -- a PASS over nothing"
        assert payload["agents_checked"] == len(from_guard_agents), (
            f"agents_checked={payload['agents_checked']} disagrees with the "
            f"{len(from_guard_agents)} agents this guard classified"
        )
        assert payload["candidates_scanned"] == len(from_guard_candidates)

    def test_validate_bundle_repo_names_every_non_agent_it_excludes(self) -> None:
        """A classifier that drops silently reintroduces the original defect.

        Lane 39z0 F4: the mitigation for a classifier's own silent-drop risk is
        that every excluded file is reported by name AND reason. A real agent
        that lost its `meta:` block must appear here, not vanish.
        """
        payload = _run_repo_recipe_step("agent-description-validation")
        non_agents = {entry["file"]: entry["reason"] for entry in payload["non_agents_found"]}

        context_docs = {
            path.relative_to(REPO_ROOT).as_posix()
            for path in (REPO_ROOT / "context" / "agents").glob("*.md")
        }
        assert context_docs, "expected context/agents/*.md to exist in this repo"
        assert context_docs <= set(non_agents), (
            "context/agents/*.md must be REPORTED as non-agents, not silently "
            f"dropped. Missing: {sorted(context_docs - set(non_agents))}"
        )
        assert payload["non_agent_count"] == len(payload["non_agents_found"])
        for path in sorted(context_docs):
            assert non_agents[path] == "no_frontmatter"

    def test_validate_bundle_repo_fails_an_agent_whose_description_cannot_be_read(
        self,
    ) -> None:
        """The defect this version exists to close, at its cause.

        v3.12.0's `extract_description()` returned `""` for a broken file, a
        missing `description:`, and an empty one alike -- and `""` passed every
        check: 0 tokens is under budget, and it contains no <example> and no
        <commentary>. Four files a maintainer would call agents therefore
        reported `errors: []`.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "fixture"
            (repo / "agents").mkdir(parents=True)
            (repo / "agents" / "good.md").write_text(
                '---\nmeta:\n  name: good\n  description: "A real description."\n---\nbody\n',
                encoding="utf-8",
            )
            (repo / "agents" / "broken-yaml.md").write_text(
                '---\nmeta:\n  name: broken\n   description: "bad indent"\n  - stray\n---\nbody\n',
                encoding="utf-8",
            )
            (repo / "agents" / "no-description.md").write_text(
                "---\nmeta:\n  name: nodesc\n---\nbody\n", encoding="utf-8"
            )
            (repo / "agents" / "empty-description.md").write_text(
                '---\nmeta:\n  name: empty\n  description: ""\n---\nbody\n',
                encoding="utf-8",
            )
            (repo / "agents" / "no-frontmatter.md").write_text(
                "# no frontmatter at all\n", encoding="utf-8"
            )

            payload = _run_repo_recipe_step("agent-description-validation", repo)

        assert payload["passed"] is False, (
            "a repo containing four unusable agent descriptions reported PASS"
        )
        errors = {error["file"]: (error["type"], error["reason"]) for error in payload["errors"]}
        assert errors.get("agents/broken-yaml.md") == (
            "agent_frontmatter_invalid",
            "unparseable_frontmatter",
        )
        assert errors.get("agents/no-description.md") == (
            "agent_description_missing",
            "no_description_key",
        )
        assert errors.get("agents/empty-description.md") == (
            "agent_description_missing",
            "description_empty",
        )
        assert "agents/good.md" not in errors, "the healthy agent was failed"

        # The frontmatter-less file is not an agent at all -- excluded by the
        # classifier and NAMED, never silently passed with an empty description.
        assert {entry["file"] for entry in payload["non_agents_found"]} == {
            "agents/no-frontmatter.md"
        }

        # And nothing reached the budget checks with an empty string: every
        # non-ok status carries a null token count rather than a passing 0.
        for detail in payload["agent_details"]:
            if detail["description_status"] != "ok":
                assert detail["description_tokens"] is None, (
                    f"{detail['file']} was budget-checked against an unreadable "
                    "description -- 0 tokens is exactly the false pass v3.13.0 removes"
                )

    def test_validate_bundle_repo_fails_a_behavior_whose_frontmatter_cannot_be_read(
        self,
    ) -> None:
        """The same pattern, second instance (the cross-check).

        `extract_frontmatter_yaml()` returned a bare `{}` for every failure,
        and `{}` satisfies both of that step's checks vacuously.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "fixture"
            (repo / "behaviors").mkdir(parents=True)
            (repo / "bundle.md").write_text(
                '---\nbundle:\n  name: root\n---\nbody\n', encoding="utf-8"
            )
            (repo / "behaviors" / "healthy.md").write_text(
                "---\nbundle:\n  name: healthy\n---\nbody\n", encoding="utf-8"
            )
            (repo / "behaviors" / "broken.md").write_text(
                "---\nbundle:\n  name: broken\n   includes: [oops\n---\nbody\n",
                encoding="utf-8",
            )

            payload = _run_repo_recipe_step("behavior-reference-hygiene", repo)

        types = {(error["behavior"], error["type"]) for error in payload["errors"]}
        assert ("broken", "behavior_frontmatter_invalid") in types, (
            "a behavior with unparseable frontmatter passed both hygiene checks: "
            f"{payload['errors']}"
        )
        assert payload["passed"] is False
        assert ("healthy", "behavior_frontmatter_invalid") not in types

    def test_validate_bundle_repo_never_drops_a_mode_file_silently(self) -> None:
        """The same pattern, third instance -- silent DROP rather than false pass.

        `if "mode" not in fm: continue` discarded an unreadable file, an absent
        frontmatter block and an unparseable one alike, with nothing in the
        output to say so.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "fixture"
            (repo / "modes").mkdir(parents=True)
            (repo / "modes" / "healthy.md").write_text(
                '---\nmode:\n  name: healthy\n  description: "A mode."\n---\nbody\n',
                encoding="utf-8",
            )
            (repo / "modes" / "broken.md").write_text(
                "---\nmode:\n  name: broken\n   description: bad indent\n  - stray\n---\nbody\n",
                encoding="utf-8",
            )
            (repo / "modes" / "notes.md").write_text(
                "# Just notes, not a mode\n", encoding="utf-8"
            )

            payload = _run_repo_recipe_step("mode-validation", repo)

        assert payload["candidates_scanned"] == 3
        assert payload["modes_checked"] == 1

        broken = [
            error for error in payload["errors"]
            if error["type"] == "mode_frontmatter_invalid"
        ]
        assert [error["file"] for error in broken] == ["modes/broken.md"], (
            f"a mode with unparseable frontmatter was skipped silently: {payload}"
        )

        assert {entry["file"]: entry["reason"] for entry in payload["non_modes_found"]} == {
            "modes/notes.md": "no_frontmatter"
        }

    def test_no_validate_bundle_repo_step_interpolates_the_repo_path_into_source(
        self,
    ) -> None:
        """The Windows defect, pinned across EVERY step of this recipe.

        Lane 39z0 fixed three steps in validate-agents. The identical line was
        still present in all TEN Python steps here -- and this recipe's steps
        carry `on_error: continue`, so a path that silently resolved to nothing
        produced a clean payload rather than a visible failure.
        """
        offenders: dict[str, list[str]] = {}
        for step_id, body in _python_step_bodies(VALIDATE_BUNDLE_REPO_RECIPE).items():
            # Comment lines are exempt: the steps document the defect they fix.
            hits = [
                line
                for line in body.splitlines()
                if "{{repo_path}}" in line and not line.lstrip().startswith("#")
            ]
            if hits:
                offenders[step_id] = hits
        assert not offenders, (
            "These validate-bundle-repo steps interpolate {{repo_path}} into "
            "Python source. A path inside a string literal is escape-processed, "
            "which silently destroys every Windows path. Pass it through "
            f"${REPO_RECIPE_PATH_ENV} instead. Offenders: {offenders}"
        )

    def test_validate_bundle_repo_survives_a_repo_path_with_escape_sequences(self) -> None:
        """The Windows defect, reproduced on every platform."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "a\\test\\nested"
            (repo / "agents").mkdir(parents=True)
            (repo / "agents" / "probe.md").write_text(
                '---\nmeta:\n  name: probe\n  description: "probe agent"\n---\nbody\n',
                encoding="utf-8",
            )
            (repo / "context" / "agents").mkdir(parents=True)
            (repo / "context" / "agents" / "notes.md").write_text(
                "# Notes\n\nA context document, not an agent.\n", encoding="utf-8"
            )

            payload = _run_repo_recipe_step("agent-description-validation", repo)

        assert payload["errors"] == [], (
            f"the step reported errors on an escape-shaped path: {payload['errors']}"
        )
        assert {detail["file"] for detail in payload["agent_details"]} == {
            "agents/probe.md"
        }, f"the agent was lost under a backslash-escape path: {payload}"
        assert {entry["file"] for entry in payload["non_agents_found"]} == {
            "context/agents/notes.md"
        }

    def test_validate_bundle_repo_reports_a_missing_repo_path_as_an_error(self) -> None:
        """Never a clean payload over zero files.

        This step is `on_error: continue`, and quality-classification treats an
        unparseable payload as `{"passed": True, "skipped": True}` -- so a
        non-zero exit here would HIDE the failure behind a clean skip. The
        error has to travel in the payload, where it reaches critical_count.
        """
        payload = _run_repo_recipe_step(
            "agent-description-validation", REPO_ROOT / "no-such-directory-here"
        )
        assert payload["passed"] is False
        assert [error["type"] for error in payload["errors"]] == ["agent_scan_path_error"]
        assert payload.get("skipped") is not True, (
            "a bad repo path was reported as a clean skip"
        )
