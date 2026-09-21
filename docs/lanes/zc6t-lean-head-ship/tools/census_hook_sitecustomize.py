"""Head-census hook: dump the composed Anthropic head, then exit before the wire.

$0 by construction -- os._exit() fires after the params dict is fully built and
BEFORE any HTTP call, so no tokens are ever purchased. Installed via PYTHONPATH
only; nothing under ~/.amplifier is written or modified.
"""
import builtins
import json
import os
import sys

OUT = os.environ.get("ZC6T_CENSUS_OUT")
if OUT:
    _real_import = builtins.__import__
    _state = {"patched": False}

    def _dump(params):
        blocks = params.get("system") or []
        if isinstance(blocks, str):
            blocks = [{"type": "text", "text": blocks}]
        sys_text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        tools = params.get("tools") or []
        rec = {
            "model": params.get("model"),
            "instr_chars": len(sys_text),
            "tools_chars": len(json.dumps(tools, ensure_ascii=False, separators=(",", ":"))),
            "head_chars": None,
            "n_tools": len(tools),
            "n_system_blocks": len(blocks),
            "n_context_file_blocks": sys_text.count("<context_file "),
            "you_are_amplifier_at": sys_text.find("You are Amplifier"),
        }
        rec["head_chars"] = rec["instr_chars"] + rec["tools_chars"]
        with open(OUT, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=2)
        with open(OUT + ".instructions.txt", "w", encoding="utf-8") as fh:
            fh.write(sys_text)
        with open(OUT + ".tools.json", "w", encoding="utf-8") as fh:
            json.dump(tools, fh, ensure_ascii=False, indent=2)

    def _hook(name, *a, **k):
        mod = _real_import(name, *a, **k)
        if not _state["patched"]:
            target = sys.modules.get("amplifier_module_provider_anthropic")
            if target is not None and hasattr(target, "_route_wire_only_params"):
                _state["patched"] = True
                _orig = target._route_wire_only_params

                def _patched(params):
                    _orig(params)
                    _dump(params)
                    sys.stderr.write("ZC6T_CENSUS_WRITTEN\n")
                    sys.stderr.flush()
                    os._exit(17)

                target._route_wire_only_params = _patched
        return mod

    builtins.__import__ = _hook
