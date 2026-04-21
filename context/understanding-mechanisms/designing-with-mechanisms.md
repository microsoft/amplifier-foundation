# Bundle Developer's Guide to Amplifier Mechanisms

## Introduction

Amplifier exposes seven composable primitives for building agent systems: 

@foundation:context/understanding-mechanisms/mechanisms/modes.md
@foundation:context/understanding-mechanisms/mechanisms/recipes.md
@foundation:context/understanding-mechanisms/mechanisms/skills.md
@foundation:context/understanding-mechanisms/mechanisms/agents.md
@foundation:context/understanding-mechanisms/mechanisms/hooks.md
@foundation:context/understanding-mechanisms/mechanisms/tools.md
@foundation:context/understanding-mechanisms/mechanisms/content.md

They overlap by design. The goal is not to pick one, but to assign **clear ownership** and **correct attachment points**, then compose them through **behaviors** — the YAML wiring layer that assembles mechanisms into bundles.

> **Use combinations freely. Avoid duplicated authority.**

| Mechanism | Verb | Triggered by |
|-----------|------|-------------|
| **Mode** | constrains | User or agent activates |
| **Recipe** | organizes | User or agent invokes |
| **Agent** | thinks | Parent delegates |
| **Skill** | knows how | Agent loads on demand |
| **Hook** | observes | Lifecycle event (deterministic) |
| **Tool** | acts | LLM decides to call |
| **Content** | informs | Resolution at load/injection time |

**Guide map:**

| Section | What it answers |
|---------|----------------|
| [Attachment Points](#attachment-points) | Where each mechanism binds and how long it lives |
| [Behaviors](#the-wiring-layer-behaviors) | The YAML layer that assembles mechanisms into bundles |
| [Choosing the Right Mechanism](#choosing-the-right-mechanism) | Ownership rules and decision tree |
| [Failure Modes](#failure-modes-misplaced-attachment) | Anti-patterns, enforcement levels, workflow ordering |
| [Context Window Economics](#context-window-economics) | Token costs, compaction, context lifecycle |
| [Parallelism](#parallelism-and-concurrency) | Two tiers, patterns, token cost |
| [Model Selection](#model-selection) | Routing, fallback chains, delegation distribution |
| [Turn Budgets](#turn-budgets-and-bounded-work) | Bounding work for focus and cost control |
| [Human-in-the-Loop](#human-in-the-loop-design) | Checkpoint placement, modes, approval gates |
| [Review Checklist](#mechanism-spec-review-checklist) | Verification checklist for mechanism specs |

---

## Attachment Points

> **Where does it bind, and how long does it live?**

| Mechanism | Attaches to | Lifetime | State location | Authored as |
|-----------|-------------|----------|----------------|-------------|
| Mode | Session | Togglable, ephemeral re-injection | `session_state["active_mode"]` | `.md` with YAML frontmatter |
| Recipe | Workflow execution | Per-invocation, resumable | `state.json` on disk | `.yaml` |
| Agent | Sub-session | One-shot or resumable | Kernel session (in-memory) | `.md` with YAML frontmatter |
| Skill | Task pattern | On-demand (inline or forked) | None (stateless) or fork session | `SKILL.md` in a directory |
| Hook | Lifecycle event | Session lifetime | Module state + `session_state` | Python module |
| Tool | LLM decision | Session lifetime | Module state | Python module |
| Content | Context window | Per-injection | None (consumed by LLM) | `.md` files |

For token costs per mechanism, see [Context Window Economics](#context-window-economics).

---

## The Wiring Layer: Behaviors

Behaviors are YAML files that compose mechanisms into reusable capability packages. They are how mechanisms become available in a session.

```yaml
# Example: behaviors/my-feature.yaml
includes:
  - bundle: some-dependency:behaviors/base
hooks:
  - module: hooks-mode
    source: git+https://github.com/microsoft/amplifier-bundle-modes@main#subdirectory=modules/hooks-mode
    config:
      search_paths: ["@my-bundle:modes"]
tools:
  - module: tool-mode
    source: git+https://github.com/microsoft/amplifier-bundle-modes@main#subdirectory=modules/tool-mode
    config:
      gate_policy: warn
  - module: tool-skills
    config:
      skills:
        - "@my-bundle:skills"
agents:
  include:
    - my-bundle:agents/specialist
context:
  include:
    - my-bundle:context/instructions.md
```

**Key pattern:** A behavior wires hooks, tools, agents, and context into a single composable unit. Bundles compose behaviors via `includes:`. This is the layer between individual mechanisms and the final bundle.

---

## Choosing the Right Mechanism

Each concern should have one primary owner:

| Concern | Primary owner | Why not elsewhere |
|---------|---------------|-------------------|
| Session-wide constraints | **Mode** | Hooks enforce deterministically; skills/recipes can't guarantee session-wide policy |
| Multi-step workflow ordering | **Recipe** | Checkpointing and approval gates require persistent state |
| Task-specific expertise | **Skill** | Portable, discoverable, loadable on demand without sub-session overhead |
| Complex sub-task reasoning | **Agent** | Needs isolated context, own tool surface, potentially different model |
| Guaranteed lifecycle behavior | **Hook** | Fires deterministically, not subject to LLM judgment |
| LLM-decided capabilities | **Tool** | Model chooses when to invoke based on schema |
| Reference knowledge | **Content** | Passive — no execution, just context |

**Decision tree** — ask in order:

1. Must it always apply, regardless of what the LLM decides? **Hook** (code-decided enforcement) or **Mode** (policy overlay)
2. Is it a multi-step process with checkpoints? **Recipe**
3. Is it reusable task knowledge, loadable on demand? **Skill**
4. Does the LLM need to decide when to invoke a capability? **Tool**
5. Does it need isolated reasoning with its own context? **Agent**
6. Is it just reference information? **Content**

**Hooks vs modes:** Hooks are Python modules — use them when you need programmatic logic (parse responses, compute values, call APIs on lifecycle events). Modes are declarative `.md` files — use them when you need tool policy tiers and injected guidance without writing code.

**Skills vs agents:** Inline skills are cheaper (no sub-session overhead) and portable (Agent Skills spec). Fork skills converge with agents but require less infrastructure (a directory vs. a bundle). Use agents when you need: resumable sessions, specific tool composition, or deep context isolation.

---

## Failure Modes (Misplaced Attachment)

| Anti-pattern | Symptom | Fix |
|---|---|---|
| **Policy leakage** (too low) | Safety rules in skills or recipe prompts — inconsistent enforcement | Move to a **mode** with tool policy tiers |
| **Automated workflow hidden in skills** | Step sequences that *can run without user interaction between steps* encoded in skill bodies — brittle, no checkpointing | Move to a **recipe** (exception: interactive conversational workflows — see Workflow Ordering below) |
| **Expertise smearing** (too high) | Heuristics duplicated across recipe step prompts | Centralize in a **skill** |
| **Skill hypertrophy** | A skill that spawns agents, manages state, enforces policy | Split: policy to mode, workflow to recipe, expertise stays in skill |
| **Capability fiction** | APIs described in context but not exposed as tools | Wire as a **tool** (or MCP resource) |
| **Cognitive overload** | Single agent prompt does everything | Decompose into **delegated agents** with focused concerns |
| **Knowledge entanglement** | Docs copy-pasted into agent prompts, skills, and recipe steps | Centralize as **content** files, reference via `@mention` |
| **Missing hook, using mode** | You need programmatic logic (compute, API calls) but used a mode | Write a **hook** module — modes are declarative only |
| **Composition loophole** | Mode blocks `write_file` but allows `delegate` or `recipes` as safe — sub-agents bypass parent mode restrictions via their own sessions | Audit which tools can spawn child sessions. If `delegate`/`recipes` are safe, sub-agents can write files, run code, and launch pipelines regardless of the parent mode's restrictions. Name it explicitly in the spec. |

### Enforcement Levels: Structural vs Conventional

**Tool restrictions reliably shape behavior; prose instructions frequently don't.** In observed sessions, behaviors described only in prose guidance were skipped consistently, while tool restrictions were enforced every time.

| Level | Enforced by | Reliability | Example |
|-------|------------|-------------|---------|
| **Structural** | Tool policies, hook gating, blocked tools | High — prevented if violated | `write_file` blocked in design mode; gate policy requires double-confirmation to transition |
| **Conventional** | Prose in mode body, skill guidance, agent instructions | Variable — model skips under pressure | "Always validate with the user before proceeding" |
| **Inferred** | Neither stated nor enforced; emerges from tool availability | Unpredictable | Agent uses `delegate(explorer)` because the instruction says "analyze" without specifying how |

**Structural enforcement is doubly effective.** Models read the safe-tools list and self-restrict without attempting prohibited tools — zero blocked-tool error events have been observed across thousands of sessions. The guardrail shapes behavior *and* catches violations.

**Design test:** If a behavior is critical enough to appear in a spec, ask: "What happens if the model skips this?" If the answer is "the workflow breaks," encode it structurally. If the answer is "output quality degrades but it's still usable," conventional guidance is acceptable.

### Workflow Ordering: Recipes vs Mode + Companion Skill

Multi-step workflows need ordering. Where it lives depends on who drives the workflow.

**Recipes vs ad-hoc delegation.** Recipes are powerful but high-ceremony — in practice, most multi-step work uses ad-hoc delegation chains. Design a recipe when the workflow will be repeated, needs resumability, or requires approval gates. Use sequential delegation for one-off workflows or those needing conversational steering between steps.

| Workflow type | Mechanism | Why |
|---------------|-----------|-----|
| Fixed steps, no user interaction between them | **Recipe** (flat) | Checkpointing, persistent state, isolated step sessions |
| Fixed steps, user approval needed between stages | **Recipe** (staged) | Approval gates pause for user between stages |
| Conversational, user steers at every phase | **Mode + companion skill** | Root session handles conversation; skill guides phases |
| Both automated and interactive paths needed | **Both** | Recipe for the automated pipeline, skill for the interactive path |

**When workflow-in-skill is wrong:** A skill encoding steps that could run as a recipe — no user interaction needed between steps. Move it to a recipe.

**When workflow-in-skill is right:** A skill guiding a multi-phase conversation where user input at each phase shapes the next. The skill describes phases and decision points; the LLM adapts them to the conversation.

**Guardrails for companion skills that encode workflow ordering:**

1. **Build the recipe equivalent when possible.** The recipe handles "run end-to-end with checkpoints." The skill handles "walk me through this interactively."
2. **Keep the skill as a phase guide, not a script.** Describe phases and decision points. Don't dictate exact tool calls.
3. **Don't enforce policy from the skill.** Policy enforcement belongs in the mode's tool policy tiers.

---

## Context Window Economics

Every mechanism has a token cost. Understanding which costs are fixed vs compactable is essential for bundles that stay effective across long sessions.

### The Three-Layer Model

```
Context Window (e.g., 200K tokens)
+-- LAYER 1: System Prompt (never compacted)
|   Instruction body + all @mentioned files + context.include files
|   Re-read from disk every turn. Fixed cost. Immune to compaction.
|
+-- LAYER 2: Message History (compactable)
|   User/assistant messages, tool calls and results.
|   Accumulates during session. Compacted when budget pressure hits 92%.
|   8-level cascade: truncate tool results -> remove old messages -> stub.
|
+-- LAYER 3: Ephemeral Injections (per-turn, not stored)
|   Skills visibility list, active mode body, hook injections.
|   Appended AFTER compaction runs. Never enters message history.
|   Cost repeats every turn but never compounds.
|
+-- RESERVED
    4096 tokens safety margin + 50% of max_output_tokens + 800 for compaction notice
```

**Budget formula:** `context_window - (max_output_tokens x 0.5) - 4096`

Compaction triggers at 92% of budget and targets 50%. The system prompt sits outside the compactable portion — it's a permanent tax.

### How Each Mechanism Affects the Budget

| Mechanism | Layer | Compactable? | Cost model |
|-----------|-------|-------------|------------|
| **Content** (bundle-level: @mentions in instructions + context.include) | System prompt | Never | Fixed per turn. Re-read from disk every LLM call. Accumulates across behavior composition. |
| **Content** (user-level: @mentions in conversation or `read_file`) | Message history | Yes | Enters as user message content or tool result. Subject to compaction like any other message. |
| **Agents** (delegation) | Message history | Yes | Parent pays ~200-500 tokens per delegation. Child absorbs all exploration tokens. |
| **Skills** (L1 visibility) | Ephemeral | Never | ~1,000-1,500 tokens per turn. Scales with number of composed skills. |
| **Skills** (L2 load) | Message history | Yes | Full skill content as tool result. Subject to compaction. |
| **Skills** (fork) | Isolated session | N/A | Like agents: runs in child context, parent sees only the result. |
| **Hooks** (ephemeral injection) | Ephemeral | Never | Per-turn cost while hook fires. Not stored. |
| **Hooks** (persistent injection) | Message history | Yes | Stored via `context.add_message()`. Subject to compaction. |
| **Modes** | Ephemeral | Never | Full mode body injected every turn while active. Stops on deactivation. |
| **Recipes** | Isolated per step | N/A | Each step runs in a fresh child session. Only text output propagates. |
| **Tools** (results) | Message history | Yes, first | Primary compaction target. Truncated to 250 chars before messages are removed. Last 5 protected. |

### Design Implications

**The fixed-cost trap.** Bundle-level content files (@-mentioned in instructions or listed in `context.include`) are the most dangerous budget item — always present, never compacted, invisible. A bundle composing three behaviors each adding 2K tokens of context has a 6K permanent floor before any conversation starts. Note: files @-mentioned by the user during conversation or read via `read_file` enter message history and ARE compactable — only bundle-level injection is immune.

**Compaction favors tool results over conversation.** The cascade truncates tool results first (Levels 1-2), then removes old messages (Level 3+). Heavy tool use is more sustainable than heavy context file use.

**Ephemeral injections add up silently.** Skills visibility (~1K) + active mode (~500-2K) + hooks can consume 3-4K tokens per turn that never appear in history but still eat the window.

**Agents are the primary context management tool.** Delegating a 20-file exploration costs the parent ~400 tokens. Doing it directly costs ~20K that persist until compacted.

### Rules of Thumb

1. **Bundle-level content files: budget per byte.** Every token in an @-mentioned instruction file or `context.include` entry is paid every turn and immune to compaction. Move detailed reference material to skills (on-demand, compactable) or agent @mentions (child context only).

2. **Skills over content for reference material.** A 3K-token guide as content costs 3K/turn. As a skill: ~100 tokens/turn (L1 visibility) plus 3K once when loaded, and that load is compactable.

3. **Fork skills over inline skills for heavy work.** If a skill reads many files or produces large outputs, use `context: fork` to run in an isolated child session.

4. **Modes should be concise.** Mode content is injected every turn it's active. Keep guidance focused; move detailed methodology to a companion skill loaded once on mode activation.

5. **Watch behavior composition.** Each `includes:` adding `context.include` entries increases the permanent floor. Audit the total when composing multiple behaviors.

6. **Prefer ephemeral over persistent hook injections.** Use `ephemeral=True` unless the information must survive across turns.

7. **Loaded skills can trigger a compaction death spiral.** When compaction truncates a loaded skill, the model detects the missing guidance and reloads it, adding tokens that accelerate the next compaction. In observed sessions, this produced 200+ reloads of a single skill. **Mitigations:** (a) Convert must-persist skills to content files. (b) Load phase-specific skills at phase entry and complete before compaction is likely. (c) Keep skills concise (<2K tokens) so compaction targets other things first. (d) Design for graceful degradation of skill guidance in long sessions.

### Calculate Your Token Floor

The per-turn floor is the minimum token cost of every LLM call before any conversation happens.

```
Your bundle's per-turn floor:
  Content files (your own @mentions + context.include):  ____ tokens
  Content files (from composed behaviors):               ____ tokens
  Skills L1 visibility (~50-100 per skill):              ____ tokens
  Active mode body (when on):                            ____ tokens
  Ephemeral hook injections:                             ____ tokens
  Reserved (output buffer + safety margin):              ____ tokens
  ──────────────────────────────────────────────────────────────
  Total per-turn floor:                                  ____ tokens
  Budget for conversation: context_window - total floor
```

**Worked example (systems-design bundle composed on foundation):**

| Component | Tokens | Source |
|-----------|--------|--------|
| Foundation context files (15+ files) | ~12,000 | `delegation-instructions.md`, `multi-agent-patterns.md`, `AWARENESS_INDEX.md`, philosophy files, etc. |
| Systems-design content files (5 files) | ~4,000 | `instructions.md`, `system-design-principles.md`, `tradeoff-frame.md`, etc. |
| Skills L1 visibility (~20 skills) | ~1,000 | One-line descriptions of all composed skills |
| Active mode body (when in `/systems-design`) | ~1,000 | Mode markdown injected ephemerally |
| Hook injections (status, design context) | ~300 | Ephemeral per-turn injections |
| Reserved | ~6,000 | Output buffer + safety margin |
| **Total per-turn floor** | **~24,300** | **12% of a 200K window consumed before conversation starts** |

**Compaction reality:** 87% of sessions trigger compaction, and 66% reach strategy level 5+ (aggressive message and tool result pruning). The higher your floor, the sooner compaction fires. Agents with heavy @-mentioned documentation can hit maximum compaction in as few as 8 turns.

**When reviewing a mechanism spec:** If the floor exceeds 15-20% of the context window, audit which content files could be skills or agent @mentions.

### Context Lifecycle

Beyond token cost, the lifecycle question is: **does this context need to persist, or can it be thrown away?**

| Lifecycle | Where it lives | When it disappears | Mechanisms |
|-----------|---------------|-------------------|------------|
| **Persistent** | System prompt or message history | Never (system prompt) or when compacted (history) | Bundle-level content files (system prompt, immune); tool results and user-supplied @mentions (history, compactable); non-ephemeral hook injections (history, compactable) |
| **Ephemeral** | Appended per-turn, not stored | After each LLM call | Mode body, skills visibility, ephemeral hook injections |
| **Disposable** | Isolated child session | When child session completes | Agents, recipe steps, fork skills |

**The disposable context pattern.** The most powerful context management tool is **throwing context away**. When work runs in an agent, recipe step, or fork skill, the entire working context is discarded when the task completes. Only the distilled output survives. The parent doesn't need to know *how* the agent explored 20 files — it needs the *conclusion*.

**Default to clean slate.** Use `context_depth: "none"` for delegations unless the agent genuinely needs conversational context. 84% of all delegations use clean-slate — the ecosystem has converged on this as the norm. Use `"recent"` only when the agent needs to understand what the user asked for. Use `"all"` almost never.

**Choosing the right lifecycle:**

| Question | If yes → | If no → |
|----------|----------|---------|
| Must the LLM see this on every single turn? | Persistent (content file) | Something cheaper |
| Is it phase-specific guidance? | Ephemeral (mode) | Persistent or disposable |
| Is it one-time reference material? | Disposable (skill load, compactable) | Content file |
| Is it heavy exploration producing a summary? | Disposable (agent) | Root session tool calls |
| Does the user need to see intermediate steps? | Persistent (root session) | Disposable (agent) |

**Session length impact.** Over a 50-turn session:
- **Persistent content file (2K tokens):** 2K every turn = 100K total (never recovered)
- **Ephemeral mode (2K tokens, active 10 turns):** 20K total, then zero
- **Disposable agent delegations (10 delegations):** ~4K in parent (compactable)

Sessions that rely on disposable context stay lean longer than sessions that accumulate persistent context.

---

## Parallelism and Concurrency

### Two Tiers of Parallelism

**Tool parallelism (~24% of LLM responses):** Routine and automatic. The LLM emits multiple tool calls in a single response — reading several files, running concurrent searches. Requires no special design. Design for this by default.

**Agent parallelism (~6% of delegate responses):** Deliberate and high-value. Multiple `delegate()` calls in a single turn. Sessions using parallel agents are 6x longer than sequential-only sessions, reflecting this pattern's use for substantial, decomposable work.

### Where Agent Parallelism Is Available

| Mechanism | Parallel capability | How it works |
|-----------|-------------------|-------------|
| **Agents** | Multiple `delegate()` calls in one turn | Independent sessions, no shared state, results return when all complete |
| **Recipes** | `foreach` steps with items | Each item gets its own step execution, can run concurrently |
| **Fork skills** | Skill body dispatches parallel agents | e.g., adversarial-review spawns 5 agents in parallel |

### When to Parallelize Agents

Parallelism is beneficial when:
- **Tasks are independent.** No data flows between them. Each fully specified upfront.
- **Tasks benefit from isolation.** Different focus areas, expertise, or model roles.
- **Latency matters.** Three parallel agents complete in ~1x time, not ~3x.

Parallelism is harmful when:
- **Results of one task inform another.** Use sequential delegation with `context_scope="agents"`.
- **Tasks need iterative convergence.** Use recipe `while` loops or session resumption.
- **Shared context is needed.** Parallel agents can't see each other's in-progress work.

### Parallel Design Patterns

**Fan-out / fan-in (agents):** Dispatch multiple agents, synthesize results in the parent or a synthesis agent.

```
Parent dispatches 3 explorer agents (parallel)
  → Agent A surveys src/auth/
  → Agent B surveys src/api/
  → Agent C surveys src/models/
Parent (or synthesis agent) combines findings
```

**Parallel review (fork skill):** Sub-agents each evaluate the same artifact from different perspectives.

**Parallel recipe items (foreach):** Each item in its own step session. Useful for batch operations across repos, files, or components.

### Parallelism and Token Cost

Parallel agents multiply *throughput* without multiplying the parent's context cost. Three parallel agents cost the parent ~1,200 tokens total (3 × ~400 each), same as sequential. Wall-clock time is ~1/3.

The trade-off: parallel agents can't share discoveries. For exploratory work where findings compound, sequential delegation with `context_scope="agents"` produces better-informed analysis at higher latency.

---

## Model Selection

Different mechanisms can run on different models — a significant cost, quality, and latency lever.

### Where Model Selection Applies

| Mechanism | Model control | How configured |
|-----------|--------------|---------------|
| **Root session** | Bundle's default model | Provider configuration in bundle YAML |
| **Agent** | `model_role` in agent frontmatter | Declares what kind of model the agent needs |
| **Delegation override** | `model_role` parameter in `delegate()` | Parent overrides child's default |
| **Recipe step** | Inherited from step's agent | Each step runs with its agent's model role |
| **Fork skill** | `model_role` in skill metadata | Fork session uses the specified model |

### Matching Models to Tasks

| Task character | Model role | Typical tier |
|---------------|-----------|-------------|
| Deep design, architecture, complex reasoning | `reasoning` | Heavy (Opus-class) |
| Finding flaws, critical evaluation | `critique` | Mid + high reasoning |
| Code generation, debugging | `coding` | Mid (Sonnet-class) |
| Long-form prose, documentation | `writing` | Heavy (Opus-class) |
| File operations, git commands, triage | `fast` | Cheap (Haiku-class) |
| General-purpose, varied work | `general` | Mid (Sonnet-class) |

### Model Selection as a Design Decision

When designing a bundle's agents, model roles define the cost and capability profile. A bundle that routes an explorer agent to `fast` instead of `general` might pay 1/10th the cost per delegation.

**Model routing is actively used.** ~80% of sessions specify model roles. `fast` (~45%) and `general` (~38%) dominate, with specialized roles handling ~17%. Complex sessions routinely dispatch 3 model tiers within a single workflow.

**Fallback chains** provide resilience — most specific role first, falling back to more available options. Always end with `general` or `fast`:

| Role | Recommended chain | Rationale |
|------|------------------|-----------|
| `reasoning` | `[reasoning, general]` | Falls back to capable mid-tier |
| `critique` | `[critique, reasoning, general]` | Critique benefits from reasoning as second choice |
| `coding` | `[coding, general]` | Most providers have code-capable models |
| `ui-coding` | `[ui-coding, coding, general]` | Spatial reasoning → code → general |
| `writing` | `[writing, creative, general]` | Long-form → creative → general |
| `creative` | `[creative, general]` | Aesthetic judgment → general |
| `security-audit` | `[security-audit, critique, general]` | Security → analytical evaluation → general |
| `research` | `[research, general]` | Extended context → general |
| `fast` | `fast` | Terminal — fast agents rarely need fallback |
| `image-gen` | `[image-gen, creative, general]` | Sparse provider coverage; always needs fallback |

### Model Selection and Parallelism

Parallel agents can each run on different models:
- Dispatch 3 `fast` explorers to survey different areas (cheap, concurrent)
- Feed results to 1 `reasoning` architect for synthesis (expensive, focused)

The expensive model runs once, on distilled input.

### Expected Delegation Distribution

`foundation:explorer` (~43% of delegations) and `foundation:git-ops` (~26%) together account for ~69% of agent dispatch. Custom agents (reviewers, architects, builders) account for ~23%. `self`-delegation for token management is ~8%.

**Design implication:** Optimize for high-frequency patterns first. If your bundle triggers heavy exploration, ensure the explorer's model role is cost-appropriate. If your workflow involves frequent commits, ensure git-ops delegation is frictionless.

---

## Turn Budgets and Bounded Work

Unbounded work is the most common source of runaway cost and quality degradation.

### Where Bounds Apply

| Mechanism | Bound | Effect when hit |
|-----------|-------|----------------|
| **Agent** | `max_turns` in agent config | Agent stops and returns whatever it has |
| **Orchestrator** | `loop_iterations_limit` (per user turn) | System reminder injected telling agent to wrap up |
| **Recipe step** | Step's agent turn limit | Step completes with partial output |
| **Recipe** | `max_iterations` on while loops | Loop exits regardless of condition |

### Why Bounds Matter

An agent with an open-ended instruction will use its entire turn budget exploring diminishing-return areas. Tighter bounds produce more focused work:

| Turn budget | Character of work |
|------------|-------------------|
| 3-5 turns | Focused: read specific files, form opinion, return |
| 10-15 turns | Thorough: explore an area, iterate on findings, synthesize |
| 25+ turns | Exhaustive: deep investigation, multi-approach analysis |

### Designing for Bounded Work

1. **Scope the instruction tightly.** "Survey `src/auth/` and report on authentication patterns" beats "understand the codebase."
2. **Match turn budget to task complexity.** A utility agent needs 3-5 turns. An architect needs 15-20.
3. **Prefer multiple bounded agents over one unbounded agent.** Three 5-turn explorers produce more focused results than one 15-turn explorer, and they can run in parallel.
4. **Recipe steps are naturally bounded.** Each step has a focused instruction and its own turn budget.

### Reference Budgets by Agent Archetype

Starting points, not rules. For calibration: the median session is 8 user turns, 36% are under 5, only 9% exceed 30. An agent with a 20-turn budget consumes more turns than most entire sessions.

| Agent archetype | Suggested turns | Character of work |
|----------------|----------------|-------------------|
| File/utility (explorer, file-ops, git-ops) | 3-8 | Read specific files, form opinion, return |
| Reviewer/critic (spec-reviewer, code-quality) | 8-15 | Analyze an artifact, produce structured assessment |
| Architect/designer (zen-architect, systems-architect) | 12-20 | Multi-step reasoning, consider alternatives, produce spec |
| Writer/documenter (design-writer, technical-writer) | 5-10 | Produce output from already-gathered input |
| Implementer/builder (modular-builder, implementer) | 10-20 | Write code, run tests, iterate on failures |
| Fork skill sub-agents (e.g., adversarial review) | 5-8 | Single-perspective evaluation of a shared artifact |

### Bounds and Context Interaction

An agent with a generous turn budget accumulates tool results, potentially triggering compaction in its own session. This is usually fine — the child's compaction is independent of the parent's. For tasks requiring synthesis of a large body of findings: gather in one agent (or parallel agents), then synthesize in a fresh agent that receives only the summaries.

---

## Human-in-the-Loop Design

Not every mechanism supports human interaction equally.

### Interaction Models by Mechanism

| Mechanism | Human interaction | Timing |
|-----------|------------------|--------|
| **Root session** | Every turn is a human exchange | Continuous |
| **Mode** | User activates/deactivates; agent can suggest transitions | Phase boundaries |
| **Recipe (staged)** | Approval gates between stages | Between workflow phases |
| **Recipe (flat)** | None — fire and forget | N/A |
| **Agent** | None during execution; parent interacts before/after | Before dispatch, after return |
| **Fork skill** | None during execution | N/A |
| **Hook** | Can trigger `confirm` tool policy | Per tool call |

### Checkpoint Placement

**High-value checkpoints** (place these):
- After analysis, before implementation (user validates direction)
- After implementation, before deployment (user validates result)
- At phase transitions (brainstorm → plan → execute → verify)

**Low-value checkpoints** (avoid these):
- Between individual tool calls within a task
- Between recipe steps within a single stage
- For decisions the agent has clear criteria to make

### Modes as Phase Boundaries

Mode transitions from `/brainstorm` to `/write-plan` to `/execute-plan` create deliberate pause points. This is lighter-weight than recipe approval gates and more structured than raw conversation.

**Mode adoption reality.** Mode activation has very low adoption compared to skills — the gate policy (warn on first attempt, requiring retry) creates friction. Skills providing equivalent behavioral guidance are loaded in the vast majority of sessions. **Reserve modes for their structural strength** — tool policies and explicit phase boundaries that change what the session *can do*, not just what it *should do*.

### Recipe Approval Gates

Staged recipes pause between stages and require `approve` or `deny`. Use when:
- Work is expensive and difficult to undo
- The user needs to review an intermediate artifact
- Different stages might be routed to different people

Gates support messages (`approve(message="merge")`) that subsequent stages can read.

### Recipe Denial Handling

Denial is terminal — no built-in retry. Design implications:

- **Design stages so partial completion is safe.** Stage 1 artifacts should be independently useful if stage 2 is denied.
- **Consider a revision pattern.** If denial commonly leads to "try again with adjustments," build the recipe so the denied stage can be re-executed with modified context.
- **Document expected denial outcomes.** State what happens at each gate on denial.

---

## Practical Example: This Bundle

The `systems-design` bundle composes all seven mechanisms:

| Mechanism | Instance | Role |
|-----------|----------|------|
| **Modes** | `system-design`, `design-review` | Enforce read-only tool policy during design; block file writes until design is complete |
| **Recipes** | `architecture-review` (staged, 2 gates), `design-exploration` (parallel foreach), `codebase-understanding` (sequential) | Multi-step design workflows with checkpointing |
| **Agents** | `systems-architect` (reasoning), `systems-design-critic` (critique), `systems-design-writer` (writing) | Isolated sub-sessions with different model roles |
| **Skills** | `adversarial-review` (fork, 5 parallel agents), `tradeoff-analysis`, `architecture-primitives`, domain-type skills | Task expertise loaded on demand |
| **Hooks** | `hooks-mode` (from modes bundle) | Mode enforcement and context injection |
| **Tools** | `tool-mode`, `tool-skills` (via behavior YAML wiring) | LLM-accessible mode switching and skill loading |
| **Content** | `system-design-principles.md`, `structured-design-template.md`, `instructions.md` | Design philosophy and standing orders injected into root session |

**Key design decisions:**

- **Three agent model roles** (`reasoning`, `critique`, `writing`): The workflow moves through distinct cognitive phases with different model requirements.
- **Content files (~4K) kept as always-loaded, not skills:** Agents @mention these in their own sub-sessions, so converting to skills wouldn't reduce child session costs. Trade-off accepted for a design-focused bundle. A general-purpose bundle should convert reference frameworks to skills.
- **`recipes` not listed as safe in design modes:** Structural enforcement preventing the design phase from accidentally launching implementation workflows.
- **Fork skill for adversarial review (not an agent):** Doesn't need persistent state, resumable sessions, or its own tool surface. Lighter-weight infrastructure for the same parallel pattern.
- **Turn budgets (added post-evaluation):** architect ANALYZE 10-15, DESIGN 15-20, critic 10-15, writer 5-8, adversarial sub-agents 5-8.

---

## Mechanism Spec Review Checklist

When reviewing a mechanism spec, verify each of these:

**Mechanism assignment:**
- [ ] Each concern has one primary owner (no duplicated authority)
- [ ] Decision hierarchy applied: policy → mode, workflow → recipe, expertise → skill, reasoning → agent
- [ ] No anti-patterns from the Failure Modes table

**Enforcement levels:**
- [ ] Critical behaviors are structurally enforced (tool policies, hook gating), not just prose
- [ ] Tool policy explicitly names safe, warned, and blocked tools for each mode
- [ ] Composition loopholes assessed: if `delegate` or `recipes` are safe while write tools are blocked, is that intentional?

**Context economics:**
- [ ] Per-turn token floor calculated
- [ ] Floor is below 15-20% of context window
- [ ] Always-loaded content files justified — could any be skills or agent @mentions?
- [ ] Skill compaction risk assessed for critical methodology skills

**Execution design:**
- [ ] Turn budgets specified for all agents and recipe steps
- [ ] Model roles assigned with fallback chains ending in `general` or `fast`
- [ ] Parallelism opportunities identified (independent tasks → parallel agents or foreach)
- [ ] Sequential dependencies identified (findings-compound tasks → sequential with `context_scope="agents"`)

**Human-in-the-loop:**
- [ ] Checkpoints at high-stakes transitions
- [ ] Approval gate denial behavior documented
- [ ] Correct mechanism for frequency: recipe gates (low-frequency, high-stakes), hooks (high-frequency, low-stakes), modes (phase transitions)

---

## Closing

When mechanism choice gets confusing, work through these questions:

> **1. "Where should this attach, and who triggers it?"** — Attach at the highest level where it remains valid. Code-decided → hook. LLM-decided → tool. Policy → mode. Ordering → recipe. Expertise → skill. Reasoning → agent.

> **2. "Should this context persist, or can it be thrown away?"** — Default to disposable. The session stays leaner.

> **3. "What does this cost the context window?"** — Content files are permanent per-turn tax. Skills are on-demand and compactable. Agents are context sinks. Budget per byte for system prompt.

> **4. "Can parts of this work run in parallel?"** — Independent tasks with no shared state → parallel agents. Findings that compound → sequential with `context_scope="agents"`.

> **5. "What model capability does this need?"** — Route utility work to `fast`, deep analysis to `reasoning`, evaluation to `critique`, prose to `writing`.

> **6. "How much work should this be allowed to do?"** — Tighter budgets produce more focused results. Prefer multiple bounded agents over one unbounded agent.

> **7. "Where do humans need to weigh in?"** — High-stakes transitions → recipe gates or mode boundaries. Not between individual tool calls.

Question 1 picks the mechanism. Questions 2-3 shape context management. Questions 4-6 shape execution. Question 7 places human control. These questions interact — an agent (Q1) with disposable context (Q2) on a fast model (Q5) with a tight budget (Q6) in parallel with peers (Q4) is a very different design than a single reasoning-model agent with open scope. Both are valid; the questions force you to make the choice deliberately.
