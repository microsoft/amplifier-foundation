#!/usr/bin/env python3
"""Replay one bash step's Python heredoc from a recipe, at $0.00 API spend.

Extracts the named step's `<< 'EOF' ... EOF` body verbatim, substitutes
`{{repo_path}}` the way the recipe engine would, runs it, returns the JSON.
Executed, never re-implemented -- a re-implementation would agree with itself
while the recipe diverged, which is the failure mode under investigation.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def step_body(recipe: Path, step_id: str) -> tuple[str, int]:
    lines = recipe.read_text(encoding="utf-8").splitlines()
    step = next(i for i, ln in enumerate(lines) if ln.strip() == f'- id: "{step_id}"')
    start = next(i for i in range(step, len(lines)) if lines[i].rstrip().endswith("<< 'EOF'"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "EOF")
    indent = len(lines[start]) - len(lines[start].lstrip())
    body = "\n".join(ln[indent:] for ln in lines[start + 1 : end])
    return body, indent


def run(recipe: Path, step_id: str, repo_path: Path, env_extra: dict | None = None) -> dict:
    body, _ = step_body(recipe, step_id)
    src = body.replace("{{repo_path}}", str(repo_path))
    env = dict(os.environ)
    env.update(env_extra or {})
    proc = subprocess.run(
        [sys.executable, "-c", src], capture_output=True, text=True, env=env
    )
    if proc.returncode != 0:
        raise SystemExit(f"exit {proc.returncode}\nSTDERR:\n{proc.stderr}")
    return json.loads(proc.stdout)


if __name__ == "__main__":
    recipe = Path(sys.argv[1])
    step_id = sys.argv[2]
    repo = Path(sys.argv[3]) if len(sys.argv) > 3 else Path.cwd()
    env_extra = {}
    for kv in sys.argv[4:]:
        k, _, v = kv.partition("=")
        env_extra[k] = v.replace("{{repo_path}}", str(repo))
    print(json.dumps(run(recipe, step_id, repo, env_extra), indent=2))
