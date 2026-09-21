#!/usr/bin/env bash
# Safe extraction from a real session's raw.system.  Never loads the whole
# ~99k-char line into a reader's context: jq slices, offsets and tallies only.
#
# Usage: probe_session.sh <session-dir> <label>
set -euo pipefail

SESSION_DIR="$1"
LABEL="${2:-session}"
EV="$SESSION_DIR/events.jsonl"

echo "=== $LABEL ==="
echo "session_dir: $SESSION_DIR"

# raw.system for Anthropic is .data.raw.system[0].text; OpenAI Responses uses
# .data.raw.instructions.  Take the FIRST llm:request only.
jq -r --arg label "$LABEL" '
  select(.event=="llm:request")
  | (.data.raw.system[0].text // .data.raw.instructions // "")
  | . as $s
  | [
      "raw.system_chars=\($s|length)",
      "head_120=\($s[0:120]|gsub("\n";"\\n"))",
      "context_file_blocks=\($s | [splits("<context_file paths=\"")] | length - 1)",
      "first_block_paths=\(($s | capture("<context_file paths=\"(?<p>[^\"]*)\"")).p)",
      "offset_of_You_are_Amplifier=\($s | (index("You are Amplifier") // -1))",
      "offset_of_dev_marker=\($s | (index("configured for development OF") // -1))",
      "notify_readme_present=\($s | test("# Notify Bundle"))"
    ]
  | .[]
' "$EV" | head -20

# mentions:resolved tallies -- counts only, no payload bodies.
jq -r '
  select(.event=="mentions:resolved")
  | "mentions_resolved: resolutions=\(.data.resolutions|length) failed=\(.data.failed|length) dedup=\(.data.deduplicated_count)"
' "$EV" | head -5

jq -r '
  select(.event=="mentions:resolved")
  | .data.resolutions[0].mention // "none"
  | "first_resolution_mention: \(.)"
' "$EV" | head -1
echo
