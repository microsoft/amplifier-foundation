#!/usr/bin/env python3
"""Extract raw.system + delegate-tool agent roster from a session's first llm:request.
Writes text sidecars + a markers JSON. Never prints the payload."""
import json, sys, pathlib, re
sess, out = sys.argv[1], sys.argv[2]
p = pathlib.Path(sess) / "events.jsonl"
s = None; tools = None
with p.open(encoding="utf-8") as f:
    for line in f:
        if '"llm:request"' not in line: continue
        try: ev = json.loads(line)
        except Exception: continue
        if ev.get("event") != "llm:request": continue
        raw = (ev.get("data") or {}).get("raw") or {}
        sysf = raw.get("system")
        if isinstance(sysf, list) and sysf and isinstance(sysf[0], dict): s = sysf[0].get("text","")
        elif isinstance(sysf, str): s = sysf
        else: s = raw.get("instructions") or ""
        tools = raw.get("tools") or []
        break
if s is None: print("NO llm:request FOUND"); sys.exit(2)
pathlib.Path(out + ".raw_system.txt").write_text(s, encoding="utf-8")

# delegate tool description carries the agent roster
deleg = ""
for t in tools:
    nm = t.get("name") or (t.get("function") or {}).get("name") or ""
    if nm == "delegate":
        deleg = t.get("description") or (t.get("function") or {}).get("description") or ""
        break
pathlib.Path(out + ".delegate_desc.txt").write_text(deleg, encoding="utf-8")
roster = sorted(set(re.findall(r"^\s*-\s+([a-z0-9][a-z0-9-]*:[a-z0-9][a-z0-9-]*):", deleg, re.M)))

# strip the status-context reminder block (carries git log text, not agent content)
s_nostatus = re.sub(r'<system-reminder source="hooks-status-context">.*?</system-reminder>', "", s, flags=re.S)

m = {
  "session": sess.rstrip("/").split("/")[-1],
  "head_240": s[:240],
  "len_chars": len(s), "len_bytes": len(s.encode()),
  "off_You_are_Amplifier": s.find("You are Amplifier"),
  "off_Behavioral_Principles": s.find("## Behavioral Principles"),
  "off_Amplifier_Ecosystem_Principles": s.find("## Amplifier Ecosystem Principles"),
  "off_configured_for_development_OF": s.find("configured for development OF"),
  "off_Notify_Bundle": s.find("# Notify Bundle"),
  "system_example_tags_total": s.count("<example>"),
  "system_example_tags_excl_status_context": s_nostatus.count("<example>"),
  "delegate_desc_chars": len(deleg),
  "delegate_desc_example_tags": deleg.count("<example>"),
  "roster": roster, "roster_count": len(roster),
  "p4_delegate_present": "**Delegate complex work**" in s,
  "p3_long_form": "**Verify at every step** -- Run tests" in s,
  "p3_short_form": '**Verify at every step** -- Never claim "done" without proof.' in s,
  "tool_names": sorted((t.get("name") or (t.get("function") or {}).get("name") or "") for t in tools),
}
pathlib.Path(out + ".markers.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
print(json.dumps(m, indent=2))
