#!/usr/bin/env bash
# Safe extraction of system-prompt ordering metrics from an Amplifier session's events.jsonl.
# NEVER prints the raw.system body -- only lengths, counts, offsets and short slices.
# Usage: extract.sh <events.jsonl path> <label>
set -uo pipefail
EV="$1"; LABEL="${2:-session}"

echo "### $LABEL"
echo "events_file: $EV"
echo "events_bytes: $(wc -c < "$EV")"

# FIRST llm:request only -> raw system prompt (slice to disk, never to stdout)
jq -r 'select(.event == "llm:request")
       | (.data.raw.system[0].text // .data.raw.system // .data.raw.instructions // empty)' \
   "$EV" > /tmp/_sys_all.txt

if [ ! -s /tmp/_sys_all.txt ]; then
  echo "RESULT: NO llm:request raw.system FOUND"
  jq -r '.event' "$EV" | sort | uniq -c | head -25
  exit 1
fi

python3 - "$LABEL" <<'PY'
import re, sys
s = open('/tmp/_sys_all.txt', encoding='utf-8', errors='replace').read().rstrip('\n')
print(f"raw_system_total_chars: {len(s)}")
print(f"first_120_chars: {s[:120]!r}")
print(f"context_file_block_count: {s.count('<context_file paths=\"')}")
m = re.search(r'<context_file paths="([^"]*)"', s)
print(f"FIRST_context_file_paths: {m.group(1) if m else '<none>'}")
i = s.find('You are Amplifier')
print(f"offset_of_You_are_Amplifier: {i}")
print(f"within_first_1500_chars: {i >= 0 and i < 1500}")
if i >= 0:
    print(f"context_around_offset: {s[max(0,i-70):i+70]!r}")
paths = re.findall(r'<context_file paths="([^"]*)"', s)
print("context_file_paths_in_emission_order:")
for k, p in enumerate(paths, 1):
    print(f"  {k}. {p[:170]}")
print(f"notify_readme_present: {bool(re.search(r'notify[^\"]*README', s, re.I))}")
PY

echo "--- mentions:resolved ---"
jq -c 'select(.event == "mentions:resolved")
       | {resolutions: (.data.resolutions | length),
          failed: (.data.failed | length),
          failed_list: .data.failed}' "$EV"

echo "--- error/failure events ---"
jq -r 'select(.event | test("error|fail"; "i")) | .event' "$EV" | sort | uniq -c
jq -r 'select(.status? and (.status | test("error|fail"; "i"))) | "\(.event) status=\(.status)"' "$EV" | head -10
echo "(none above = no error events)"
echo
