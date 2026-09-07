"""Run the four description-alignment checks against an arbitrary repo path.

Independent verification harness for model_performance-8050. It imports the
step-body extractor from ``tests/test_description_alignment_checks.py`` so it
executes the RECIPES' own bodies -- never a re-implementation, which is the
failure mode the checks exist to catch.

    python docs/lanes/8050-principles-to-tooling/run_checks.py <repo_path> [label]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from tests.test_description_alignment_checks import (  # noqa: E402
    run_agents_structural,
    run_repo_step,
)


def summarise(repo: Path, label: str) -> dict:
    agents = run_repo_step("agent-description-validation", repo)
    skills = run_repo_step("skill-description-validation", repo)
    awareness = run_repo_step("awareness-redundancy-check", repo)
    head = run_repo_step("bundle-head-cost", repo)

    def counts(r: dict) -> tuple[int, int]:
        return len(r.get("errors", [])), len(r.get("warnings", []))

    bundles = head.get("bundle_details", [])
    largest = max((b.get("head_chars", 0) for b in bundles), default=0)

    out = {
        "label": label,
        "repo_path": str(repo),
        "agents": {
            "checked": agents.get("agents_checked", len(agents.get("agent_details", []))),
            "errors": counts(agents)[0],
            "warnings": counts(agents)[1],
            "error_types": sorted({e.get("type") for e in agents.get("errors", [])}),
            "warning_types": sorted({w.get("type") for w in agents.get("warnings", [])}),
        },
        "skills": {
            "checked": skills.get("skills_checked", len(skills.get("skill_details", []))),
            "errors": counts(skills)[0],
            "warnings": counts(skills)[1],
            "error_types": sorted({e.get("type") for e in skills.get("errors", [])}),
            "warning_types": sorted({w.get("type") for w in skills.get("warnings", [])}),
        },
        "awareness": {
            "files": len(awareness.get("file_details", awareness.get("files", []))),
            "best_coverage_max": max((f.get("best_coverage", 0) or 0 for f in awareness.get("file_details", [])), default=0),
            "warnings": counts(awareness)[1],
            "warning_types": sorted({w.get("type") for w in awareness.get("warnings", [])}),
        },
        "head_cost": {
            "bundles": len(bundles),
            "warnings": counts(head)[1],
            "largest_head_chars": largest,
            "threshold_chars": head.get("threshold_chars"),
        },
    }
    try:
        structural = run_agents_structural(repo)
        out["validate_agents_structural"] = {
            "agents": len(structural.get("agents", [])),
            "errors": sum(len(a.get("errors", [])) for a in structural.get("agents", [])),
            "warnings": sum(len(a.get("warnings", [])) for a in structural.get("agents", [])),
        }
    except Exception as exc:  # pragma: no cover - reported, never swallowed
        out["validate_agents_structural"] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


if __name__ == "__main__":
    path = Path(sys.argv[1]).resolve()
    label = sys.argv[2] if len(sys.argv) > 2 else path.name
    print(json.dumps(summarise(path, label), indent=2))
