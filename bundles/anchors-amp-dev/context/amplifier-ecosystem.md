# Amplifier Ecosystem

In this session you are configured for development OF the Amplifier ecosystem
itself -- its kernel, modules, bundles, and foundation. The principles above
govern everything you do; these are what that ecosystem adds.

## Amplifier Ecosystem Principles

**Respect dependency order.** Cross-repo changes sequence bottom-up: core → foundation → modules → bundles → apps. Never push downstream before upstream is merged.

**Prove cross-repo changes in isolation.** A change spanning multiple repos must be validated together in a DTU — not unit-tested in each repo independently. If scope crosses repos, escalate to DTU.

**Safe multi-repo push order.** Push core-side first; wait for merge and CI; then push module/bundle/app. A module pushed before its core dep merges can break downstream consumers.

**Never read `events.jsonl` directly** — one line can exceed 100k tokens and will crash the session. Delegate to `context-intelligence:session-navigator`, which extracts fields safely; where the analysis layer is also composed, `context-intelligence:graph-analyst` is the entry point and falls back to it. A session that will not resume is repaired with the foundation repo's own `python scripts/amplifier-session.py {diagnose,repair,rewind,info,find} <session-dir>` — there is no `amplifier session repair` subcommand, and routine transcript repair already runs automatically before each turn and on resume.
