"""Empirically resolve @namespace:path mentions for a loaded bundle."""
import asyncio, json, sys
from pathlib import Path
from amplifier_app_cli.lib.bundle_loader.discovery import AppBundleDiscovery
from amplifier_app_cli.paths import get_bundle_search_paths

MENTIONS = [
    "@foundation:context/shared/common-agent-base.md",
    "@foundation:context/amplifier-dev/ecosystem-map.md",
    "@foundation:context/amplifier-dev/dev-workflows.md",
    "@foundation:context/amplifier-dev/testing-patterns.md",
    "@anchors:context/system.md",
    "@anchors-amp-dev:context/system.md",
    "@anchors-amp-dev:context/amplifier-ecosystem.md",
    "@anchors-amp-dev:context/amplifier-dev/ecosystem-map.md",
]

async def main(bundle_ref: str, out=None, overrides_json="{}"):
    discovery = AppBundleDiscovery(search_paths=get_bundle_search_paths())
    import json as _j
    ov = _j.loads(overrides_json)
    if ov:
        from amplifier_app_cli.lib.bundle_loader.prepare import _build_include_source_resolver
        discovery.registry.set_include_source_resolver(_build_include_source_resolver(ov))
    uri = bundle_ref if bundle_ref.startswith(("git+","file://","http","zip+","/")) else discovery.find(bundle_ref)
    b = await discovery.registry.load(uri)
    rows = {"bundle": b.name, "uri": uri,
            "source_base_paths": {k: str(v) for k, v in sorted((b.source_base_paths or {}).items())},
            "mentions": {}}
    for m in MENTIONS:
        ns, name = m[1:].split(":", 1)
        p = None
        try:
            sbp = (b.source_base_paths or {}).get(ns)
            if sbp is not None:
                cand = Path(sbp) / name
                p = str(cand) if cand.exists() else None
        except Exception as e:
            p = f"ERR {e}"
        rows["mentions"][m] = {"namespace_registered": ns in (b.source_base_paths or {}), "resolved": p}
    txt = json.dumps(rows, indent=2, sort_keys=True)
    print(txt)
    if out: Path(out).write_text(txt + "\n")

asyncio.run(main(*sys.argv[1:]))
