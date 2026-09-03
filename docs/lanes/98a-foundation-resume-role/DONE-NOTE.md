# DONE-NOTE — 98a: tool-delegate resume path drops model_role/provider_preferences

Item: `model_performance-98a` (project `model_performance`)
Branch: `lane/98a-foundation-resume-role`
Parent commit measured against: `0d7b3f6e66953bcdafd4372c16f6694aab942292`

## Result

DONE. The amplifier-foundation half of the "resume wipes the model role"
defect is fixed, with a test that fails on the parent commit and passes on
the fix. Full suite green. Draft PR opened. Nothing merged from this lane.

## The two sites, quoted

Both at `modules/tool-delegate/amplifier_module_tool_delegate/__init__.py`,
line numbers as of the parent commit `0d7b3f6`.

### Site 1 — the call site, `execute()` :1533-1539

```python
            return await self._resume_existing_session(
                session_id,
                instruction,
                hooks,
                tool_call_id=tool_call_id,
                parallel_group_id=parallel_group_id,
            )
```

**Why it drops the role.** By the time control reaches this branch,
`execute()` has *already* resolved the caller's `model_role` into
`provider_preferences` (the resolver block at :1396-1490) — both values are
live local variables. The spawn branch 60 lines below passes both to
`_spawn_new_session(... provider_preferences=..., raw_model_role=...)`. This
branch passes neither. The resolution work is done and then thrown away.

### Site 2 — the resume path, `_resume_existing_session()`

Signature, :2113-2121 — no parameter exists to receive them even if the
call site had sent them:

```python
    async def _resume_existing_session(
        self,
        session_id: str,
        instruction: str,
        hooks,
        *,
        tool_call_id: str = "",
        parallel_group_id: str | None = None,
    ) -> ToolResult:
```

The wire call to the app layer, :2199-2202:

```python
            # Resume agent session (with optional session-level timeout)
            resume_coro = resume_fn(
                sub_session_id=full_session_id,
                instruction=effective_instruction,
            )
```

**Why it drops the role.** `session.resume` is the app-layer capability —
this call *is* the request tool-delegate makes of the app layer, and its
kwargs are what the app layer turns into the resumed session's provider
config. Two kwargs and no more. The app-layer half already accepts
`provider_preferences` and `model_role`
(amplifier-app-cli `session_spawner.py:911-926` → `resume_sub_session`
:1246-1251, shipped in #292 / `31ad917`); this repo simply never sent them,
so the app layer's promotion had nothing to promote and the resumed leg fell
back to settings priority. Silent: no error, no telemetry difference —
`delegate:agent_spawned` carried `model_role`, `delegate:agent_resumed`
carried no routing field at all, so there was nothing to compare it against.

The two sites are one bug: the call site has the values and does not pass
them; the resume path could not accept them if it did.

## What changed

1. **Call site** now threads `provider_preferences` + `raw_model_role`.
2. **`_resume_existing_session`** accepts both; sends them to `resume_fn`.
3. **`_session_routing` cache** — records the routing each spawn resolved
   to, keyed by sub_session_id, mirroring the existing `_session_agents`
   cache. This is what makes the acceptance criterion hold for the shape
   every real caller uses: routing is pinned once on the *spawn* call, and
   resumes are then issued as `(session_id, instruction)` with no routing
   argument at all. Caller-explicit routing on the resume call still wins.
4. **`_supported_resume_routing_kwargs()`** — introspects the app-layer
   capability and sends only what it declares. An older app layer taking
   `(sub_session_id, instruction)` keeps working instead of dying on an
   unexpected kwarg; what it *cannot* do is drop the routing quietly — the
   withheld kwargs are named in a `logger.warning`.
5. **`delegate:agent_resumed`** now carries `model_role` /
   `provider_preferences`, same shape as `delegate:agent_spawned`, so the
   two legs of a delegation are comparable in telemetry. Additive.
6. README: documents the resume capability's optional kwargs and the
   precedence rule.

## Measured

**Fail-before / pass-after.** Same test file both times; the fix was stashed
for the "before" run, so only the module changed.

Before (parent `0d7b3f6`, fix stashed) — `docs/lanes/98a-foundation-resume-role/fail-before.txt`:

```
8 failed, 2 passed
```

The 2 that passed before are the backward-compatibility tests
(`test_legacy_two_argument_resume_capability_still_works`,
`test_plain_resume_sends_no_routing_kwargs`) — they *should* pass before, and
they pin the behaviour the fix must not break.

After: `10 passed`.

**Full suite:** `1873 passed, 1 skipped, 1 warning in 19.45s` (the warning is
pre-existing, in `tests/test_subprocess_runner.py`).

## Honest scope of "the wire"

The acceptance criterion says "the resumed session's first LLM request
carries the same model_role/provider_preferences". This repo does not build
LLM requests — it defines the capability contract and the app layer
implements it. So the strongest assertion available *here* is at the
`session.resume` seam, which is where the values were being lost.

`TestResumedRequestConfig` closes as much of the remaining gap as this repo
can: it drives foundation's own `apply_provider_preferences` — the same
function the app layer calls to build a child's provider config — with
whatever actually crossed the seam, and asserts the resumed leg's effective
provider/model (`provider-anthropic` / `claude-opus-4`) equals the spawn
leg's. That is the config the first request would be issued against. The
end-to-end wire capture that proves it against a live provider is rc0's, and
is what motivated this item; it is not re-run here (no spend authority).

## Spend

**$0.00.** No API calls, no DTU, no infrastructure created, nothing to tear
down. The spend cap for this item was $0 and it was not approached: every
assertion is a local unit test against mocked capabilities. Nothing in the
remaining budget would have bought a stronger result at this layer — an
end-to-end wire re-capture would need a live provider and belongs with the
integration lane, not here.

## Deviations

1. **Added the `_session_routing` spawn-time cache.** Strictly more than
   "thread the two arguments through". Without it the fix only covers a
   caller who restates the role on *every* resume call, which is not how the
   tool is used — and the acceptance criterion is phrased about a session
   *spawned* with a role, not a resume call carrying one. Same lifetime and
   same cold-cache caveat as the existing `_session_agents` cache; on a cold
   cache the app layer's own recovery (agent overlay → persisted mount plan)
   takes over, so a cold cache is a fallback, not a regression.

2. **Added the capability-signature guard.** Unconditionally sending two new
   kwargs to an app-layer-provided callable would have turned a silent
   downgrade into a hard `TypeError` for any app layer that has not taken
   app-cli #292. Guarded + warned instead. A callable that cannot be
   introspected is treated as accepting nothing (preserve the working call
   shape) and is also warned about — chosen deliberately over guessing and
   crashing a resume.

3. **Added routing fields to `delegate:agent_resumed`.** Not asked for. The
   reason the drop survived this long is that it was invisible in telemetry;
   leaving that gap open would leave the next regression equally invisible.
   Additive — absence in an old capture means UNKNOWN, never "no routing".

4. **CI runs `pytest tests/` only**, which does not include
   `modules/tool-delegate/tests`. The local `testpaths` does include it and
   that is what was run for the green result above. Not changed here — out
   of scope for a bug fix, and worth its own item.

## Open

- The app-cli half (#292) is merged; the foundation half is this draft PR.
  Neither alone closes the defect — a deployment needs both.
- `routing-matrix` #53 (`dca54b5`) remains defense-in-depth only; it does not
  substitute for either fix.
- CI's `pytest tests/` excludes the tool-delegate module tests, so this
  test would not run in CI as configured. Flagged, not fixed.
