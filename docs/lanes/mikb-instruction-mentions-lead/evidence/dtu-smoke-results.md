# DTU smoke — instruction @mentions lead the system-prompt context block

Repo under test: `amplifier-foundation`, branch `lane/mikb-instruction-mentions-lead`
DTU instance: `mikb-foundation-order` (launched 2026-09-06T15:16:01Z)
Profile: `.cache/dtu-smoke/mikb-foundation-order.yaml` (git-ignored)

## Provenance (the DTU really ran THIS code)

`url_rewrites` redirected `github.com/microsoft/amplifier-foundation` to a Gitea
snapshot of the worktree. `amplifier-foundation` carries BOTH the Python package
and the `anchors` / `anchors-amp-dev` bundles, so one rewrite covered both.

| state | installed foundation commit (`direct_url.json`) | `_prepared.py` step order |
|---|---|---|
| AFTER (branch)  | `1accb7581c9442b48ccbefd81f2e2ec8403b8d3f`, later `a3fb4e3f7920e8dd951d5b1d5bfd8d33e9da33ad` | `# 1. Resolve @mentions` (L427) → `# 2. Bundle context files` (L441) |
| BEFORE (origin/main) | `e681764cb4313f83b456e1edd56d9df070e44f26` | `# 1. Bundle context files` (L418) → `# 2. Resolve @mentions` (L428) |

Both snapshots have identical trees to the corresponding host commits; the AFTER
sha changed on restore only because a fresh snapshot commit was made.

## Result — fail-before / pass-after, measured in the SAME DTU

### `anchors`

| metric | BEFORE (`e681764`) | AFTER (branch) |
|---|---|---|
| `raw.system` total chars | **40,732** | **40,732** (identical) |
| `<context_file>` block count | **8** | **8** (identical) |
| FIRST block `paths=` | `modes:context/modes-instructions.md` | **`@anchors:context/system.md`** |
| position of the instruction's mention | **block 8 of 8 (LAST)** | **block 1 of 8 (FIRST)** |
| offset of `"You are Amplifier"` | **16,930** | **190** |
| within first 1,500 chars | **False** | **True** |
| `mentions:resolved` resolutions / failed | 8 / **0** | 8 / **0** |

### `anchors-amp-dev`

| metric | BEFORE (`e681764`) | AFTER (branch) |
|---|---|---|
| `raw.system` total chars | **43,564** | **43,564** (identical) |
| `<context_file>` block count | **9** | **9** (identical) |
| FIRST block `paths=` | `gitea:context/gitea-awareness.md` | **`@anchors-amp-dev:context/system.md`** |
| position of the instruction's mention | **block 9 of 9 (LAST)** | **block 1 of 9 (FIRST)** |
| offset of `"You are Amplifier"` | **19,232** | **214** |
| within first 1,500 chars | **False** | **True** |
| `mentions:resolved` resolutions / failed | 9 / **0** | 9 / **0** |

Identical char counts and block counts on both arms: **content is unchanged,
only emission order moved.** `failed=[]` on every run. `notify README` absent
on every run.

## Runs executed (all exit 0, no startup errors)

| # | bundle | provider / model | cost |
|---|---|---|---|
| provision warm-up | anchors | anthropic claude-sonnet-5 | ~$0.06 (est.) |
| provision warm-up | anchors-amp-dev | anthropic claude-sonnet-5 | ~$0.07 (est.) |
| 1 | anchors | anthropic claude-sonnet-5 | $0.06 |
| 2 | anchors-amp-dev | anthropic claude-sonnet-5 | $0.07 |
| 3 | anchors | openai gpt-5.6-sol | $0.11 |
| 4 | anchors-amp-dev | openai gpt-5.6-sol | $0.11 |
| update warm-up | anchors | anthropic claude-sonnet-5 | ~$0.06 (est.) |
| before-arm | anchors | anthropic claude-sonnet-5 | $0.06 |
| before-arm | anchors-amp-dev | anthropic claude-sonnet-5 | $0.07 |
| restore confirm | anchors-amp-dev | anthropic claude-sonnet-5 | $0.07 |

**Total ≈ $0.74** (7 observed = $0.55, 3 warm-ups estimated at ~$0.19).

Both providers were wired in the DTU (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`OPENAI_BASE_URL` all forwarded via `passthrough`), and both were exercised on
both bundles.

## Final state

DTU left RUNNING and pinned to the branch (AFTER) state; readiness `ready: true`.
Teardown is owned by the calling lane.

```
amplifier-digital-twin destroy mikb-foundation-order
```

Also created on the host: Gitea repo `admin/amplifier-foundation-mikb` on the
pre-existing instance `gitea-55cf7007` (port 10230). The shared
`admin/amplifier-foundation` mirror was deliberately NOT touched.
