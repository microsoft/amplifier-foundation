# misaligned-bundle — a fixture that violates all four description checks

This is not a bundle anyone should install. It exists so the checks added in
`validate-bundle-repo.yaml` v3.15.0 and `validate-agents.yaml` v1.8.0 have
something that FAILS, and so the fail-before evidence is reproducible by
anyone rather than quoted from a lane note.

It violates, by construction:

| # | Check | How this fixture violates it |
|---|---|---|
| 1 | Description length cap | `agents/fixture-agent.md` description is >1,200 chars (2x the 600 cap); `skills/fixture-skill/SKILL.md` is >800 (2x the 400 cap) |
| 2 | `<example>` / `<commentary>` | Both the agent AND the skill description carry them — the skill half was unenforced everywhere before v3.15.0 |
| 3 | Awareness redundancy | `context/fixture-awareness.md` restates the agent description (R1) and is when-to-use prose plus a `delegate()` pointer (R2) |
| 4 | Bundle head cost | `bundle.md` pulls all of it into every root prompt, over the 4,000-char WARNING |

`test-fixtures/` is in every scan's `EXCLUDED_DIRS`, so this directory does
not contaminate foundation's own validation run. The tests point `repo_path`
directly AT this directory, which makes it the repo root and the exclusion
inapplicable.
