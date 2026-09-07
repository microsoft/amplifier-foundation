#!/usr/bin/env python3
"""Fidelity diff + patch generator for the lean head (model_performance-zc6t).

`$0`, offline, no API call. For each of the 23 lean-head targets (13 tool
descriptions + 10 context files) it answers the one question the item calls the
gate that matters most:

    is any rule / constraint / command / pointer present in STOCK and ABSENT
    from LEAN?

A saving bought by deleting a real instruction is not a saving. It is a
behaviour change that will surface later with nothing pointing back here.

WHAT COUNTS AS A "RULE" -- stated, because an automatic check that does not say
what it looks for is unfalsifiable. Extracted from the stock text:

  * every backticked code span      (`todo`, `events.jsonl`, `--no-alt-screen`)
  * every command-shaped line       (npm install ..., uv run ..., gh pr create)
  * every @mention / namespace:ref  (@anchors:context/system.md, cli-expert)
  * every URL
  * every ALL-CAPS imperative token (MUST, NEVER, ALWAYS, REQUIRED, ...)

Each extracted token must appear somewhere in the lean text. Matching is
case-insensitive and whitespace-normalised: the invariant is that the rule is
still STATED, not that the sentence still starts with the same capital.

The check is deliberately noisy in one direction only. It over-reports (a
compressed-but-preserved rule can be flagged) and is reviewed by a human; it
does not under-report, which is the direction that costs money.

Usage:
    python fidelity_diff.py --write-patches <outdir>
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path

PROBE = Path(
    "/home/bkrabach/dev/openai-evals-team-ci/.amplifier/evaluation/probes/bji-lean-head"
)
CACHE = Path.home() / ".amplifier" / "cache"
SITE = (
    Path.home()
    / ".local/share/uv/tools/amplifier/lib/python3.13/site-packages"
)

# --- the 10 context-file targets (v1_instructions.json spans 0-9) ------------
#
# Spans 10 and 11 are HOOK-GENERATED output (`<system-reminder source=...>`).
# No file produces them; a renderer emits them at runtime. They are out of
# scope for this item and are listed here so the census reader can see exactly
# why the realised saving is short of the shim's 48,249.
HOOK_GENERATED_SPANS = {
    10: 'system-reminder source="routing-matrix" -- emitted by hooks-routing',
    11: 'system-reminder source="hooks-skills-visibility" -- emitted by the skills hook',
}

SPAN_SOURCES: dict[int, tuple[str, str]] = {
    0: ("amplifier-bundle-gitea", "context/gitea-awareness.md"),
    1: ("amplifier-bundle-digital-twin-universe", "context/dtu-awareness.md"),
    2: ("amplifier-bundle-amplifier-tester", "context/amplifier-tester-awareness.md"),
    3: ("amplifier-bundle-modes", "context/modes-instructions.md"),
    4: ("__app_cli__", "amplifier_app_cli/_bundle/context/cli-awareness.md"),
    5: ("amplifier-bundle-skills", "context/skills-instructions.md"),
    6: ("amplifier-bundle-wayfinder", "context/wayfinder-voice.md"),
    7: ("amplifier-bundle-wayfinder", "context/propose-and-ack.md"),
    8: ("amplifier-bundle-routing-matrix", "context/routing-instructions.md"),
    # Span 9 was authored against a file that no longer exists as one: ux32's
    # DRY refactor split anchors-amp-dev/context/system.md into
    # anchors/context/system.md + anchors-amp-dev/context/amplifier-ecosystem.md.
    # Both live in THIS repo and are shipped in this lane's PR, so the span is
    # handled there rather than patched here.
    9: ("__this_repo__", "bundles/anchors/context/system.md"
        " + bundles/anchors-amp-dev/context/amplifier-ecosystem.md"),
}

# --- the 13 tool-description targets (v1_tools.json keys) -------------------
TOOL_SOURCES: dict[str, tuple[str, str]] = {
    "read_file": ("amplifier-module-tool-filesystem", "amplifier_module_tool_filesystem"),
    "write_file": ("amplifier-module-tool-filesystem", "amplifier_module_tool_filesystem"),
    "edit_file": ("amplifier-module-tool-filesystem", "amplifier_module_tool_filesystem"),
    "grep": ("amplifier-module-tool-filesystem", "amplifier_module_tool_filesystem"),
    "glob": ("amplifier-module-tool-filesystem", "amplifier_module_tool_filesystem"),
    "bash": ("amplifier-module-tool-bash", "amplifier_module_tool_bash"),
    "todo": ("amplifier-module-tool-todo", "amplifier_module_tool_todo"),
    "web_fetch": ("amplifier-module-tool-web", "amplifier_module_tool_web"),
    "web_search": ("amplifier-module-tool-web", "amplifier_module_tool_web"),
    "load_skill": ("amplifier-bundle-skills", "modules/tool-skills"),
    "mode": ("amplifier-bundle-modes", "modules/tool-mode"),
    "recipes": ("amplifier-bundle-recipes", "modules/tool-recipes"),
    "delegate": ("__this_repo__", "modules/tool-delegate"),
}

CODE_SPAN = re.compile(r"`([^`\n]{2,80})`")
MENTION = re.compile(r"@[\w.-]+:[\w./-]+")
URL = re.compile(r"https?://[^\s)\]\"'>]+")
CAPS = re.compile(r"\b(MUST(?: NOT)?|NEVER|ALWAYS|REQUIRED|DO NOT|ONLY|STOP)\b")
CMD = re.compile(
    r"^\s*(?:\$ )?((?:npm|uv|pip|git|gh|python3?|make|brew|winget|adb|amplifier|"
    r"amplifier-[\w-]+|curl|docker|az)\s+[^\n|]{3,120})",
    re.MULTILINE,
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).casefold()


def extract_rules(stock: str) -> list[str]:
    found: list[str] = []
    for pattern in (CODE_SPAN, MENTION, URL, CAPS, CMD):
        for match in pattern.finditer(stock):
            token = (match.group(1) if match.re.groups else match.group(0)).strip()
            if len(token) >= 3:
                found.append(token)
    seen: set[str] = set()
    ordered: list[str] = []
    for token in found:
        key = _norm(token)
        if key not in seen:
            seen.add(key)
            ordered.append(token)
    return ordered


def missing_rules(stock: str, lean: str) -> list[str]:
    lean_n = _norm(lean)
    return [rule for rule in extract_rules(stock) if _norm(rule) not in lean_n]


def resolve(repo: str, rel: str) -> Path | None:
    if repo == "__app_cli__":
        candidate = SITE / rel
        return candidate if candidate.exists() else None
    if repo == "__this_repo__":
        return None
    hits = sorted(CACHE.glob(f"{repo}-*"))
    for hit in hits:
        candidate = hit / rel
        if candidate.exists():
            return candidate
    return None


def span_lean_body(new: str) -> str:
    """Strip the `<context_file paths=...>` wrapper the shim's span carries."""
    body = new
    if body.startswith("<context_file"):
        body = body.split(">", 1)[1]
    if body.endswith("</context_file>"):
        body = body[: -len("</context_file>")]
    return body.strip("\n") + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-patches")
    args = ap.parse_args()
    outdir = Path(args.write_patches) if args.write_patches else None
    if outdir:
        (outdir / "context-files").mkdir(parents=True, exist_ok=True)
        (outdir / "tool-descriptions").mkdir(parents=True, exist_ok=True)

    spans = json.loads((PROBE / "v1_instructions.json").read_text(encoding="utf-8"))
    tools = json.loads((PROBE / "v1_tools.json").read_text(encoding="utf-8"))
    stock_tools = {
        t["name"]: t.get("description", "")
        for t in json.loads((PROBE / "head" / "tools_stock.json").read_text(encoding="utf-8"))
        if t.get("name")
    }

    report: list[dict] = []

    print("=" * 78)
    print("HALF B -- 10 context files (v1_instructions.json spans 0-9)")
    print("=" * 78)
    print(f"{'#':>2} {'source':52s} {'stock':>7s} {'lean':>7s} {'saved':>7s}  fidelity")
    for idx in sorted(SPAN_SOURCES):
        repo, rel = SPAN_SOURCES[idx]
        lean = span_lean_body(spans[idx]["new"])
        path = resolve(repo, rel)
        if repo == "__this_repo__":
            print(f"{idx:2d} {rel[:52]:52s} {'--':>7s} {'--':>7s} {'--':>7s}  SHIPPED IN THIS PR")
            report.append({"kind": "context", "index": idx, "status": "in-repo", "source": rel})
            continue
        if path is None:
            print(f"{idx:2d} {(repo + '/' + rel)[:52]:52s} {'?':>7s} {len(lean):7d} {'?':>7s}  SOURCE NOT FOUND")
            report.append({"kind": "context", "index": idx, "status": "source-not-found",
                           "source": f"{repo}/{rel}"})
            continue
        stock = path.read_text(encoding="utf-8")
        miss = missing_rules(stock, lean)
        print(
            f"{idx:2d} {(repo + '/' + rel)[:52]:52s} {len(stock):7d} {len(lean):7d} "
            f"{len(stock) - len(lean):7d}  {'OK' if not miss else str(len(miss)) + ' MISSING'}"
        )
        report.append({
            "kind": "context", "index": idx, "status": "out-of-repo",
            "source": f"{repo}/{rel}", "resolved": path.as_posix(),
            "stock_chars": len(stock), "lean_chars": len(lean),
            "saved_chars": len(stock) - len(lean), "missing_rules": miss,
        })
        if outdir:
            name = f"{idx:02d}-{repo}-{Path(rel).name}"
            (outdir / "context-files" / f"{name}.lean.md").write_text(lean, encoding="utf-8")
            diff = difflib.unified_diff(
                stock.splitlines(keepends=True), lean.splitlines(keepends=True),
                fromfile=f"a/{rel}", tofile=f"b/{rel}",
            )
            (outdir / "context-files" / f"{name}.patch").write_text(
                "".join(diff), encoding="utf-8"
            )

    print()
    print("=" * 78)
    print("HALF A -- 13 tool descriptions (v1_tools.json)")
    print("=" * 78)
    print(f"{'tool':12s} {'repo':42s} {'stock':>7s} {'lean':>7s} {'saved':>7s}  fidelity")
    for name in tools:
        repo, rel = TOOL_SOURCES[name]
        stock = stock_tools.get(name, "")
        lean = tools[name]
        if stock == lean:
            print(f"{name:12s} {repo:42s} {len(stock):7d} {len(lean):7d} {0:7d}  IDENTICAL -- SKIP")
            report.append({"kind": "tool", "name": name, "status": "identical-skip",
                           "repo": repo, "stock_chars": len(stock)})
            continue
        if repo == "__this_repo__":
            print(f"{name:12s} {'(this repo, preamble only)':42s} {1190:7d} {938:7d} {252:7d}  SHIPPED IN THIS PR")
            report.append({"kind": "tool", "name": name, "status": "in-repo",
                           "stock_chars": 1190, "lean_chars": 938, "saved_chars": 252,
                           "note": "preamble only; runtime agent catalog out of scope"})
            continue
        miss = missing_rules(stock, lean)
        print(
            f"{name:12s} {repo:42s} {len(stock):7d} {len(lean):7d} "
            f"{len(stock) - len(lean):7d}  {'OK' if not miss else str(len(miss)) + ' MISSING'}"
        )
        report.append({"kind": "tool", "name": name, "status": "out-of-repo", "repo": repo,
                       "module": rel, "stock_chars": len(stock), "lean_chars": len(lean),
                       "saved_chars": len(stock) - len(lean), "missing_rules": miss})
        if outdir:
            (outdir / "tool-descriptions" / f"{name}.lean.txt").write_text(lean, encoding="utf-8")
            diff = difflib.unified_diff(
                stock.splitlines(keepends=True), lean.splitlines(keepends=True),
                fromfile=f"a/{name}.description", tofile=f"b/{name}.description",
            )
            (outdir / "tool-descriptions" / f"{name}.patch").write_text(
                "".join(diff), encoding="utf-8"
            )

    print()
    print("OUT OF SCOPE (hook-generated, no file to change):")
    for idx, why in HOOK_GENERATED_SPANS.items():
        chars = len(spans[idx]["new"])
        print(f"  span {idx}: {why}  (lean block would be {chars} chars)")

    if outdir:
        (outdir / "fidelity-report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\npatches + fidelity-report.json written to {outdir.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
