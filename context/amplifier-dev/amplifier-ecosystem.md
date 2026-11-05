# Amplifier Ecosystem
This is development OF the Amplifier ecosystem (kernel, modules, bundles, foundation), not general app work. Host instructions govern.
- Merge/release in dependency order: core → foundation → modules → bundles → apps. Dependent branches and draft PRs may be pushed for parallel review; link upstream PRs and state the dependency hold.
- Validate cross-repo changes in an isolated DTU; unit tests alone are insufficient. Label candidate-graph evidence separately. Before downstream merge/release, verify affected integration against accepted upstream revisions. Early review does not waive checks, independent approvals or protections.
- Before packaging, read `foundation:docs/BUNDLE_GUIDE.md`: behaviors first; preserve the chosen host and root manifests that anchor repo namespaces.
- Never read `events.jsonl` directly: lines can exceed 100k tokens. Use available Context Intelligence agents or a session-navigation capability.
- For a session that will not resume, use Foundation's `python scripts/amplifier-session.py {diagnose,repair,rewind,info,find} <session-dir>`; there is no `amplifier session repair` subcommand. Routine transcript repair runs before each turn and on resume.
