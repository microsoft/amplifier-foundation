# DONE-NOTE — 67u: why does an EXPLICITLY-NAMED delegate bypass matrix resolution?

**Item:** `model_performance-67u` · **Branch:** `lane/67u-named-delegate-matrix-bypass`
**Repo:** `microsoft/amplifier-foundation` @ `18efe87` (origin/main)
**Spend: $0.00 against a $0 authority.** No API calls, no DTU, no probe re-runs.
Everything below is a code read plus pure-function reproduction. Accounting in §7.

---

## VERDICT (one line)

**There is no bypass.** `tool-delegate` has exactly ONE resolver call site, and it is
guarded on a `model_role` that is read **only from the tool input**. Naming an agent in
the prompt does not take a different path — it just produces a tool call with no
`model_role` argument, so the guard is false and the delegate falls through to the
session default. That fall-through is **intended, documented, and opt-out**
(`strict_model_role`). **No shipped routing decision is wrong today** (§5).

While answering that, the code read turned up **a real, separate defect in this repo**
(§4): three helpers in `spawn_utils.py` gave three different answers to "which mounted
instance does the bare module type `anthropic` mean?", and one function used two of them
in a single pass. Fixed, with fail-before tests.

---

## 1. THE MECHANISM, AT file:line — both call sites side by side

`modules/tool-delegate/amplifier_module_tool_delegate/__init__.py` @ `18efe87`.

**Where `model_role` comes from — the only place:**

```python
1625:  raw_model_role = input.get("model_role", "").strip()
```

**The only resolver call site, and its guard:**

```python
1636:  if raw_model_role and provider_preferences is None:
1637:      resolver = (
1638:          self.coordinator.get_capability("model_role_resolver")
...
1650:          resolved = await resolver.resolve(raw_model_role)
1651:          if resolved:
1653:              provider_preferences = list(resolved)
```

**The only agent-level routing input the spawn path reads — note what is NOT there:**

```python
1819:  # Apply agent-level default provider_preferences if caller didn't specify
1820:  if provider_preferences is None and self.provider_selection_enabled:
1821:      agent_cfg = agents.get(agent_name, {})
1822:      agent_default_prefs = agent_cfg.get("provider_preferences", [])
```

`agent_cfg.get("model_role")` **does not appear anywhere in the module.** Verified:
`grep -n 'agent_cfg\|agents\.get' __init__.py` returns lines 1821/1822 (preferences),
2061/2062 and 2510/2515 (return-contract) — and nothing else.

### The answer to the item's primary question

The two paths are **the same code**. The difference is entirely in the tool-call
arguments the root model emitted:

| | organic (h7n arm A, S3) | named (h7n probe) |
|---|---|---|
| tool arguments | `agent=…`, `instruction=…`, **`model_role="reasoning"`** | `agent=…`, `instruction=…` |
| guard at `:1636` | **true** | **false** |
| `resolver.resolve()` | called | **never called** |
| `provider_preferences` | matrix candidate + its `config` | `None` |
| `routing_matrix` provenance | populated | `None` (correctly — no matrix produced it) |
| child model | matrix's model **and** matrix's effort (`high` ≠ root's `medium`) | session default |

The probe's prompt (`scripts/probe_delegate.sh`) fully specified the call —
*"delegate to agent \"anchors-amp-dev:architect\" with instruction …"* — so the model
emitted exactly those two arguments. That is the whole mechanism. Of the item's three
candidate explanations, **"the named-agent path may pass no `model_role`" is correct**;
"architect/builder resolve differently from explorer" and "the probe caused the root to
pass `provider_preferences` itself" are both **ruled out** — agent identity is never
consulted for routing here (`:1821-1822`), and an explicit `provider_preferences` pin
would have populated `provider_preferences` rather than left it `None`.

### Why the agents' own declared roles did not save it

The captured parent `session:config`
(`probes/h7n-knob-consistent-anthropic/raw/agent-prefs-armA.json`) shows
`anchors-amp-dev:architect` carrying `model_role: ["reasoning","general"]` and
**`n_prefs: 0`**; builder `["coding","general"]`, `n_prefs: 0`. So the agent frontmatter
*did* declare roles, and the one fallback that exists at `:1822` had nothing to read.

**That declaration is honoured in a different repo, and this lane stops at the boundary
as instructed.** `amplifier-app-cli`'s `session_spawner.py:568-575` states it verbatim:

> *"The routing hook (hooks-routing) writes provider_preferences into agent configs at
> session:start when resolving model_role declarations in agent frontmatter.
> Tool-delegate normally reads these and passes them as a function argument…"*

So the design is two-layer — **(A)** caller-supplied `model_role` resolved parent-side by
tool-delegate, **(B)** agent-declared `model_role` resolved by hooks-routing at
session:start. Layer A is what the probe skipped. **Why layer B produced `n_prefs: 0` for
11 of 13 agents in that container lives in `amplifier-bundle-routing-matrix` /
`amplifier-app-cli` — reported here, not investigated** (SCOPE-OUT: "if the divergence
turns out to live in app-cli's session_spawner or in routing-matrix's hooks-routing, say
so and stop"). That is the one open thread this lane hands on.

---

## 2. THE ECONOMY-MATRIX CASE — explained

**The case:** opus-5 root, `economy` matrix → architect ran on `gpt-5.6-terra`, builder on
`gpt-5.6-luna`, while `economy` is described as declaring `claude-sonnet-*` /
`claude-haiku-*`.

**It is ordered-candidate fallback, authored into `economy.yaml` itself.** Read at
`routing-matrix@0188a12`, `routing/economy.yaml`:

| role | candidate #1 | candidate #2 | observed child |
|---|---|---|---|
| `reasoning` | `anthropic: claude-sonnet-*` | **`openai: gpt-?.?-terra*`** | **gpt-5.6-terra** ✓ #2 |
| `coding` | `anthropic: claude-haiku-*` | **`openai: gpt-?.?-luna*`** | **gpt-5.6-luna** ✓ #2 |

Both children landed on **candidate #2 of their own role**, exactly. The advance from #1
to #2 is explicit, documented behaviour in two places that mirror each other —
`amplifier_foundation/spawn_utils.py:867-884` ("A preference whose provider is present
but whose glob pattern fails to resolve … is NOT applied with the raw, unresolved
pattern … we advance to the next preference in the ordered list, mirroring
`resolve_model_role()`'s `continue` behavior") and routing-matrix's
`modules/hooks-routing/.../resolver.py:461`.

**"Anthropic globs resolved to OpenAI models" is therefore a misreading of the symptom.**
The matrix's own author put OpenAI second in both roles; the Anthropic glob matched no
installed model, so the matrix's own next choice won. Reproduced as a pure function, $0,
in `TestOrderedCandidateFallback::test_unresolvable_first_candidate_advances_to_the_next`
(parametrised over both roles, asserting terra and luna by name).

**Positively: this row is the one that proves per-role resolution DID run.** A session
default gives both children the *same* model; these two differ from each other **and**
from the root. Nothing but role-differentiated resolution produces that.

**What I could NOT determine at $0, stated plainly.** *Why* the Anthropic candidate failed
to match in that container. Two mechanisms are consistent with it — the cell-pinning
harness restricting what the anthropic mount lists, and the instance-selection defect in
§4 — and separating them needs the container, which is gone, or a fresh run, which is not
authorised at $0 and which the SCOPE-OUTs forbid re-running. Recorded as open rather than
guessed.

---

## 3. VERDICT ON EACH OF THE THREE REPRODUCTIONS

Argued from the code, per the item's requirement. Verdict vocabulary is the item's own:
**(a)** child resolves to the matrix's declared model, or **(b)** divergence is
**INTENDED** with the reason.

| # | reproduction | verdict | reason, from the code |
|---|---|---|---|
| 1 | opus root + `anthropic` → both children `claude-opus-5` (builder's `coding` should be `claude-sonnet-*`) | **(b) INTENDED** | No `model_role` in the tool call ⇒ guard at `:1636` false ⇒ resolver never consulted ⇒ documented session-default fall-through (`:1699-1712`). Corroborated independently by **effort**: `anthropic.yaml` `reasoning` carries `reasoning_effort: high`, and a promotion *does* carry a candidate's `config` (`spawn_utils.py:767-773`); the child's `session:config` shows the root's `xhigh`, so no promotion occurred. |
| 2 | sonnet root + `anthropic` → both children `claude-sonnet-5` (architect's `reasoning` should be `claude-opus-*`) | **(b) INTENDED** | Same mechanism, same guard. |
| 3 | opus root + `economy` → children `gpt-5.6-terra` / `gpt-5.6-luna` | **(b) INTENDED, and it is the matrix's own ordered fallback** | §2. Each child took candidate #2 of its own role, which `economy.yaml` authors as OpenAI. Per-role resolution ran here. Residual open: why candidate #1 did not match (§2). |

None is verdict (a); none is a defect in the named-delegate path.

---

## 4. THE DEFECT THIS CODE READ DID FIND (fixed here)

`amplifier_foundation/spawn_utils.py` had **three** answers to "which mounted instance
does the bare module type `anthropic` mean?":

| helper | rule | line |
|---|---|---|
| `_find_provider_instance` | **highest priority** (lowest number) | `:601-617` |
| `_find_provider_index` | **first declared** | `:636-645` |
| `_build_provider_lookup` | **last declared** (plain dict, last write wins) | `:660-673` |

`apply_provider_preferences_with_resolution` calls **two of them in one pass**: it
resolves the candidate's model glob against the instance `_find_provider_instance` picks
(`:430`), then promotes the index `_build_provider_lookup` returns (`:859-888`).

This matters exactly when a matrix is in play, because a matrix addresses providers by
bare module type while the mount plan carries several instances of that module — which is
the shape the routing-matrix bundle explicitly asks for (`_find_provider_instance`'s own
docstring, `:585`: distinct `id:`s exist *"for routing-matrix disambiguation"*).

**Measured on the h7n roster** (10 mounts, 2 module types, cell forced to priority 0):

```
_build_provider_lookup["anthropic"] -> idx 7 = 'fable'    prio 7   (promoted)
_find_provider_instance("anthropic") ->      'opus'     prio 0   (model list read)
_build_provider_lookup["openai"]    -> idx 9 = 'luna-max' prio 9   (promoted)
_find_provider_instance("openai")   ->      'sol'      prio 2   (model list read)
```

So the model resolved from one instance's list was written as `default_model` onto a
different instance, and *that* instance was promoted to priority 0 — carrying its own
`base_url`, long-context, and cache-retention settings. Silently: nothing logs a
mismatch, and the child looks correctly routed because the model name is right.

**The fix (`spawn_utils.py`, +87/−16):** one rule — *highest priority wins, ties by
declaration order; an explicit instance `id` is more specific than a module type and
always wins* — applied to all three helpers via a shared `_provider_priority()`.
`_build_provider_lookup` becomes two passes (priority-aware type keys, then id keys
overwriting). The single-instance case, which is the overwhelming majority, is unchanged
and pinned by `test_single_instance_plans_are_unchanged`.

**Behaviour change to flag for review:** a multi-instance plan that sets *no* `priority`
previously resolved a bare type to the **last** declared instance and now resolves to the
**first** — which is what `_find_provider_instance` already did, so this removes a
disagreement rather than inventing a rule.

---

## 5. IS ANY SHIPPED ROUTING DECISION WRONG TODAY?

**On the named-delegate question: no. Blast radius zero.** The organic path is what real
workloads use, h7n proved it healthy (matrix model *and* matrix effort, glob-resolved,
not the root's effort), and it is untouched here. The "bypass" only appears when a prompt
over-specifies a delegate call so the model omits `model_role` — a probe artefact, and
even then the outcome is the documented, opt-out fall-through, not a wrong decision.

**On the §4 defect: yes, but narrowly.** It can only fire where **≥2 instances of one
provider module are mounted** *and* a matrix candidate addresses that module by bare type
*and* the instances differ in more than model. That is the eval-harness roster and any
multi-account/multi-endpoint setup; it is not the default single-instance install. When
it fires the model name still looks right, which is why it survived this long.

**Not re-opened, as instructed:** the organic delegation path. h7n verified it; this lane
did not re-litigate it and produced no evidence against it.

---

## 6. DELIVERABLES

| deliverable | state |
|---|---|
| Mechanism named at file:line, both call sites side by side | **DONE** — §1 |
| Economy-matrix case explained | **DONE** — §2 (with the one residual explicitly recorded as open, and what was ruled out) |
| Verdict on each of the three reproductions | **DONE** — §3, all three (b) INTENDED, argued from code |
| A test pinning whichever answer is true | **DONE** — `tests/test_named_delegate_matrix_67u.py`, 15 tests: 4 characterization (intended behaviour), 8 fail-before defect tests, 3 economy-fallback reproductions |
| Say plainly whether any shipped routing decision is wrong today | **DONE** — §5 |
| Do NOT re-open the organic path | **HONOURED** |
| Full suite green | **DONE** — §7 |
| DRAFT PR on origin | **DONE** — see marker |
| DONE-NOTE at the lane artifact root | **this file** |

---

## 7. SPEND, TESTS, DEVIATIONS

**Spend: $0.00 against a $0 authority.** No API calls, no DTU launched, no infrastructure
registered, nothing to tear down. The authority stated `$0` for a code-read question with
reproductions already on disk; the arithmetic closes trivially because **nothing needed
buying** — the primary question was answerable from source, and every reproduction in
§2/§4 is a pure function called with no network. Residue: the full $0 authority, unspent;
the smallest useful purchase it could not buy is one DTU launch (~$2–5 at this batch's
observed rates), which was **not needed**.

**Full suite** (`uv run pytest -q`, repo root):

```
baseline @ 18efe87 : 1924 passed, 1 skipped, 1 warning in 20.17s
with this change   : 1939 passed, 1 skipped, 1 warning in 19.21s      (+15, 0 regressions)
```

**Fail-before, verified by reverting only `spawn_utils.py`:**

```
7 failed, 8 passed        # the 7 defect tests fail; characterization + fallback tests pass either way
AssertionError: assert 'fable' == 'opus'
```

`ruff check` and `ruff format --check` clean on both touched files. The single repo-wide
`ruff` error (`F401 ParsedURI` in `amplifier_foundation/updates/__init__.py`) is
**pre-existing on `18efe87`** — confirmed by stashing this lane's changes and re-running —
and was left alone.

**Deviations and choices, recorded:**

1. **Tests placed in `tests/`, not `modules/tool-delegate/tests/`.** The goal notes CI runs
   `pytest tests/` only, excluding the module's own test dir. `pyproject.toml` sets
   `pythonpath = ["modules/tool-delegate"]`, so `tests/` can import `DelegateTool`
   directly — the characterization tests therefore actually run in CI. The known CI gap is
   noted, not fixed (not this lane's).
2. **`_find_provider_index` aligned too**, although it has no production caller today
   (`grep`: definition + tests only). Leaving a third disagreeing rule in the same file is
   how this gets re-filed; six lines removed the trap.
3. **Did not cross into `amplifier-app-cli` or `amplifier-bundle-routing-matrix`.** Both
   were read for evidence (quoted above) and neither was modified, per the SCOPE-OUT.
4. **Did not re-run the delegate probes.** Answered from source and from the captures, as
   instructed.
5. **No fresh run was required**, so no priced authority is being requested.

**Claim tags** (§5 rules-for-lanes): mechanism at `:1625`/`:1636`/`:1822` — *(knob: none ·
family: n/a · confidence: **measured** — code read at `18efe87` · evidence:
`modules/tool-delegate/amplifier_module_tool_delegate/__init__.py:1625,1636,1819-1826`)*.
Economy fallback — *(knob: routing.matrix=economy · family: mixed anthropic/openai ·
confidence: **measured** — pure-function reproduction, n=2 roles · evidence:
`tests/test_named_delegate_matrix_67u.py::TestOrderedCandidateFallback`)*. Instance
split-brain — *(knob: none · family: n/a · confidence: **measured** — fail-before test,
n=10-mount roster · evidence: `amplifier_foundation/spawn_utils.py:601-674`)*. Why layer B
produced `n_prefs: 0` in that container — *(confidence: **not determined**; out of repo
scope)*.
