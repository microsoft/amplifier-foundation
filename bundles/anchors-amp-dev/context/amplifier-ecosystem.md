# Amplifier Ecosystem
This session is configured for development OF the Amplifier ecosystem itself -- its kernel, modules, bundles, and foundation. "Development" here means multi-repo Amplifier work, not general-purpose software engineering. The principles above govern everything; these are what the ecosystem adds.
- Respect dependency order: cross-repo changes sequence bottom-up core → foundation → modules → bundles → apps; never push downstream before upstream is merged.
- Prove cross-repo changes in isolation: a change spanning repos must be validated together in a DTU, not unit-tested per repo; if scope crosses repos, escalate to DTU.
- Safe multi-repo push order: push core-side first, wait for merge and CI, then push module/bundle/app.
- Never read `events.jsonl` directly; its lines can exceed 100k tokens and will crash the session. Delegate to `context-intelligence:session-navigator`, or to `context-intelligence:graph-analyst` where the analysis layer is composed -- it falls back to the navigator.
- Repair a session that will not resume with the foundation repo's `python scripts/amplifier-session.py {diagnose,repair,rewind,info,find} <session-dir>`; there is no `amplifier session repair` subcommand, and routine transcript repair already runs before each turn and on resume.
