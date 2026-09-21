#!/usr/bin/env python3
"""Deterministic before/after measurement of validate-bundle-repo's
`agent-description-validation` step. $0.00 API spend -- no LLM step is run.

Two measurements:

  1. THIS REPO -- the count deliverable. 32 -> 28 with `errors: []` preserved.
  2. A SYNTHETIC REPO -- the real deliverable. Five files that a maintainer
     would call agents, four of them broken in a different way. v3.12.0 reports
     all five checked and zero errors; v3.13.0 fails the three that are agents
     with unusable descriptions and names the one that is not an agent at all.

Run from the repo root:  python3 docs/lanes/dfni-validate-bundle-repo-count/measure.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from replay_step import run  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
AFTER = REPO / "recipes" / "validate-bundle-repo.yaml"
STEP = "agent-description-validation"
ENV = "VALIDATE_BUNDLE_REPO_PATH"


def before_recipe() -> Path:
    """origin/main's copy of the recipe, extracted to a temp file."""
    dest = Path(tempfile.gettempdir()) / "validate-bundle-repo.origin-main.yaml"
    dest.write_text(
        subprocess.run(
            ["git", "show", "origin/main:recipes/validate-bundle-repo.yaml"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout,
        encoding="utf-8",
    )
    return dest


def measure(recipe: Path, repo: Path) -> dict:
    # v3.12.0 reads {{repo_path}}; v3.13.0 reads $VALIDATE_BUNDLE_REPO_PATH.
    # Supplying both exercises each version through its own real mechanism.
    return run(recipe, STEP, repo, {ENV: str(repo)})


def report(label: str, out: dict) -> None:
    print(f"--- {label} ---")
    print(f"  candidates_scanned : {out.get('candidates_scanned', 'n/a (v3.12.0 did not report it)')}")
    print(f"  agents_checked     : {out['agents_checked']}")
    print(f"  non_agent_count    : {out.get('non_agent_count', 'n/a')}")
    print(f"  non_agent_reasons  : {out.get('non_agent_reasons', 'n/a')}")
    for entry in out.get("non_agents_found", []):
        print(f"      NON-AGENT  {entry['file']}  ({entry['reason']})")
    print(f"  passed             : {out['passed']}")
    print(f"  errors             : {len(out['errors'])}")
    for err in out["errors"]:
        print(f"      [{err['type']}] {err['file']} ({err.get('reason', '-')})")
    print(f"  warnings           : {[w['type'] for w in out['warnings']]}")
    print(f"  summary            : {out['summary']}")
    print()


def build_fixture(root: Path) -> Path:
    """Five files a maintainer would call agents; four of them broken."""
    repo = root / "fixture-repo"
    (repo / "agents").mkdir(parents=True)

    # 1. healthy -- the control.
    (repo / "agents" / "good.md").write_text(
        '---\nmeta:\n  name: good\n'
        '  description: "Does a real thing. USE WHEN a real thing is needed."\n'
        "---\nbody\n",
        encoding="utf-8",
    )
    # 2. a REAL agent whose frontmatter is MALFORMED -- the item's headline case.
    (repo / "agents" / "broken-yaml.md").write_text(
        '---\nmeta:\n  name: broken\n   description: "bad indent -> YAML error"\n'
        "  - stray\n---\nbody\n",
        encoding="utf-8",
    )
    # 3. frontmatter MISSING entirely.
    (repo / "agents" / "no-frontmatter.md").write_text(
        "# I am supposed to be an agent\n\nbut I lost my frontmatter.\n",
        encoding="utf-8",
    )
    # 4. `meta:` present, `description:` key absent.
    (repo / "agents" / "no-description.md").write_text(
        "---\nmeta:\n  name: nodesc\n---\nbody\n", encoding="utf-8"
    )
    # 5. `description:` present but empty -- the one case where "" is honest,
    #    and still an error, because an empty description is not a description.
    (repo / "agents" / "empty-description.md").write_text(
        '---\nmeta:\n  name: empty\n  description: ""\n---\nbody\n', encoding="utf-8"
    )
    return repo


def main() -> None:
    before = before_recipe()

    print("=" * 78)
    print("MEASUREMENT 1 -- this repository (the count)")
    print("=" * 78)
    print(f"repo: {REPO}")
    print(f"head: {subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=REPO, capture_output=True, text=True).stdout.strip()}")
    print()
    report("BEFORE  origin/main  v3.12.0", measure(before, REPO))
    report("AFTER   this branch  v3.13.0", measure(AFTER, REPO))

    print("=" * 78)
    print("MEASUREMENT 2 -- synthetic repo (the silent hole)")
    print("=" * 78)
    with tempfile.TemporaryDirectory() as tmp:
        fixture = build_fixture(Path(tmp))
        print("files:")
        for f in sorted(fixture.rglob("*.md")):
            print(f"  {f.relative_to(fixture).as_posix()}")
        print()
        report("BEFORE  origin/main  v3.12.0", measure(before, fixture))
        report("AFTER   this branch  v3.13.0", measure(AFTER, fixture))


if __name__ == "__main__":
    main()
