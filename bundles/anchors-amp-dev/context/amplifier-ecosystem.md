# Amplifier Ecosystem
This is development OF the Amplifier ecosystem (kernel, modules, bundles, foundation), not general app work. The principles above still govern.
- Respect dependency order: core → foundation → modules → bundles → apps; never push downstream before upstream is merged.
- Validate cross-repo changes together in an isolated DTU; per-repo unit tests alone are insufficient.
- Safe push order: push core-side first, wait for merge and CI, then push module/bundle/app.
- Before packaging recommendations, read `foundation:docs/BUNDLE_GUIDE.md`: behaviors first; Anchors for new supporting runnable roots. Preserve namespace anchors.
- Never read `events.jsonl` directly: lines can exceed 100k tokens and crash the session. Delegate to `context-intelligence:graph-analyst` where available; otherwise `context-intelligence:session-navigator`.
- For a session that will not resume, use Foundation's `python scripts/amplifier-session.py {diagnose,repair,rewind,info,find} <session-dir>`; there is no `amplifier session repair` subcommand. Routine transcript repair runs before each turn and on resume.
