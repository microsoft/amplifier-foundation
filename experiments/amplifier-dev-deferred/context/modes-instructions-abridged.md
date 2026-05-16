# Modes (this bundle)

Modes are runtime overlays that change tool policy and inject guidance.

**Commands** (`mode` tool, also user slash-commands):
- `mode(operation="set", name="amplifier-dev")` -- activate a mode. The
  user can also type `/<mode-name>` (e.g. `/amplifier-dev`).
- `mode(operation="clear")` -- deactivate the current mode.
- `mode(operation="list")` -- list available modes.

Each mode declares a tool policy: tools fall into `safe` (free), `warn`
(blocked first call, retry to proceed), `confirm` (user approval), or
`block`. Unlisted tools fall under the mode's `default_action`.

When a mode is active you'll see `<system-reminder source="mode-<name>">`
in context -- follow that guidance until the mode is cleared.
