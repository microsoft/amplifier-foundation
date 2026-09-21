#!/usr/bin/env python3
"""Replay `validate-bundle-repo`'s DETERMINISTIC chain, at $0.00 API spend.

Every step from `environment-check` through `quality-classification` is
`type: "bash"` -- the single exception, `validate-recipes` (`type: "recipe"`),
is guarded by `condition: {{validation_flags.validate_recipes}} == 'true'` and
has a $0 sibling, `set-default-recipe-validation`, that supplies the same
`recipe_validation` output when the flag is false. So with the recipe's own
default (`validate_recipes: "false"`) the whole verdict chain is reachable
without buying a single model call.

`quality_classification.quality_level` is what the report step then RENDERS as
the verdict (validate-bundle-repo.yaml, Phase 4):

    good       -> "PASS"
    polish     -> "PASS WITH SUGGESTIONS"
    needs_work -> "PASS WITH WARNINGS"
    critical   -> "FAIL"

This lane's spend authority is $0.00 for code and tests, which is why the
numbers in DONE-NOTE.md come from here rather than from a paid run. Same
technique as 39z0's `replay_deterministic_phases.py` (validate-agents) and
dfni's `replay_step.py` (single-step), generalised to the full chain.

Each step's `command:` block is executed VERBATIM through bash exactly as the
recipe engine would run it -- including the `VALIDATE_BUNDLE_REPO_PATH=...`
environment prefix v3.13.0 introduces -- rather than re-implemented. A
re-implementation would agree with itself while the recipe diverged, which is
precisely the failure aaa5c47 was fixing.

DELIBERATELY STOPS at `quality-classification`. The later
`bundle-overview-regen-write` step rewrites `bundle.dot`/`bundle.png`
UNCONDITIONALLY (only its *input* is gated on `enhance_diagrams`) -- see 6phe
finding F1 -- so this driver never reaches it.

Usage:
    python replay_validate_bundle_repo.py <repo_path> [key=value ...]
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

STOP_AFTER = "quality-classification"

RECIPE_DEFAULTS = {
    "validate_agents": "false",
    "validate_recipes": "false",
    "validate_all": "false",
    "recipes_dir": "recipes",
    "known_agents": "",
    "enhance_diagrams": "true",
    "root_bundle_repo": "false",
    "published_distributions": "",
}


def parse_steps(text: str) -> list[dict]:
    """Every step's id / type / condition / output / verbatim command body."""
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if re.match(r'^  - id: "', ln)]
    steps: list[dict] = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        block = lines[start:end]
        step: dict = {
            "id": re.match(r'^  - id: "(.+)"', block[0]).group(1),  # type: ignore[union-attr]
            "type": None,
            "condition": None,
            "output": None,
            "parse_json": False,
            "command": None,
        }
        for i, ln in enumerate(block):
            if m := re.match(r'^    type: "(.+)"', ln):
                step["type"] = m.group(1)
            elif m := re.match(r'^    condition: "(.+)"', ln):
                step["condition"] = m.group(1)
            elif m := re.match(r'^    output: "(.+)"', ln):
                step["output"] = m.group(1)
            elif re.match(r"^    parse_json: true", ln):
                step["parse_json"] = True
            elif ln.strip() == "command: |":
                body: list[str] = []
                for cl in block[i + 1 :]:
                    if cl.strip() and not cl.startswith("      "):
                        break
                    body.append(cl[6:])
                step["command"] = "\n".join(body).rstrip() + "\n"
        steps.append(step)
    return steps


def substitute(text: str, context: dict) -> str:
    """Render `{{key}}` and `{{key.dotted.path}}` from context."""

    def render(match: re.Match) -> str:
        parts = match.group(1).strip().split(".")
        value = context.get(parts[0])
        if value is None:
            return match.group(0)
        for part in parts[1:]:
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    return match.group(0)
            if not isinstance(value, dict) or part not in value:
                return match.group(0)
            value = value[part]
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    return re.sub(r"\{\{([^}]+)\}\}", render, text)


def condition_holds(condition: str, context: dict) -> bool:
    """Evaluate the `{{x}} == y` / `!= y` conditions this recipe uses.

    Both quoted (`== 'true'`) and bare (`== true`) right-hand sides appear.
    """
    rendered = substitute(condition, context).strip()
    if m := re.match(r"^(.*?)\s*(==|!=)\s*(.*)$", rendered):
        left = m.group(1).strip().strip("'\"")
        op = m.group(2)
        right = m.group(3).strip().strip("'\"")
        return left == right if op == "==" else left != right
    raise SystemExit(f"unhandled condition shape: {condition!r} -> {rendered!r}")


def main() -> None:
    repo_path = Path(sys.argv[1]).resolve()
    overrides = dict(kv.split("=", 1) for kv in sys.argv[2:])

    recipe = (
        Path(__file__).resolve().parents[3] / "recipes" / "validate-bundle-repo.yaml"
    )
    text = recipe.read_text(encoding="utf-8")
    version = next(
        ln.split(":", 1)[1].strip().strip('"')
        for ln in text.splitlines()
        if ln.startswith("version:")
    )

    context: dict = {"repo_path": str(repo_path), **RECIPE_DEFAULTS, **overrides}
    ran: list[str] = []
    skipped: list[str] = []

    for step in parse_steps(text):
        if step["type"] != "bash":
            skipped.append(f"{step['id']} (type={step['type']}, not deterministic)")
            continue
        if step["condition"] and not condition_holds(step["condition"], context):
            skipped.append(f"{step['id']} (condition false)")
            continue
        command = substitute(step["command"], context)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".sh", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(command)
            script = handle.name
        done = subprocess.run(["bash", script], capture_output=True, text=True)
        if done.returncode != 0:
            raise SystemExit(
                f"step {step['id']} exited {done.returncode}\n"
                f"stdout: {done.stdout[:2000]}\nstderr: {done.stderr[:2000]}"
            )
        ran.append(step["id"])
        if step["output"]:
            payload = done.stdout.strip()
            try:
                context[step["output"]] = json.loads(payload)
            except json.JSONDecodeError:
                context[step["output"]] = payload
        if step["id"] == STOP_AFTER:
            break

    classification = context.get("quality_classification")
    if not isinstance(classification, dict):
        raise SystemExit(f"no quality_classification produced; ran: {ran}")

    verdict = {
        "good": "PASS",
        "polish": "PASS WITH SUGGESTIONS",
        "needs_work": "PASS WITH WARNINGS",
        "critical": "FAIL",
    }[classification["quality_level"]]

    discovery = context.get("repo_discovery") or context.get("discovery_results") or {}
    agent_desc = context.get("agent_description_results") or {}

    print(f"recipe version              : {version}")
    print(f"repo_path                   : {repo_path}")
    print(f"root_bundle_repo            : {context['root_bundle_repo']}")
    print(f"steps executed              : {len(ran)}")
    print(f"steps skipped               : {len(skipped)}")
    for entry in skipped:
        print(f"    - {entry}")
    if isinstance(discovery, dict) and discovery.get("bundles_found"):
        counts = {k: len(v) for k, v in discovery["bundles_found"].items()}
        print(f"bundles discovered          : {counts}")
    if isinstance(agent_desc, dict) and agent_desc.get("summary"):
        print(f"agent_description_validation: {json.dumps(agent_desc['summary'])}")
    for key in sorted(
        k for k in context if k.endswith("_results") or k.endswith("_check")
    ):
        value = context[key]
        if isinstance(value, dict) and isinstance(value.get("summary"), dict):
            summary = value["summary"]
            if {"errors", "warnings"} & set(summary):
                print(
                    f"  {key:38} errors={summary.get('errors')} "
                    f"warnings={summary.get('warnings')}"
                )
    print(f"quality summary             : {json.dumps(classification.get('summary'))}")
    print(f"quality_level               : {classification['quality_level']}")
    print(f"VERDICT                     : {verdict}")

    findings = (
        classification.get("critical_issues") or classification.get("errors") or []
    )
    if findings:
        print("critical issues:")
        for item in findings:
            print(f"    - {item}")


if __name__ == "__main__":
    main()
