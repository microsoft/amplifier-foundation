"""Dump a bundle's composed mount plan as canonical JSON. $0, no network beyond cache."""
import asyncio, json, sys, os
from pathlib import Path
from amplifier_app_cli.lib.bundle_loader.discovery import AppBundleDiscovery
from amplifier_app_cli.paths import get_bundle_search_paths

async def main(bundle_ref: str, out: str, overrides_json: str = "{}"):
    bundle_source_overrides = json.loads(overrides_json)
    discovery = AppBundleDiscovery(search_paths=get_bundle_search_paths())
    if bundle_source_overrides:
        from amplifier_app_cli.lib.bundle_loader.prepare import _build_include_source_resolver
        discovery.registry.set_include_source_resolver(_build_include_source_resolver(bundle_source_overrides))
    uri = bundle_ref if bundle_ref.startswith(("git+","file://","http","zip+","/")) else discovery.find(bundle_ref)
    b = await discovery.registry.load(uri)
    plan = {
        "name": b.name,
        "version": b.version,
        "description": (b.description or "").strip(),
        "includes": list(b.includes),
        "session": b.session,
        "providers": b.providers,
        "tools": b.tools,
        "hooks": b.hooks,
        "agents": sorted(b.agents.keys()) if isinstance(b.agents, dict) else b.agents,
        "context_keys": sorted(b.context.keys()) if isinstance(b.context, dict) else b.context,
        "instruction": b.instruction,
        "namespaces": sorted((b.source_base_paths or {}).keys()),
    }
    Path(out).write_text(json.dumps(plan, indent=2, sort_keys=True, default=str) + "\n")
    print(f"wrote {out}: uri={uri}")
    print("namespaces:", plan["namespaces"])

asyncio.run(main(*sys.argv[1:]))
