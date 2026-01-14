# Progress Monitor Hook

Detects analysis paralysis patterns and injects corrective prompts to keep agents productive.

## Problem It Solves

Agents can get stuck in "analysis paralysis" - endlessly reading files and searching without ever writing code. This hook monitors the read/write ratio and injects warnings when patterns suggest the agent is stuck.

## Detection Patterns

| Pattern | Threshold | Action |
|---------|-----------|--------|
| High read/write ratio | 30 reads with 0 writes | Inject warning to start implementing |
| Repeated file reads | Same file read 3 times | Remind agent they have enough info |
| Continued paralysis | Every 15 additional reads | Escalating warnings |

## Configuration

```yaml
hooks:
  - module: hooks-progress-monitor
    source: foundation:modules/hooks-progress-monitor
    config:
      enabled: true           # Enable/disable (default: true)
      read_threshold: 30      # Reads before first warning (default: 30)
      same_file_threshold: 3  # Same-file reads before warning (default: 3)
      warning_interval: 15    # Reads between repeated warnings (default: 15)
```

## Warning Escalation

The hook escalates urgency with repeated warnings:

1. **First warning** (at read_threshold): "Note: Consider starting implementation..."
2. **Second warning** (+warning_interval reads): "Warning: You should start implementing NOW..."
3. **Third+ warning** (+warning_interval reads): "CRITICAL: STOP READING. Write code IMMEDIATELY..."

## Reset Behavior

Warnings reset when the agent writes a file (write_file or edit_file). This indicates progress is being made.

## Tracked Operations

**Read operations** (increment counter):
- `read_file`
- `grep`
- `glob`
- `web_fetch`
- `web_search`

**Write operations** (reset warning state):
- `write_file`
- `edit_file`
