"""Measure the delegate catalog before/after the render-time example strip.

Reads the OWNER'S OWN app list -- the agent set this host's sessions actually
mount -- and renders the delegate tool description twice: once with
``_strip_example_blocks`` neutralised to the identity function (which is
byte-for-byte what tool-delegate main does), and once with the branch's real
strip. Everything else in the renderer is identical between the two arms, so
the delta is the example/commentary payload and nothing else.

READ-ONLY. Touches ``~/.amplifier/cache`` and ``~/.amplifier/settings.yaml``
for reads only, writes nothing outside this lane's artifact root, and makes
NO API call -- the lane's spend authority is $0.

CALIBRATION (verify a claim against a value you already know): the item's own
census of this host puts the delegate tool description at 51,550 chars, of
which 48,268 is agent description across 66 agents, carrying 25 <example>
blocks. This script re-derives all four independently; if they disagree, the
reconstruction is wrong and the measurement below must not be quoted.

Usage:  uv run python docs/lanes/6f80-delegate-example-strip/evidence/measure_catalog_bytes.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "modules/tool-delegate"))

import amplifier_module_tool_delegate as tool_delegate  # noqa: E402
from amplifier_module_tool_delegate import DelegateTool  # noqa: E402

CACHE = Path.home() / ".amplifier" / "cache"

# The namespaces this host's sessions mount, as they appear in the rendered
# catalog. Taken from the delegate catalog of a live session on this host.
NAMESPACES = {
    "amplifier-online": "amplifier-bundle-amplifier-online-*",
    "amplifier-tester": "amplifier-bundle-amplifier-tester-*",
    "android-tester": "amplifier-bundle-android-tester-*",
    "attractor": "amplifier-bundle-attractor-*",
    "browser-tester": "amplifier-bundle-browser-tester-*",
    "context-intelligence": "amplifier-bundle-context-intelligence-*",
    "converge": "amplifier-bundle-converge-*",
    "digital-twin-universe": "amplifier-bundle-digital-twin-universe-*",
    "dot-graph": "amplifier-bundle-dot-graph-*",
    "infographic-builder": "infographic-builder-*",
    "ios-tester": "amplifier-bundle-ios-tester-*",
    "notify": "amplifier-bundle-notify-*",
    "reality-check": "amplifier-bundle-reality-check-*",
    "recipes": "amplifier-bundle-recipes-*",
    "stories": "amplifier-bundle-stories-*",
    "terminal-tester": "amplifier-bundle-terminal-tester-*",
    "work-tracker": "amplifier-work-tracker-*",
}

# Two agent sources that do not live at ``<cache-dir>/agents``.
EXTRA_AGENT_DIRS = {
    # `anchors` is a bundle inside the foundation repo.
    "anchors": sorted(CACHE.glob("amplifier-foundation-*/bundles/anchors/agents")),
    # `app-cli` ships inside the installed CLI package.
    "app-cli": sorted(
        Path.home().glob(
            ".local/share/uv/tools/amplifier/lib/python*/site-packages/"
            "amplifier_app_cli/_bundle/agents"
        )
    ),
}


def _frontmatter_description(path: Path) -> str | None:
    """``meta.description`` from an agent markdown file.

    Nested under ``meta:`` -- reading ``description:`` at the top level
    returns nothing for every agent in every repo, which is a known way to
    get a confident, plausible, wrong answer while exiting 0.
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    try:
        front = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return None
    if not isinstance(front, dict):
        return None
    meta = front.get("meta")
    if not isinstance(meta, dict):
        return None
    description = meta.get("description")
    return description if isinstance(description, str) else None


def collect_agents() -> dict[str, dict[str, str]]:
    agents: dict[str, dict[str, str]] = {}
    dirs: list[tuple[str, Path]] = []

    for namespace, pattern in NAMESPACES.items():
        for cache_dir in sorted(CACHE.glob(pattern)):
            agent_dir = cache_dir / "agents"
            if agent_dir.is_dir():
                dirs.append((namespace, agent_dir))
    for namespace, found in EXTRA_AGENT_DIRS.items():
        for agent_dir in found:
            dirs.append((namespace, agent_dir))

    for namespace, agent_dir in dirs:
        for agent_file in sorted(agent_dir.glob("*.md")):
            description = _frontmatter_description(agent_file)
            if description is None:
                continue
            agents[f"{namespace}:{agent_file.stem}"] = {"description": description}
    return agents


def make_tool(agents: dict[str, dict[str, str]]) -> DelegateTool:
    coordinator = MagicMock()
    coordinator.session_id = "measure-6f80"
    coordinator.config = {"agents": agents}
    coordinator.session_state = {}
    coordinator._tool_dispatch_context = {}
    coordinator.get_capability = lambda name: None
    coordinator.get = MagicMock(return_value=None)
    return DelegateTool(coordinator, {"features": {}, "settings": {"exclude_tools": []}})


def main() -> int:
    agents = collect_agents()
    if not agents:
        print("no agents discovered -- reconstruction failed, do not quote", file=sys.stderr)
        return 1

    tool = make_tool(agents)

    # AFTER: the branch renderer, strip live.
    after = tool.description

    # BEFORE: the same renderer with the strip neutralised. tool-delegate main
    # renders `cfg.get("description", ...)` verbatim, so identity here IS main.
    real_strip = tool_delegate._strip_example_blocks
    tool_delegate._strip_example_blocks = lambda text: text
    try:
        before = tool.description
    finally:
        tool_delegate._strip_example_blocks = real_strip

    raw_descriptions = "".join(a["description"] for a in agents.values())

    result = {
        "agents": len(agents),
        "example_open_tags_before": before.count("<example"),
        "commentary_open_tags_before": before.count("<commentary"),
        "example_open_tags_after": after.count("<example"),
        "commentary_open_tags_after": after.count("<commentary"),
        "raw_description_chars": len(raw_descriptions),
        "delegate_description_chars_before": len(before),
        "delegate_description_chars_after": len(after),
        "saved_chars": len(before) - len(after),
        "saved_pct": round(100 * (len(before) - len(after)) / len(before), 2),
        "agents_stripped": sorted(
            name
            for name, cfg in agents.items()
            if real_strip(cfg["description"]) != cfg["description"]
        ),
    }
    print(json.dumps(result, indent=2))

    # FIDELITY SPOT-CHECK: every non-blank line of every description that is
    # not inside a block must still be present in the rendered catalog.
    block = re.compile(
        r"(?:^[ \t]*)?<(example|commentary)\b[^>]*>.*?</\1\s*>(?:[ \t]*\n)?",
        re.DOTALL | re.IGNORECASE | re.MULTILINE,
    )
    missing: list[str] = []
    for name, cfg in agents.items():
        for line in block.sub("", cfg["description"]).splitlines():
            if line.strip() and line.strip() not in after:
                missing.append(f"{name}: {line.strip()[:80]}")
    print(f"\nfidelity: {len(missing)} surviving line(s) missing from the render")
    for entry in missing[:10]:
        print(f"  MISSING {entry}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
