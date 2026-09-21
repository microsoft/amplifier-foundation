# (C) `tool-skills` double mount — compose-semantics finding

**Verdict: NOT A BUG. Nothing is silently dropped.** The double mount is safe, but it is
safe *because of one specific merge rule*, and that dependency was undocumented and
untested until now. This document records the measurement; `tests/test_tool_skills_double_mount.py`
pins it.

Measured on `main` @ `a0decc6`, 2026-09-06. **Measured before anything was changed**, per the
goal's "do NOT fix (C) before measuring it".

## The two mounts

| # | Declared in | What it contributes |
|---|---|---|
| 1 | `behaviors/agents.yaml:36-40` | `config.skills: [amplifier-foundation@main#subdirectory=skills]` — foundation's OWN skills dir |
| 2 | root `bundle.md:33` → `amplifier-bundle-skills@main#subdirectory=behaviors/skills.yaml` | `config.skills: [amplifier-bundle-skills@main#subdirectory=skills]` (curated collection) + `config.visibility` |

Both name the same `module: tool-skills` and, importantly, the **same `source`**
(`amplifier-bundle-skills@main#subdirectory=modules/tool-skills`). The comment in
`agents.yaml` — "Register foundation's own skills directory for discovery" — is accurate: it
is a *config contribution*, not a competing module implementation.

## What `Bundle.compose()` actually does with a duplicate module id

`amplifier_foundation/bundle/_dataclass.py:250` → `merge_module_lists()`
(`amplifier_foundation/dicts/merge.py:79`) keys module configs by `id or module`, so the two
declarations collapse to **one** entry. On collision it calls `deep_merge()`, which resolves
per value type:

| Config value type | Rule | Consequence here |
|---|---|---|
| `dict` | recurse | `visibility` (only mount 2 has it) survives |
| `list` | **concatenate, deduplicated, parent first** | **both** `skills` entries survive |
| scalar | later replaces earlier | `source` — later wins, harmless only because both agree |

The list rule is not incidental. It was introduced by `70d521f` ("fix: concatenate lists in
`deep_merge` instead of replacing — fixes silent config loss in bundle composition", #120),
whose docstring names *this exact pair*:

> This ensures that composing two behaviors that declare the same module
> (e.g. tool-skills) with list-typed config values (e.g. config.skills,
> config.search_paths) accumulates all entries rather than silently dropping
> the parent's list.

So the double mount was already repaired at the merge layer before this lane looked at it.

## Effective composed config (measured)

`behaviors/agents.yaml` composed with the external skills behavior, in root `bundle.md` order
(agents at line 27 → skills.yaml at line 33):

```json
{
  "module": "tool-skills",
  "source": "git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=modules/tool-skills",
  "config": {
    "skills": [
      "git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=skills",
      "git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=skills"
    ],
    "visibility": { "enabled": true, "inject_role": "user",
                    "visibility_token_budget": 5000, "ephemeral": true, "priority": 20 }
  }
}
```

Reverse order yields the same set (order of the list flips; membership does not).

## Real-session confirmation

`amplifier run "hi" --bundle foundation` (foundation as ROOT, via a project-scope source
override — `~/.amplifier/cache` untouched), session `268a6e56-d64d-4958-9ea4-2c2bdfd94a2e`.
Presence in the live `hooks-skills-visibility` list:

```
bundle-to-dot                  present=True   <- behaviors/agents.yaml mount (foundation skills dir)
creating-amplifier-modules     present=True   <- behaviors/agents.yaml mount (foundation skills dir)
per-repo-conventions           present=True   <- behaviors/agents.yaml mount (foundation skills dir)
image-vision                   present=True   <- bundle.md:33 skills.yaml mount (curated collection)
```

Both mounts' skills reach the model. Nothing is dropped end to end.

## Why a test was still warranted

The double mount depends on a merge rule that is invisible at both declaration sites and has
**no failure signal** if it regresses: the skills simply stop being discoverable, with no error.
Disabling list-concat in `deep_merge` (simulating the pre-#120 behavior) makes 2 of the 7 new
tests fail:

```
FAILED tests/test_tool_skills_double_mount.py::test_double_mount_keeps_both_skills_dirs_in_bundle_md_order
FAILED tests/test_tool_skills_double_mount.py::test_double_mount_keeps_both_skills_dirs_in_reverse_order
2 failed, 5 passed
```

Restored: 7 passed.

## Residual risk (not fixed — recorded)

The two mounts are order-insensitive **only because they name the same `source`**. `source` is a
scalar, so if the two declarations ever diverge, whichever composes last silently wins and the
other becomes dead text with no diagnostic. `test_both_mounts_agree_on_the_module_source` pins
that agreement so a future divergence fails a test instead of a session.

No code change was made for (C). Per the goal: "A double mount that merges correctly is a
finding, not a bug."
