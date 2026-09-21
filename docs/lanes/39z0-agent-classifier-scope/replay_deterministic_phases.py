#!/usr/bin/env python3
"""Replay `validate-agents`' DETERMINISTIC phases, at zero API spend.

Phases 0-2 plus quality-classification are pure bash/python steps -- no model
is called. `quality_classification.quality_level` is what the report step then
RENDERS as the verdict:

    good       -> "PASS"                    (validate-agents.yaml, Verdict Selection)
    polish     -> "PASS WITH SUGGESTIONS"
    needs_work -> "PASS WITH WARNINGS"
    critical   -> "FAIL"

So the verdict is decided before any LLM step runs, and can be established
honestly without buying one. This lane's spend authority is $0.00, which is
why the numbers in DONE-NOTE.md come from here rather than from a paid run.

Each step's `command:` block is executed VERBATIM through bash, exactly as the
recipe engine would run it -- including the `VALIDATE_AGENTS_REPO_PATH=...`
environment prefix v1.7.0 introduces -- rather than re-implemented.

Usage:
    python replay_deterministic_phases.py <repo_path> [git_rev_of_recipe]
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

STEPS = (
    "environment-check",
    "agent-discovery",
    "structural-validation",
    "quality-classification",
)
OUTPUT_KEYS = {
    "environment-check": "env_check",
    "agent-discovery": "discovery_results",
    "structural-validation": "structural_results",
    "quality-classification": "quality_classification",
}


def step_command(recipe_text: str, step_id: str) -> str:
    """The step's `command:` block, de-indented, verbatim."""
    lines = recipe_text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f'- id: "{step_id}"')
    cmd = next(i for i in range(start, len(lines)) if lines[i].strip() == "command: |")
    body: list[str] = []
    for line in lines[cmd + 1 :]:
        if line.strip() and not line.startswith("      "):
            break
        body.append(line[6:])
    return "\n".join(body).rstrip() + "\n"


def run(recipe_text: str, repo_path: Path) -> dict:
    context: dict[str, str] = {"repo_path": str(repo_path)}
    outputs: dict[str, object] = {}
    for step_id in STEPS:
        command = step_command(recipe_text, step_id)
        for key, value in context.items():
            command = command.replace("{{" + key + "}}", value)
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, encoding="utf-8") as handle:
            handle.write(command)
            script = handle.name
        completed = subprocess.run(["bash", script], capture_output=True, text=True)
        if completed.returncode != 0:
            raise SystemExit(
                f"step {step_id} exited {completed.returncode}\n"
                f"stdout: {completed.stdout[:2000]}\nstderr: {completed.stderr[:2000]}"
            )
        payload = json.loads(completed.stdout)
        outputs[step_id] = payload
        context[OUTPUT_KEYS[step_id]] = json.dumps(payload)
    return outputs


def main() -> None:
    repo_path = Path(sys.argv[1]).resolve()
    rev = sys.argv[2] if len(sys.argv) > 2 else None
    if rev:
        recipe_text = subprocess.run(
            ["git", "show", f"{rev}:recipes/validate-agents.yaml"],
            cwd=repo_path, capture_output=True, text=True, check=True,
        ).stdout
    else:
        recipe_text = (repo_path / "recipes" / "validate-agents.yaml").read_text(encoding="utf-8")

    version = next(
        line.split(":", 1)[1].strip().strip('"')
        for line in recipe_text.splitlines()
        if line.startswith("version:")
    )
    outputs = run(recipe_text, repo_path)
    discovery = outputs["agent-discovery"]
    structural = outputs["structural-validation"]
    classification = outputs["quality-classification"]

    verdict = {
        "good": "PASS",
        "polish": "PASS WITH SUGGESTIONS",
        "needs_work": "PASS WITH WARNINGS",
        "critical": "FAIL",
    }[classification["quality_level"]]

    print(f"recipe version        : {version} ({rev or 'working tree'})")
    print(f"candidates scanned    : {discovery.get('candidates_scanned', '<absent in v1.6.0>')}")
    print(f"agents discovered     : {discovery['total_count']}")
    locations = discovery.get("location_counts")
    if locations is None:  # v1.5.1 and earlier reported only search_locations
        print(f"locations             : {len(discovery['search_locations'])} (search_locations, pre-v1.6.0)")
        for loc in discovery["search_locations"]:
            print(f"    - {loc}")
    else:
        print(f"locations             : {len(locations)} {locations}")
    print(f"classified non-agents : {discovery.get('non_agent_count', '<absent in v1.6.0>')}")
    for entry in discovery.get("non_agents_found", []):
        print(f"    - {entry['relative_path']}  ({entry['reason']})")
    print(f"structural errors     : {structural['summary']['errors']}")
    print(f"structural warnings   : {structural['summary']['warnings']}")
    for agent in structural.get("agents", []):
        for error in agent.get("errors", []):
            print(f"    ERROR {agent['name']}: {error['code']} -- {error['message']}")
    print(f"quality breakdown     : {classification.get('summary')}")
    print(f"quality_level         : {classification['quality_level']}")
    print(f"requires_llm_analysis : {classification['requires_llm_analysis']}")
    print(f"VERDICT               : {verdict}")


if __name__ == "__main__":
    main()
