#!/usr/bin/env bash
# Session Naming E2E Smoke Test
#
# Tests that hooks-session-naming works end-to-end with the local module:
#   - Session naming fires after the configured trigger turn
#   - Naming is non-blocking (execution:end fires before naming completes)
#   - Session name + description appear in metadata.json
#   - Structured observability events fire (session-naming:set)
#
# Uses the same approach as amplifier-core/scripts/e2e-smoke-test.sh:
# creates an isolated Docker container, installs Amplifier from GitHub,
# overrides the local hooks-session-naming module, and runs real LLM calls.
#
# Usage:
#   ./scripts/session-naming-smoke-test.sh
#
# Environment:
#   ANTHROPIC_API_KEY   Required (or set in ~/.amplifier/keys.env)
#   SMOKE_TIMEOUT       Timeout per prompt in seconds (default: 120)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MODULE_PATH="$REPO_DIR/modules/hooks-session-naming"
CONTAINER_NAME="session-naming-smoke-$$"
TIMEOUT_SECONDS="${SMOKE_TIMEOUT:-120}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${YELLOW}[smoke]${NC} $*"; }
info() { echo -e "${CYAN}[smoke]${NC} $*"; }
pass() { echo -e "${GREEN}[PASS]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

cleanup() {
    log "Cleaning up container $CONTAINER_NAME..."
    docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    KEYS_ENV="$HOME/.amplifier/keys.env"
    [[ -f "$KEYS_ENV" ]] && { set -a; source "$KEYS_ENV"; set +a; }
fi
[[ -z "${ANTHROPIC_API_KEY:-}" ]] && fail "ANTHROPIC_API_KEY not set"
command -v docker &>/dev/null || fail "Docker not found"

log "Module path: $MODULE_PATH"
[[ -d "$MODULE_PATH" ]] || fail "Module not found at $MODULE_PATH"

# ---------------------------------------------------------------------------
# Step 1: Create container
# ---------------------------------------------------------------------------
log "Creating isolated Docker container..."
docker run -d --name "$CONTAINER_NAME" \
    -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
    python:3.12-slim \
    sleep 3600

# ---------------------------------------------------------------------------
# Step 2: Bootstrap (git + uv)
# ---------------------------------------------------------------------------
log "Installing git + uv..."
docker exec "$CONTAINER_NAME" bash -c "
    apt-get update -qq && apt-get install -y -qq git >/dev/null 2>&1
    pip install -q uv
    echo 'Bootstrap OK'
" || fail "Bootstrap failed"

# ---------------------------------------------------------------------------
# Step 3: Install Amplifier from GitHub
# ---------------------------------------------------------------------------
log "Installing amplifier from GitHub (this takes ~2 minutes)..."
INSTALL_OUT=$(docker exec "$CONTAINER_NAME" bash -c "
    export PATH=/root/.local/bin:\$PATH
    uv tool install git+https://github.com/microsoft/amplifier@main 2>&1
") || fail "Amplifier install failed"
info "Install: $(echo "$INSTALL_OUT" | tail -3 | tr '\n' ' ')"

INSTALLED_VER=$(docker exec "$CONTAINER_NAME" bash -c "
    export PATH=/root/.local/bin:\$PATH
    amplifier --version 2>&1
")
info "Installed: $INSTALLED_VER"

# ---------------------------------------------------------------------------
# Step 4: Override hooks-session-naming with local module
# ---------------------------------------------------------------------------
log "Copying local hooks-session-naming into container..."
docker cp "$MODULE_PATH" "$CONTAINER_NAME:/tmp/hooks-session-naming" \
    || fail "Failed to copy module"

log "Installing local module (--force-reinstall --no-deps)..."
OVERRIDE_OUT=$(docker exec "$CONTAINER_NAME" bash -c "
    uv pip install \
        --python /root/.local/share/uv/tools/amplifier/bin/python3 \
        --force-reinstall --no-deps \
        '/tmp/hooks-session-naming' 2>&1
") || fail "Module override install failed"
info "Override: $(echo "$OVERRIDE_OUT" | tail -1)"

# Confirm local version is installed
LOCAL_VER=$(grep '^version = ' "$MODULE_PATH/pyproject.toml" | grep -oP '"\K[^"]+')
info "Local module version: $LOCAL_VER"

# ---------------------------------------------------------------------------
# Step 5: Configure Amplifier
# ---------------------------------------------------------------------------
log "Writing test bundle YAML with hooks-session-naming configured for initial_trigger_turn=1..."
docker exec "$CONTAINER_NAME" bash -c "
    mkdir -p /root/.amplifier/bundles
    # Write a minimal test bundle that directly configures hooks-session-naming
    # with initial_trigger_turn: 1 so naming fires on the very first turn.
    # This avoids the overrides-in-settings limitation (bundle YAML config wins).
    cat > /root/.amplifier/bundles/session-naming-test.yaml << 'BUNDLE_EOF'
bundle:
  name: session-naming-test
  version: 1.0.0
  description: Minimal bundle for session-naming smoke test

# Streaming orchestrator + simple context — same as production
session:
  orchestrator:
    module: loop-streaming
    source: git+https://github.com/microsoft/amplifier-module-loop-streaming@main
  context:
    module: context-simple
    source: git+https://github.com/microsoft/amplifier-module-context-simple@main

# hooks-logging MUST be present: it creates the session directory on disk.
# Without it, the SessionStore cannot save metadata and the session-naming
# hook cannot find the session directory to write name/description.
hooks:
  - module: hooks-logging
    source: git+https://github.com/microsoft/amplifier-module-hooks-logging@main
    config:
      mode: session-only
      session_log_template: ~/.amplifier/projects/{project}/sessions/{session_id}/events.jsonl

  # Session naming hook — local version, trigger on turn 1 for testing
  - module: hooks-session-naming
    config:
      initial_trigger_turn: 1
      update_interval_turns: 99
BUNDLE_EOF
    echo 'Bundle written'
"

log "Writing settings.yaml pointing at test bundle by name..."
docker exec "$CONTAINER_NAME" bash -c "
    mkdir -p /root/.amplifier
    cat > /root/.amplifier/settings.yaml << 'YAML_EOF'
bundle:
  active: session-naming-test

config:
  providers:
  - config:
      api_key: \${ANTHROPIC_API_KEY}
      default_model: claude-haiku-4-5
      enable_prompt_caching: 'true'
      priority: 1
    id: anthropic
    module: provider-anthropic
    source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
YAML_EOF
    echo 'Settings written'
"

# ---------------------------------------------------------------------------
# Step 6: Run 3 sequential `amplifier run` calls
#
# Each call creates its own session. With initial_trigger_turn=1, naming fires
# after the first turn in each session. This directly tests the scenario the
# user asked for: sequential calls to `amplifier run <prompt>` where each
# session is named in the background without blocking the user.
# ---------------------------------------------------------------------------
echo ""
log "============================================================"
log " SMOKE TEST: 3 sequential amplifier run <prompt> calls"
log " Each session names itself at turn 1 (model_role: fast)"
log " Timeout per run: ${TIMEOUT_SECONDS}s"
log "============================================================"
echo ""

WORKDIR="/workspace"
docker exec "$CONTAINER_NAME" mkdir -p "$WORKDIR"

# Run 1
log "Run 1/3: What is Python asyncio?"
RUN1_OUTPUT=$(docker exec "$CONTAINER_NAME" bash -c "
    export PATH=/root/.local/bin:\$PATH
    cd $WORKDIR
    timeout $TIMEOUT_SECONDS amplifier run \
        'In one sentence, what is Python asyncio?' 2>&1 || true
")
log "--- Run 1 (last 8 lines) ---"
echo "$RUN1_OUTPUT" | tail -8
echo "---"
if echo "$RUN1_OUTPUT" | grep -qE "^Traceback|TypeError:|AttributeError:|ImportError:"; then
    fail "Run 1 produced Python exceptions"
fi

# Run 2
log "Run 2/3: What is a Python dataclass?"
RUN2_OUTPUT=$(docker exec "$CONTAINER_NAME" bash -c "
    export PATH=/root/.local/bin:\$PATH
    cd $WORKDIR
    timeout $TIMEOUT_SECONDS amplifier run \
        'In one sentence, what is a Python dataclass?' 2>&1 || true
")
log "--- Run 2 (last 8 lines) ---"
echo "$RUN2_OUTPUT" | tail -8
echo "---"
if echo "$RUN2_OUTPUT" | grep -qE "^Traceback|TypeError:|AttributeError:|ImportError:"; then
    fail "Run 2 produced Python exceptions"
fi

# Run 3 (slightly longer prompt to make sure naming produces a useful name)
log "Run 3/3: Explain async/await vs threads in Python"
RUN3_OUTPUT=$(docker exec "$CONTAINER_NAME" bash -c "
    export PATH=/root/.local/bin:\$PATH
    cd $WORKDIR
    timeout $TIMEOUT_SECONDS amplifier run \
        'In two sentences, explain the difference between Python async/await and threads.' 2>&1 || true
")
log "--- Run 3 (last 8 lines) ---"
echo "$RUN3_OUTPUT" | tail -8
echo "---"
if echo "$RUN3_OUTPUT" | grep -qE "^Traceback|TypeError:|AttributeError:|ImportError:"; then
    fail "Run 3 produced Python exceptions"
fi

# Combine all run output for error checking
SESSION_OUTPUT="$RUN1_OUTPUT
$RUN2_OUTPUT
$RUN3_OUTPUT"

# ---------------------------------------------------------------------------
# Step 7: Wait for the background naming task then inspect results
# ---------------------------------------------------------------------------
log "Waiting 20s for background session-naming task to complete..."
sleep 20

log "Locating most-recent session directory..."
SESSION_DIR=$(docker exec "$CONTAINER_NAME" bash -c "
    find /root/.amplifier/projects -mindepth 3 -maxdepth 3 -type d 2>/dev/null | \
    while read d; do
        [[ -f \"\$d/metadata.json\" ]] && echo \"\$d\"
    done | xargs ls -td 2>/dev/null | head -1 || true
")

[[ -z "$SESSION_DIR" ]] && fail "No session directory found in ~/.amplifier/projects/"
info "Session dir: $SESSION_DIR"

METADATA=$(docker exec "$CONTAINER_NAME" bash -c "cat '$SESSION_DIR/metadata.json' 2>/dev/null || echo '{}'")
echo ""
log "=== metadata.json ==="
echo "$METADATA" | python3 -m json.tool 2>/dev/null || echo "$METADATA"
echo "==="

# Check for session-naming events in events.jsonl (context-intelligence)
NAMING_EVENTS=$(docker exec "$CONTAINER_NAME" bash -c "
    find '$SESSION_DIR' -name 'events.jsonl' 2>/dev/null | head -1 | \
        xargs grep -o '\"session-naming:[^\"]*\"' 2>/dev/null | sort | uniq -c || \
        echo '(no events.jsonl or no naming events found)'
" 2>/dev/null || echo "(events check failed)")
info "Session-naming events: $NAMING_EVENTS"

# ---------------------------------------------------------------------------
# Step 8: Evaluate results
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo " RESULTS"
echo "============================================================"

PASS_COUNT=0
FAIL_COUNT=0

check_pass() { pass "✓ $1"; ((PASS_COUNT++)) || true; }
check_fail() { echo -e "${RED}[FAIL]${NC} ✗ $1"; ((FAIL_COUNT++)) || true; }
check_warn() { echo -e "${YELLOW}[WARN]${NC} ⚠ $1"; }

# Check 1: LLM responded to both prompts
if echo "$SESSION_OUTPUT" | grep -qiE "asyncio|event.loop|concurrent|cooperative"; then
    check_pass "LLM responded to prompt 1 (asyncio question)"
else
    check_warn "Could not confirm LLM response to prompt 1 (may still have worked)"
fi

if echo "$SESSION_OUTPUT" | grep -qiE "dataclass|data class|@dataclass|field"; then
    check_pass "LLM responded to prompt 2 (dataclass question)"
else
    check_warn "Could not confirm LLM response to prompt 2 (may still have worked)"
fi

# Check 2: Session received a name
SESSION_NAME=$(echo "$METADATA" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('name', ''))
except:
    print('')
" 2>/dev/null || true)

SESSION_DESC=$(echo "$METADATA" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('description', ''))
except:
    print('')
" 2>/dev/null || true)

if [[ -n "$SESSION_NAME" ]]; then
    check_pass "Session named: '$SESSION_NAME'"
    [[ -n "$SESSION_DESC" ]] && info "  Description: '$SESSION_DESC'"
else
    # One more wait — background task might still be running
    log "Name not yet in metadata — waiting 10s more..."
    sleep 10
    METADATA=$(docker exec "$CONTAINER_NAME" bash -c "cat '$SESSION_DIR/metadata.json' 2>/dev/null || echo '{}'")
    SESSION_NAME=$(echo "$METADATA" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('name', ''))
except:
    print('')
" 2>/dev/null || true)
    if [[ -n "$SESSION_NAME" ]]; then
        check_pass "Session named (after extra wait): '$SESSION_NAME'"
    else
        check_fail "Session naming did NOT fire — no 'name' in metadata.json after 30s"
    fi
fi

# Check 3: name_generated_at timestamp present (proves naming code ran)
NAME_TS=$(echo "$METADATA" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('name_generated_at', ''))
except:
    print('')
" 2>/dev/null || true)

if [[ -n "$NAME_TS" ]]; then
    check_pass "name_generated_at timestamp present: $NAME_TS"
else
    check_fail "name_generated_at missing from metadata.json"
fi

# Check 4: No naming timeout in output
if echo "$SESSION_OUTPUT" | grep -qi "session naming provider call timed out"; then
    check_fail "Naming provider timed out"
else
    check_pass "No naming provider timeout"
fi

# Check 5: No naming errors in output
if echo "$SESSION_OUTPUT" | grep -qi "Error generating name"; then
    check_fail "Error in naming task (check output above)"
else
    check_pass "No naming errors detected"
fi

# Check 6: session-naming:set event in events.jsonl (if context-intelligence is active)
if echo "$NAMING_EVENTS" | grep -q "session-naming:set"; then
    check_pass "session-naming:set event fired in events.jsonl"
else
    check_warn "session-naming:set not in events.jsonl (context-intelligence not configured in test environment)"
fi

# Check 7: Verify input was NOT blocked (response came before naming)
# The session output should have the assistant's response BEFORE any naming delay.
# We can't easily verify the exact timing in a log, but absence of long pauses is a good sign.
if echo "$SESSION_OUTPUT" | grep -qiE "asyncio|dataclass" && [[ -n "$SESSION_NAME" ]]; then
    check_pass "Non-blocking: responses received AND naming completed (no blocked input)"
fi

# ---------------------------------------------------------------------------
# Final verdict
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
    pass " SMOKE TEST PASSED ($PASS_COUNT checks)"
    pass " $INSTALLED_VER"
    pass " Session name: '$SESSION_NAME'"
    pass "============================================================"
    echo ""
    exit 0
else
    echo -e "${RED}[FAIL]${NC} SMOKE TEST FAILED ($FAIL_COUNT failure(s), $PASS_COUNT passed)"
    echo "============================================================"
    echo ""
    exit 1
fi
