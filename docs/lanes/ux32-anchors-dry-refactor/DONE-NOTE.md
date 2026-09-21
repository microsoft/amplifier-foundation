# DONE-NOTE — `model_performance-ux32`

**Lane:** `ux32-anchors-dry-refactor` · **Branch:** `lane/ux32-anchors-dry-refactor`
**Draft PR:** [microsoft/amplifier-foundation#360](https://github.com/microsoft/amplifier-foundation/pull/360)
**Outcome:** **branch A — RESOLVED.** Every deliverable is DONE. Nothing was
recorded NOT-POSSIBLE; the cap did not bind.
**Spend:** **$0.62 of the $4.00 authority** (arithmetic and per-purchase detail
in §7). Residue **$3.38**, unspent because the work finished, not because it
was unaffordable.

---

## 1. The namespace-resolution check — the one that decides the shape

**Question.** Does the `foundation:` namespace resolve inside an
`anchors-amp-dev` session that does **not** include foundation? If not, the
canonical docs cannot live at the repo root and the fallback applies.

**Answer: yes. Preferred path taken.** The three ecosystem docs live at the repo
root's `context/amplifier-dev/`, and `amplifier-dev-expert` reaches them as
`@foundation:context/amplifier-dev/*`.

**Mechanism, read from the shipped code and then confirmed at runtime.**
`amplifier_foundation/registry.py:440-476`: when a bundle is loaded from a
`#subdirectory=` URI (or a path nested in a repo), the registry walks up from the
bundle file's grandparent to `_find_nearest_bundle_file()`, finds the enclosing
root `bundle.md`, and registers
`bundle.source_base_paths[<root bundle name>] = resolved.source_root`.

It is **filesystem nesting**, *not* the app-CLI `WELL_KNOWN_BUNDLES` table. That
table maps names to URIs for discovery and `bundle use`; it is never consulted
for `@mention` resolution — `amplifier_foundation/mentions/resolver.py:55-57`
looks only at the loaded-bundle map. Naming that distinction was the point of
checking rather than assuming: the convenient answer and the true mechanism were
different things.

**Evidence** (`evidence/resolve-ampdev-{main,worktree}.json`) — from the cache on
`main` and from this branch via a source override, both:

```
foundation                                             -> <foundation checkout root>
@foundation:context/shared/common-agent-base.md        -> resolves
@foundation:context/amplifier-dev/ecosystem-map.md     -> resolves
@foundation:context/amplifier-dev/dev-workflows.md     -> resolves
@foundation:context/amplifier-dev/testing-patterns.md  -> resolves
```

Also observed and worth recording: on `main`, the `anchors` namespace is **not**
registered in an `anchors-amp-dev` session — the include added here is what
registers it, so `@anchors:context/system.md` in the new body could not have
worked before this change.

**Caveat, stated rather than buried.** This works because `anchors-amp-dev` is
physically nested inside the amplifier-foundation repo. Lift the bundle out and
it breaks. That was already true before this PR — the agent's pre-existing
`@foundation:…common-agent-base.md` mention relied on the identical mechanism —
so the refactor introduces no new class of dependency. It does, however, mean the
anchors README's "self-contained by design" claim is about *module source URLs*,
not about namespaces.

**Confirmed end-to-end**, not just structurally: a real delegation to
`amplifier-dev-expert` produced a sub-agent prompt whose three resolved context
files are exactly those root paths, carrying `# Amplifier Ecosystem Map`,
`# Amplifier Development Workflows`, `# Amplifier Testing Patterns`, with
`common-agent-base` **absent** and the emoji commit footer **absent**
(`evidence/after-ampdev-subagent.markers.json`).

**Status: DONE.**

---

## 2. The five guard tests — 5 FAIL on main, 5 PASS on the branch

`tests/test_anchors_bundles_dry.py`. Pure filesystem + YAML; no shell, no
`stat -c`, nothing platform-dependent, because CI runs this suite on
`windows-latest` as well as ubuntu. (The first draft compared paths with
`split("/")`, which would have silently passed on Windows where `rglob` yields
backslashes; fixed in `7b3907e` to compare on `Path.parts`.)

Full output: `evidence/guard-tests-FAIL-BEFORE.txt` (taken with `bundles/` and
`context/amplifier-dev/` checked out of `origin/main`) and
`evidence/guard-tests-PASS-AFTER.txt`.

| # | guard | fail-before, quoted |
|---|---|---|
| a | `anchors-amp-dev` declares no `session:`/`tools:`/`hooks:` | `re-declares ['session', 'tools', 'hooks']` |
| b | no same-path file under both bundles except `bundle.md`/`README.md` | **7** offenders: `agents/{architect,builder,debugger,explorer,git-ops,researcher}.md` + `context/system.md` |
| c | no `<example>`/`<commentary>` in **any** agent description | **7** offenders, all under `bundles/anchors-amp-dev/agents/` |
| d | exactly one *shipped* copy of each ecosystem doc | `found 3` for each: root, bundle copy, and `experiments/behavioral-anchor-amplifier-dev/` |
| e | `anchors-amp-dev` includes anchors by **full URL** | 7 includes listed, none of them anchors |

**Guard (c) is a new check, not an extension of an existing one.** The spec said
"extend #341's no-`<example>` check". There is no such check to extend: #341
(`1b16a7d`) was a manual sweep of 16 root agents, and the only enforcement in the
repo is `recipes/validate-agents.yaml` — an LLM-driven recipe that CI does not
run. Guard (c) is therefore the first deterministic enforcement of the #340
policy, and it covers root `agents/` *and* `bundles/**/agents/*.md`.

**One deviation, named.** Guard (d) excludes `experiments/`, which holds the
frozen pre-promotion originals (`anchors` was promoted out of
`experiments/behavioral-anchor`). Those are not registered bundles and are never
composed, so they cannot drift into a session — but they *are* a third copy, and
the test prints them in its own failure message rather than pretending otherwise.
Deleting them is outside this lane's owned paths and is a separate call for the
owner.

**Status: DONE.**

---

## 3. Mount-plan diff, before vs after — matches the stated deltas exactly

Dumped by `evidence/mountplan_dump.py`; artifacts
`evidence/{before,after}-anchors{,-amp-dev}.json`.

| bundle | delta |
|---|---|
| **anchors** | **empty** apart from `version: 0.1.0 → 0.2.0` — which the spec itself mandates. `session`, `tools`, `hooks`, `agents`, `instruction` all byte-identical, ordering included. |
| **anchors-amp-dev** | `agents`: **−`foundation:session-analyst`**, plus the six-agent namespace rename `anchors-amp-dev:* → anchors:*`. Plus the intended `instruction`, `description`, `version`. `session`, `tools`, `hooks` **byte-identical**, ordering included. |

**No other delta appeared** — but reaching that took one deliberate decision,
recorded here because it is the kind of thing that otherwise looks like an
unexplained change later.

With the include order the work item wrote (`anchors` first, then
`amplifier-tester`), the module **set** and every module's config stayed
identical, but two list *orders* moved: the merged `tools:` list
(`tool-skills`/`tool-delegate` swapped) and `tool-skills.config.skills` (same
five URIs, rotated). Include order is what fixes both. Listing
`amplifier-tester` **first** reproduces the shipped mount plan byte-for-byte, so
the DRY refactor moves no wire bytes at all; it also leaves anchors last, so
anchors' values win any scalar conflict, which is what "anchors is the base"
ought to mean. `bundle.md` carries that reasoning in a comment at the include
list. Probe artifact: the swapped-order run is what
`evidence/after-anchors-amp-dev.json` records.

**Status: DONE.**

---

## 4. Real sessions on this host (source override; `~/.amplifier/cache` untouched)

Override placed in `<worktree>/.amplifier/settings.local.yaml` — **project
scope, gitignored** (`*.local.*`), so it never touched the shared global
settings other lanes are using on this host. Removed after the runs.

**anchors** — head `@anchors:context/system.md`; `You are Amplifier` at byte
**222**; three principles (`p3_short_form: true`, `p4_delegate_present: false`);
zero `<example>` in any agent description; bundle roster exactly the six anchors
agents.

**anchors-amp-dev** — head is anchors' `system.md` **then**
`amplifier-ecosystem.md`, in body order (#359 made body order load-bearing):

| marker | byte offset |
|---|---|
| `You are Amplifier` | **271** |
| `## Behavioral Principles` | **359** |
| `configured for development OF` | **1865** |
| `## Amplifier Ecosystem Principles` | **2059** |

Roster: the six anchors agents + `amplifier-dev-expert`, and **no
`foundation:session-analyst`**. The delegate tool's agent catalog falls
**77,622 → 75,328 chars** and **99 → 92 `<example>` blocks** — exactly the seven
removed here (the other 92 come from app bundles installed globally on this host
and are constant across both arms).

`# Notify Bundle` appears in none of the four captures: the `f26u`
instruction-replacement bug does not reach these runs post-`2ef5e12`.

**Root-prompt size ≤ before — reported on both readings, because they disagree.**

| bundle | per-turn head (`raw.system` + `raw.tools`) | `raw.system` alone |
|---|---|---|
| anchors | 320,540 → **320,186** (−354) | 96,548 → **96,194** (−354) |
| anchors-amp-dev | 323,869 → **322,341** (−1,528) | 97,102 → **97,914** (**+812**) |

`anchors-amp-dev`'s `raw.system` **grows by 812 chars**: two layered files cost
more than one merged file, and the `events.jsonl` guidance now carries real
routing rather than a bare warning. The tools schema falls 2,340, so the per-turn
total — what is actually sent every turn — is down. Both numbers are given rather
than the flattering one. The `hooks-status-context` block is excluded from both
because a dirty working tree inflates it by ~1.1 kB and would otherwise be read
as a bundle regression.

**Status: DONE.**

---

## 5. Delegation to `amplifier-dev-expert` — the CANON decision at runtime

Sub-session `…_anchors-amp-dev-amplifier-dev-expert`, 44,169-char prompt.
Resolved context files, verbatim from the capture:

```
@foundation:context/amplifier-dev/ecosystem-map.md     -> <repo>/context/amplifier-dev/ecosystem-map.md
@foundation:context/amplifier-dev/dev-workflows.md     -> <repo>/context/amplifier-dev/dev-workflows.md
@foundation:context/amplifier-dev/testing-patterns.md  -> <repo>/context/amplifier-dev/testing-patterns.md
```

`mentions_common_agent_base: false` · `emoji_commit_footer_present: false`. The
agent, asked to list the H1 of every context doc in its prompt, returned exactly
the three. **Status: DONE.**

---

## 6. DTU smoke — both bundles, both providers, branch pinned

One container (`ux32-bundle-smoke`), both bundles installed from the **public
origin branch** — `git+https://github.com/microsoft/amplifier-foundation@lane/ux32-anchors-dry-refactor#subdirectory=…`
— so the fetch path exercised is the production one (git+https + `#subdirectory=`
into a content-hashed cache), not a directory on disk. Profile committed at
`dtu-smoke.yaml`; markers at `evidence/dtu/*.markers.json`.

One container rather than two: the deliverable is bundle coverage, not container
count, and both bundles install into the same container. No routing matrix was
mounted — the standard eval container's `routing: matrix: openai` routes delegate
sub-work to sol and would have turned a $0.09 smoke into a multi-dollar one.

| arm | result |
|---|---|
| `anchors` × haiku | 3 principles ✓, roster = 6 anchors agents (+`session-navigator`, +`app-cli:cli-expert`), **no session-analyst** ✓ |
| `anchors` × terra | identical ✓ |
| `anchors-amp-dev` × haiku | body order ✓, `anchors:*` namespace ✓, **no session-analyst** ✓ |
| `anchors-amp-dev` × terra | identical ✓ |

**The finding this smoke bought, which nothing else would have surfaced.**
`anchors-amp-dev`'s anchors include is pinned to **`@main`**. So on a branch, the
DTU composes *branch* `anchors-amp-dev` with *`main`* `anchors` — visible
directly in the resolved paths: `amplifier-ecosystem.md` came from cache
`amplifier-foundation-26df61cccf7c4703` (the branch) while
`@anchors:context/system.md` came from `…-c909465861f9d6ce` (main). Hence the
amp-dev arms report `p4_delegate_present: true` — that is `main`'s four-principle
`system.md`, correctly fetched, not a defect in this branch.

**Pre-merge, `anchors-amp-dev` cannot be smoke-tested end-to-end from a branch
at all.** The post-merge composition is instead proven by the host runs in §4,
where the source override redirected *every* `microsoft/amplifier-foundation`
URL — the anchors include included — to this worktree, giving
`p3_short_form: true` / `p4_delegate_present: false`. The two runs cover
different halves on purpose: the DTU proves the production *fetch*, the host
proves the post-merge *composition*.

**Teardown:** `lane_teardown.sh … --yes` → `verified-gone=1 rows-flipped=1
failed=0`, probe-after ABSENT. **Open ledger rows: 0**
(`awk -F'\t' '$4=="open"' infra.tsv | wc -l` → `0`). The two unrelated live DTUs
on this host (`steward-desk-test`, `wayfinder-scout-eval`) were not mine and were
not touched; `infra_ledger.sh sweep` was never run.

**Status: DONE.**

---

## 7. Spend

**Authority: $4.00.** Arithmetic as written in the goal: 2 DTU smokes × ~$0.75
observed / 1.00 valid = $1.50, plus the workspace-setup path < $1.00 if cheap,
plus ~$1.50 slack for one retry.

**Re-stated at the observed price before the smoke, as required.** By then five
host invocations had cost $0.53 in total, and the smoke was re-planned as **1
DTU × ~$0.75 observed + 4 in-DTU prompts × ~$0.05 ≈ $0.95**, against $3.47
remaining. It closes with room, so the smoke was bought.

| purchase | cost |
|---|---|
| host run — anchors before (invalid: bare-path override dropped `logging.yaml`; re-run) | ~$0.01 |
| host run — anchors before | $0.04 |
| host run — anchors-amp-dev before | $0.11 |
| host run — anchors after | $0.11 |
| host run — anchors-amp-dev after + delegation (superseded by the re-run below) | $0.19 |
| host run — anchors-amp-dev after + delegation (final, matches the shipped file) | $0.07 |
| DTU — warm-up + 4 arms ($0.03 / $0.02 / $0.01 / $0.01 + warm-up) | ~$0.09 |
| **total** | **≈ $0.62** |

Mount-plan dumps, guard tests, the namespace probe and the full test suite are
$0 — no model call.

**Residue $3.38, and it is genuinely unspent rather than unspendable**: the work
completed, so there was no remaining purchase to make. The smallest useful
further purchase would have been a second DTU (~$0.75) to test the
amplifier-workspace setup path — see §9.

---

## 8. Two things looked up rather than invented

The spec asked the ecosystem doc to cite "the CLI's session repair command" and
to route to "the context-intelligence agents (graph-analyst → session-navigator
fallback)". Both premises turned out to be wrong on this installation, and the
doc says what is true instead.

1. **There is no `amplifier session repair` subcommand.** `amplifier session
   --help` lists `cleanup, delete, fork, list, resume, show` and nothing else.
   Routine transcript repair runs **automatically**, pre-turn and on resume
   (`amplifier_app_cli/main.py:3678-3737`). The manual tool is this repo's own
   `python scripts/amplifier-session.py {diagnose,repair,rewind,info,find}`,
   whose `--help` was read to get that verb list exactly. That is what the doc
   cites.
2. **`context-intelligence:graph-analyst` is not in either bundle's roster.**
   Both bundles compose `context-intelligence-navigation.yaml`, which is Layer 1
   of 3 and mounts `session-navigator` only; `graph-analyst` needs the analysis
   layer. Confirmed in the composed mount plan, where the sole CI agent is
   `context-intelligence:session-navigator`. (It *appears* in host sessions only
   because the full CI bundle is installed globally on this machine — a host
   artifact, not a bundle property.) The doc routes to the agent the bundle
   actually mounts and marks `graph-analyst` conditional.

---

## 9. Open, and left for the owner

1. **OPTIONAL-IF-CAP-PERMITS, not bought: the amplifier-workspace setup path in
   the DTU.** Skipped not on cost — $3.38 remained and it would have cost
   ~$0.75 — but because `amplifier-workspace` is not installed on this host and
   provisioning it inside the DTU is a second, unscoped profile change with no
   stated success criterion. Named here rather than silently dropped.
2. **A third copy of the ecosystem docs survives** in
   `experiments/behavioral-anchor-amplifier-dev/context/amplifier-dev/`, frozen
   and unshipped. Guard (d) excludes and names it. Deleting `experiments/` is
   outside this lane's owned paths.
3. **`agents/ecosystem-expert.md:89` still carries the dead `amplifier-dev`
   command** — the 7th rename site lives outside the anchors family. Untouched:
   the CANON section only calls for retargeting that file on the *fallback* path,
   which was not taken.
4. **The anchors include is pinned to `@main`** (§6). That is correct for
   production and is why a branch DTU composes a mixed pair. Nothing to fix; it
   is a property a future lane should know before trying to smoke a branch.
5. **Two pre-existing test failures**, both reproducing on a clean `origin/main`
   with every change stashed: `tests/test_sources.py::TestFileSourceHandler::test_resolve_existing_file`
   (known, named in the goal) and
   `tests/test_grpc_adapter_main.py::TestVerifyModuleType::test_non_isinstance_object_with_mount_passes`
   (**not** named in the goal — verified pre-existing, not caused here).

---

## 10. Landing

Draft PR **#360**, pushed to `origin`. Per the landing stage, this lane stops at
the draft PR marked ready; the **merge is the manager's stage**. The manager
re-runs the fail-befores and verifies the mount-plan deltas independently.
