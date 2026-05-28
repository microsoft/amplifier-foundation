# Building Eval Bundles from Strategies

## How to Run

Say what you want. The recipe handles the rest.

**In a session:**
```
"Create a bundle for strategy X"
"Build an eval bundle for the critic-pipeline strategy"
"I want to use a parallel-specialists approach — build a bundle for it"
```

**Or run the recipe directly:**
```
recipes(operation="execute",
  recipe_path="context/recipes/strategy-to-bundle.yaml",
  context={
    "strategy_name": "my-strategy",
    "strategy_description": "One paragraph describing the strategy and how agents interact.",
    "output_dir": "bundles",
    "repo_root": ".",
    "domain_context_path": "context/bundle-creation-process.md",
    "token_target": "20000"
  })
```

**What happens:**
1. An LLM reads your strategy description and produces a JSON mechanism spec
2. You review and approve the spec (only human step)
3. A mechanical generator writes all bundle files — no LLM involved
4. The fingerprint harness measures actual token cost
5. If over target, the system compresses and re-measures (up to 3 times)
6. You get a complete bundle with verified token count

**Required context variables:**

| Variable | What | Example |
|---|---|---|
| `strategy_name` | kebab-case bundle name | `critic-pipeline` |
| `strategy_description` | 1-3 sentences: what the strategy does, how agents interact | See below |
| `output_dir` | Where to write the bundle | `bundles` |
| `repo_root` | Root of the amplifier-evals repo | `.` |
| `domain_context_path` | Path to this document | `context/bundle-creation-process.md` |
| `token_target` | Max tokens (Anthropic) | `20000` |

**The strategy_description is the critical input.** It should state:
- What agents exist and what each does
- How they interact (sequential pipeline? parallel dispatch? revision loops?)
- What constraints matter (read-only critic, no-tools verifier, etc.)

Example: *"Worker-critic-verifier pipeline. Worker implements changes, critic does read-only adversarial review, verifier runs tests. If critic rejects, worker revises. If verifier fails, cycle restarts. Max 3 revision cycles."*

---

## How This Works

Two things work together to turn a strategy into a production-ready eval bundle:

1. **The recipe** (`strategy-to-bundle.yaml`) — Orchestrates the full pipeline:
   design → generate → test → deliver. Uses an LLM for the design step only.
   Everything else is mechanical. The LLM produces a JSON spec that drives the
   entire bundle: system_prompt, agents, root_tools (tools available to all agents
   at root level), optional_includes (behavior includes beyond the always-free UX
   ones), delegate_config (delegate tuning), and tool_sources (git URLs for every
   tool module). The generator reads this spec and builds the complete bundle —
   no hardcoded tool lists or infrastructure decisions outside the spec.

2. **This document** — Provides domain-specific reference data for eval bundles.
   Validated source URLs, baseline tool catalog, known framework constraints,
   anti-patterns from experience, measurement infrastructure, and YAML templates.
   It is fed to the LLM as domain context during the design step.

**Files:**

| File | What | Where |
|---|---|---|
| Recipe | `strategy-to-bundle.yaml` | `context/recipes/` |
| Generator | `generate_bundle.py` | `context/recipes/` |
| Domain context | `bundle-creation-process.md` | `context/` (this file) |

---

## Domain Context for bundle-design-expert

Feed this section to the expert along with the strategy thesis. It contains
the domain constraints the expert needs to make good decisions for coding eval
bundles.

### Available Capabilities

Every production eval bundle needs these capabilities. The expert decides WHERE
they go (root_tools vs agent-scoped tools) and HOW they're configured — and
declares this in the JSON spec via `root_tools`, `optional_includes`,
`delegate_config`, and per-agent `tools`. The expert must also provide
`tool_sources` mapping every module name (from root_tools AND agent tools) to its
git source URL from the Reference Catalog below. eval = production: if it's not
in the eval bundle, it won't be in production.

| Capability | Module | Why baseline |
|---|---|---|
| File read/write/edit | tool-filesystem | Core coding capability |
| Command execution | tool-bash | Run tests, builds, scripts |
| Codebase search | tool-search | grep/glob navigation |
| Web access | tool-web | API docs, error lookup, external references |
| Task tracking | tool-todo | Prevents forgetting steps in multi-step work |
| Sub-agent dispatch | tool-delegate | Context isolation, parallelism, role separation |
| Multi-file edits | apply-patch behavior | Refactoring, coordinated changes |
| Methodology skills | tool-skills | On-demand TDD, debugging, verification |

**REQUIRED for multi-agent strategies:** Any strategy that defines 2+ agents MUST
include `tool-delegate` in `root_tools`. Without it, the root session cannot
dispatch sub-agents. This is not optional — a bundle with agents but no
tool-delegate is broken.

### Token Optimization Constraints

- **Build from zero.** Do not subtract from Foundation. Foundation is 69,704
  tokens (Anthropic). Custom bundles target 10,000–20,000.
- **System prompt paid every turn, immune to compaction.** Keep authored content
  under 300 tokens.
- **Agent descriptions embed in delegate schema at runtime.** Every registered
  agent's description costs tokens on every turn. Under 50 words each.
- **Skills visibility injects ~1,500 tokens per turn** listing all available
  skills. Set `visibility.enabled: false`. Agents discover via
  `load_skill(list=true)`.
- **`behaviors:` shorthand resolves through Foundation**, dragging ~55,000
  tokens of context. Use `includes:` with explicit git subdirectory URLs.
- **Bare `module:` names without `source:`** depend on registry resolution and
  fail in DTU measurement environments. Always include both.

### Known Framework Constraints

These affect mechanism design decisions:

1. **`exclude_tools` is global.** tool-delegate's `exclude_tools` applies to
   ALL sub-agents equally. No per-agent tool exclusion.

2. **Agent `tools:` adds, never replaces.** An agent's frontmatter `tools:`
   block adds to inherited root tools. Cannot strip inherited tools from a
   specific agent.

3. **Tool schemas are atomic.** Including tool-filesystem means accepting
   read_file + write_file + edit_file + glob together (~2,000 tokens). Cannot
   include one without the others.

4. **Agent descriptions embed in delegate schema.** Every registered agent's
   `meta.description` is injected into the delegate tool's schema. Every word
   costs tokens on every turn.

5. **Structural enforcement is reliable. Instruction-based enforcement is not.**
   In observed sessions, tool restrictions were enforced every time. Prose
   instructions were skipped under context pressure.

### Anti-Patterns from Experience

| # | Anti-Pattern | What Goes Wrong |
|---|---|---|
| 1 | **Subtraction** | Start with Foundation, remove things. Result is weaker Foundation, not a thesis. |
| 2 | **`behaviors:` shorthand** | Resolves through Foundation, drags ~55K tokens of context. |
| 3 | **Bare `module:` names** | No `source:`. Registry-dependent, fails in DTU. |
| 4 | **Explanation bloat** | Context files teaching how tools/delegation/skills work. Mechanisms self-document via schemas. |
| 5 | **"Add it later"** | Exclude tool-web/tool-skills "for eval simplicity." eval = production. |
| 6 | **Instruction-only enforcement** | Critic told "read-only" but inherits write tools. Structural or flag it. |
| 7 | **System prompt fiction** | Claims capabilities the bundle doesn't provide. Audit every sentence against actual mechanisms. |
| 8 | **Lazy agent copy** | Copy Foundation agents unchanged. Different thesis = different agents. |
| 9 | **Cargo-culting** | Reshape strategy to look like a reference bundle. |
| 10 | **No measurement** | Claim "minimal" without running harness. |
| 11 | **Tactical reaction** | Make changes as reaction to feedback without understanding root cause. |

---

## Reference Catalog

After the expert produces a mechanism specification, use this catalog to
implement it as YAML.

### Tool Sources

| Module | Source URL |
|---|---|
| tool-filesystem | `git+https://github.com/microsoft/amplifier-module-tool-filesystem@main` |
| tool-bash | `git+https://github.com/microsoft/amplifier-module-tool-bash@main` |
| tool-search | `git+https://github.com/microsoft/amplifier-module-tool-search@main` |
| tool-web | `git+https://github.com/microsoft/amplifier-module-tool-web@main` |
| tool-todo | `git+https://github.com/microsoft/amplifier-module-tool-todo@main` |
| tool-delegate | `git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/tool-delegate` |
| tool-skills | `git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=modules/tool-skills` |
| tool-mode | `git+https://github.com/microsoft/amplifier-bundle-modes@main#subdirectory=modules/tool-mode` |
| tool-recipes | `git+https://github.com/microsoft/amplifier-bundle-recipes@main#subdirectory=modules/tool-recipes` |

### Behavior Includes

Use `includes:` with explicit git subdirectory URLs:

```yaml
includes:
  # Multi-file edit capability
  - bundle: git+https://github.com/microsoft/amplifier-bundle-filesystem@main#subdirectory=behaviors/apply-patch.yaml
  # Free-cost UX hooks
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=behaviors/streaming-ui.yaml
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=behaviors/status-context.yaml
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=behaviors/redaction.yaml
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=behaviors/logging.yaml
```

### Validated Configs

**tool-skills** (visibility disabled, skills available on demand):
```yaml
- module: tool-skills
  source: git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=modules/tool-skills
  config:
    skills:
      - "git+https://github.com/microsoft/amplifier-bundle-skills@main#subdirectory=skills"
    visibility:
      enabled: false
```

**tool-delegate** (self-delegation, session resume, context inheritance):
```yaml
- module: tool-delegate
  source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/tool-delegate
  config:
    features:
      self_delegation:
        enabled: true
      session_resume:
        enabled: true
      context_inheritance:
        enabled: true
        max_turns: 10
      provider_selection:
        enabled: true
    settings:
      exclude_tools: [tool-delegate]
```

### Hook Sources

| Hook | Source URL |
|---|---|
| hooks-todo-reminder | `git+https://github.com/microsoft/amplifier-module-hooks-todo-reminder@main` |
| hooks-todo-display | `git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/hooks-todo-display` |
| hooks-session-naming | `git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/hooks-session-naming` |
| hooks-approval | `git+https://github.com/microsoft/amplifier-module-hooks-approval` |
| hooks-mode | `git+https://github.com/microsoft/amplifier-bundle-modes@main#subdirectory=modules/hooks-mode` |

**Standard hook configs:**
```yaml
hooks:
  - module: hooks-todo-reminder
    source: git+https://github.com/microsoft/amplifier-module-hooks-todo-reminder@main
    config:
      inject_role: user
      priority: 10
  - module: hooks-todo-display
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/hooks-todo-display
    config:
      show_progress_bar: true
      show_border: true
  - module: hooks-session-naming
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/hooks-session-naming
    config:
      initial_trigger_turn: 2
      update_interval_turns: 5
  - module: hooks-approval
    source: git+https://github.com/microsoft/amplifier-module-hooks-approval
    config:
      rules: []
      default_action: continue
      policy_driven_only: true
```

### Session Configuration

```yaml
session:
  raw: true
  orchestrator:
    module: loop-streaming
    source: git+https://github.com/microsoft/amplifier-module-loop-streaming@main
    config:
      extended_thinking: true
  context:
    module: context-simple
    source: git+https://github.com/microsoft/amplifier-module-context-simple@main
    config:
      max_tokens: 300000
      compact_threshold: 0.8
      auto_compact: true
```

### File Structure

```
<bundle-name>/
  bundle.md           # Entry point — tools, includes, agents, hooks (spec-driven)
  context/
    system.md         # Strategy-specific system prompt (from spec)
  agents/
    <agent1>.md       # One file per agent (from spec)
    <agent2>.md
  modules/            # Only if strategy requires custom hooks
    <hook-name>/
```

The generator builds `bundle.md` from three sources:
1. **Boilerplate** — session config, UX behavior includes, hooks (fixed infrastructure)
2. **Spec root_tools** — tools: block built from `root_tools` + `tool_sources`
3. **Spec optional_includes** — behavior includes like apply-patch added from `optional_includes`

---

## Measurement

Estimation is not measurement. Run the fingerprint harness.

### Setup

```bash
cd <bundle-directory>
git init && git add . && git commit -m "init"
```

The harness uses `git archive HEAD` — only committed files are measured.

### Create Measurement YAMLs

Create 3 files in `fingerprints/measurements/` — one per provider. Copy an
existing measurement YAML and change `name`, `bundle_name`, and `bundle`
fields. Provider/model pairs are locked in `context/eval-models.md`.

### Run

```bash
./fingerprints/run.sh baseline-<bundle>-anthropic
./fingerprints/run.sh baseline-<bundle>-openai
./fingerprints/run.sh baseline-<bundle>-gemma4
```

### Read Results

The metric is `prompt_tokens_unique_total` from each fingerprint JSON — the
provider's own tokenizer counting the actual system prompt + tool schemas +
injected context from a real session inside an isolated DTU container.

### Baselines

| Bundle | Anthropic | OpenAI | Gemma4 |
|---|---|---|---|
| Foundation | 69,704 | 39,519 | 47,455 |
| Behavioral Anchors v2 | 14,357 | 7,997 | 9,584 |
| Critic Pipeline v2 | 12,409 | 7,174 | 7,729 |

**Target: 10,000–20,000 on Anthropic.** If over 20K, likely causes:
1. `behaviors:` shorthand instead of `includes:` with explicit URLs
2. Missing `source:` on tool/hook declarations
3. Context files explaining how mechanisms work
4. Too many tools at root (each schema costs 500–2,000 tokens)

---

## Manual Alternative (Advanced)

The recipe automates the full pipeline. If you need to run steps manually
(e.g., to iterate on the spec interactively), the components are:

1. **Design step** — delegate to an LLM with `strategy_description` +
   this document as domain context. It produces a JSON mechanism spec.
2. **Generate step** — run `generate_bundle.py <spec.json> <boilerplate> <bundle_dir>`
3. **Measure step** — create a measurement YAML, run `./fingerprints/run.sh`
4. **Compare** — check `prompt_tokens_unique_total` against baselines

### Behavioral Model Verification

For non-trivial bundles, generate a behavioral model before writing YAML:

```
recipes(operation="execute",
        recipe_path="@foundation:recipes/spec-to-behavioral-model.yaml",
        context={"spec_path": "<path>", "output_path": "<path>"})
```

This surfaces broken scenarios (like a critic inheriting write tools) before
implementation. The behavioral model is a verification artifact — it exists
to find problems early.
