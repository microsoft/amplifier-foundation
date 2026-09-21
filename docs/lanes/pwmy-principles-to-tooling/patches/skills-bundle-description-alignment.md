# Patch: bring the skills bundle's creation skills into line with the description rules

**Target repo:** `microsoft/amplifier-bundle-skills`
**Why this is an artifact and not a commit:** that repo is held by lane
`smy5-patch-skills` for the duration of this batch, and GOAL.md forbids this
lane from editing another repo. Per the goal's own defect clause, the patch
ships here, drop-in.

**Measured state of that repo, 2026-09-07** (`validate-bundle-repo.yaml`
v3.15.0, `skill-description-validation`): n=31 skills, mean 413 chars, median
312, max 928 — **13 over the 400-char cap, 4 over the 800-char ERROR line.**

That distribution is not an accident. It is what `personafy` currently
*instructs*: "a `description:` field at or below ~700–800 characters". The
creation skill is emitting the defect by design, which is precisely the
failure this work item exists to close — the rules were a convention, and a
convention drifts.

---

## Patch 1 — `skills/personafy/SKILL.md`, step 7

The success criterion names a cap that is nearly 2x the enforced one, and the
surrounding paragraph describes the 1024-char *spec* ceiling as the operative
limit. Both need to point at the enforced number.

### Before

```markdown
**Check the frontmatter `description:` length, not just the body.** The Agent Skills spec
recommends a 1024-character ceiling on `description`, and Amplifier's `tool-skills` module logs
a warning past it (soft — it does not block loading or truncate anything — but every visible
skill's full `description` is injected into the model's context on every turn, so an over-long
one is a small, permanently-recurring token cost, not a one-time nuisance). Measure it (e.g.
`python3 -c "import yaml; print(len(yaml.safe_load(open('SKILL.md').read().split('---')[1])['description']))"`)
and if it's near or past 1024, compress — do not relocate the trimmed content into the body,
since the visibility hook only ever shows `description`, never the body.
```

### After

```markdown
**Check the frontmatter `description:` length, not just the body.** The enforced cap is
**400 characters, ERROR at 800** — `foundation:recipes/validate-bundle-repo.yaml` Phase 2.82,
per `foundation:context/shared/description-authoring-principles.md` V5. (The Agent Skills
spec's 1024-char ceiling is a much looser upper bound and is NOT the operative limit; the
`tool-skills` warning past 1024 is soft and fires long after the real budget is blown.)
Every visible skill's full `description` is injected on every turn, so an over-long one is a
permanently-recurring cost, not a one-time nuisance. Measure it (e.g.
`python3 -c "import yaml; print(len(yaml.safe_load(open('SKILL.md').read().split('---')[1])['description']))"`)
and if it is past 400, compress — do not relocate the trimmed content into the body,
since the visibility hook only ever shows `description`, never the body.
```

### Before

```markdown
**Success criteria:** A leaner SKILL.md with the same steering power, verified, and a
`description:` field at or below ~700–800 characters (matching sibling norms like
`crusty-old-engineer`/`cranky-old-sam`) — comfortably under the 1024 ceiling, not just barely under it.
```

### After

```markdown
**Success criteria:** A leaner SKILL.md with the same steering power, verified, and a
`description:` field **at or below 400 characters**. Do not calibrate against sibling
norms — measured 2026-09-07, 13 of this bundle's 31 skills are over that cap and 4 are
over the 800-char ERROR line, so the siblings are the drift, not the standard. If a
routing fact genuinely will not fit, keep the fact, exceed the cap, and say in the PR
which fact forced it: fidelity beats brevity (V7).
```

---

## Patch 2 — `skills/skillify/SKILL.md`, step 4 template

The emitted template's own guidance sets no cap, permits arbitrary length
("can be longer overall"), and says nothing about the shape or the example
policy. A skill created from it starts non-compliant.

### Before

```markdown
    ---
    name: {{skill-name}}
    description: >
      {{What this skill does. Front-load the key use case. Include trigger
      phrases and "Use when..." guidance — this is what the model sees in the
      skills visibility list to decide whether to auto-invoke.
      Keep under 250 characters for the first sentence; can be longer overall.}}
```

### After

```markdown
    ---
    name: {{skill-name}}
    description: >
      {{TRIGGER FIRST: the condition under which this applies, then what it
      does. Then "USE WHEN <deciding factor>." Then "DO NOT USE WHEN <the
      case that belongs elsewhere> — use <name>." This is what the model sees
      in the skills visibility list, on EVERY request of every session that
      can see the skill, invoked or not.
      HARD CAP 400 characters (ERROR at 800) — enforced by
      foundation:recipes/validate-bundle-repo.yaml Phase 2.82.
      ZERO <example> blocks and zero <commentary> tags: a trigger belongs in
      the USE WHEN clause as a decision rule, not as a worked dialogue.}}
```

Add immediately after the template block:

```markdown
Before you hand the skill back, measure the description you just wrote:

    python3 -c "import yaml,sys; d=yaml.safe_load(open('SKILL.md').read().split('---')[1])['description']; print(len(d), 'chars'); sys.exit(len(d) > 400)"

If it exceeds 400, cut advocacy first (prose that sells the skill rather than
routing to it), then compress the capability sentence. Never cut a trigger or
a DO-NOT-USE boundary to make the number — a description that got shorter by
dropping a routing fact is a mis-routing waiting to happen, and it surfaces
later as "it didn't use the right thing" with nothing pointing back here.
```

---

## Patch 3 — `skills/councilify/SKILL.md`

`councilify` builds whole panels of persona skills, so a single wrong default
multiplies by the number of lenses it emits. It delegates authoring to
`personafy`, which Patch 1 fixes — but it should state the gate it is
delivering against, because it is the step that decides "done".

Add to the step that verifies the built council (the one that confirms each
lens "via its own declared skill description, surfaced live in-session"):

```markdown
**Every lens description this council emits must pass the validators
unmodified.** Run, from the target repo:

    amplifier tool invoke recipes operation=execute \
      recipe_path=foundation:recipes/validate-bundle-repo.yaml \
      context='{"repo_path": "."}'

and read the Skill Description Validation section: zero `skill_description_excessive`,
zero `example_block_present`. A council of N lenses multiplies any description
defect by N, and every one of those descriptions is paid on every request of
every session that can see the skills.
```

---

## Patch 4 — `skills/skills-assist/authoring-guide.md`

The frontmatter reference row for `description` is the single most-read line
in the guide and carries no budget and no shape.

### Before

```markdown
| `description` | string | — | **Required.** What the skill does and when to invoke it. Used in system prompt context. Write for the agent reading it, not a human menu. |
```

### After

```markdown
| `description` | string | — | **Required.** What the skill does and when to invoke it. Rendered into the skills-visibility block on EVERY request of every session that can see the skill, invoked or not. **Cap: 400 chars, ERROR at 800.** Shape: trigger first, then USE WHEN, then DO NOT USE WHEN naming the alternative. Zero `<example>`/`<commentary>`. Canonical rules: `foundation:context/shared/description-authoring-principles.md`; enforced by `foundation:recipes/validate-bundle-repo.yaml` Phase 2.82. Write for the agent reading it, not a human menu. |
```

Also update the "Minimal Example" so the first thing a reader copies is
compliant:

### Before

```yaml
---
name: my-skill
description: "Brief, specific description of what this skill does and when to invoke it."
---
```

### After

```yaml
---
name: my-skill
description: "<Trigger: the condition under which this applies> — <what it does>. USE WHEN <deciding factor>. DO NOT USE WHEN <case that belongs elsewhere> — use <name>."
---
```

---

## How to verify after applying

From the skills bundle repo root:

```bash
amplifier tool invoke recipes operation=execute \
  recipe_path=foundation:recipes/refresh-descriptions.yaml \
  context='{"repo_path": "."}'
```

That produces `violations.json`, `proposals.md` (with a fidelity table per
rewrite), `verdict.json` and `REPORT.md` under `.description-refresh/`, and
changes nothing in the repo. The 13 over-cap and 4 over-ERROR descriptions
measured above are what it will find on the first run.
