# Creating Bundles with amplifier-foundation

**Purpose**: Guide for creating bundles to package AI agent capabilities using amplifier-foundation.

---

## What is a Bundle?

A **bundle** is a composable unit of configuration that produces a mount plan for AmplifierSession. Bundles package:

- **Tools** - Capabilities the agent can use
- **Agents** - Sub-agent definitions for task delegation
- **Hooks** - Observability and control mechanisms
- **Providers** - LLM backend configurations
- **Instructions** - System prompts and context
- **Spawn Policy** - Controls what tools spawned agents inherit

Bundles are the primary way to share and compose AI agent configurations.

**Key insight**: Bundles are **configuration**, not Python packages. A bundle repo does not need a root `pyproject.toml`.

---

## The Thin Bundle Pattern (Recommended)

**Most bundles should be thin** - inheriting from foundation and adding only their unique capabilities.

### The Problem

When creating bundles that include foundation, a common mistake is to **redeclare things foundation already provides**:

```yaml
# ❌ BAD: Fat bundle that duplicates foundation
includes:
  - bundle: foundation

session:              # ❌ Foundation already defines this!
  orchestrator:
    module: loop-streaming
    source: git+https://github.com/...
  context:
    module: context-simple

tools:                # ❌ Foundation already has these!
  - module: tool-filesystem
    source: git+https://github.com/...
  - module: tool-bash
    source: git+https://github.com/...

hooks:                # ❌ Foundation already has these!
  - module: hooks-streaming-ui
    source: git+https://github.com/...
```

This duplication:
- Creates maintenance burden (update in two places)
- Can cause version conflicts
- Misses foundation updates automatically

### The Solution: Thin Bundles

A **thin bundle** only declares what it uniquely provides:

```yaml
# ✅ GOOD: Thin bundle inherits from foundation
---
bundle:
  name: my-capability
  version: 1.0.0
  description: Adds X capability

includes:
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main
  - bundle: my-capability:behaviors/my-capability    # Behavior pattern

---

# My Capability

@my-capability:context/instructions.md

---

@foundation:context/shared/common-system-base.md
```

**That's it.** All tools, session config, and hooks come from foundation.

### Exemplar: amplifier-bundle-recipes

See [amplifier-bundle-recipes](https://github.com/microsoft/amplifier-bundle-recipes) for the canonical example:

```yaml
# amplifier-bundle-recipes/bundle.md - Only 14 lines of YAML!
---
bundle:
  name: recipes
  version: 1.0.0
  description: Multi-step AI agent orchestration for repeatable workflows

includes:
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main
  - bundle: recipes:behaviors/recipes
---

# Recipe System

@recipes:context/recipe-instructions.md

---

@foundation:context/shared/common-system-base.md
```

**Key observations**:
- No `tools:`, `session:`, or `hooks:` declarations (inherited from foundation)
- Uses behavior pattern for its unique capabilities
- References consolidated instructions file
- Minimal markdown body

---

## The Behavior Pattern

A **behavior** is a reusable capability add-on that bundles agents + context (and optionally tools/hooks). Behaviors live in `behaviors/` and can be included by any bundle.

### Why Behaviors?

Behaviors enable:
- **Reusability** - Add capability to any bundle
- **Modularity** - Separate concerns cleanly
- **Composition** - Mix and match behaviors

### Behavior File Structure

```yaml
# behaviors/my-capability.yaml
bundle:
  name: my-capability-behavior
  version: 1.0.0
  description: Adds X capability with agents and context

# Optional: Add tools specific to this capability
tools:
  - module: tool-my-capability
    source: git+https://github.com/microsoft/amplifier-bundle-my-capability@main#subdirectory=modules/tool-my-capability

# Declare agents this behavior provides
agents:
  include:
    - my-capability:agent-one
    - my-capability:agent-two

# Declare context files this behavior includes
context:
  include:
    - my-capability:context/instructions.md
```

### Using Behaviors

Include a behavior in your bundle:

```yaml
includes:
  - bundle: foundation
  - bundle: my-capability:behaviors/my-capability   # From same bundle
  - bundle: git+https://github.com/org/bundle@main#subdirectory=behaviors/foo.yaml  # External
```

### Exemplar: recipes behavior

See [amplifier-bundle-recipes/behaviors/recipes.yaml](https://github.com/microsoft/amplifier-bundle-recipes):

```yaml
bundle:
  name: recipes-behavior
  version: 1.0.0
  description: Multi-step AI agent orchestration via declarative YAML recipes

tools:
  - module: tool-recipes
    source: git+https://github.com/microsoft/amplifier-bundle-recipes@main#subdirectory=modules/tool-recipes
    config:
      session_dir: ~/.amplifier/projects/{project}/recipe-sessions
      auto_cleanup_days: 7

agents:
  include:
    - recipes:recipe-author
    - recipes:result-validator

context:
  include:
    - recipes:context/recipe-instructions.md
```

**Key observations**:
- Adds a tool specific to this capability
- Declares the agents this behavior provides
- References consolidated context file
- Can be included by foundation OR any other bundle

---

## Context De-duplication

**Consolidate instructions into a single file** rather than inline in bundle.md.

### The Problem

Inline instructions in bundle.md cause:
- Duplication if behavior also needs to reference them
- Large bundle.md files that are hard to maintain
- Harder to reuse context across bundles

### The Solution: Consolidated Context Files

Create `context/instructions.md` with all the instructions:

```markdown
# My Capability Instructions

You have access to the my-capability tool...

## Usage

[Detailed instructions]

## Agents Available

[Agent descriptions]
```

Reference it from your behavior:

```yaml
# behaviors/my-capability.yaml
context:
  include:
    - my-capability:context/instructions.md
```

And from your bundle.md:

```markdown
---
bundle:
  name: my-capability
includes:
  - bundle: foundation
  - bundle: my-capability:behaviors/my-capability
---

# My Capability

@my-capability:context/instructions.md

---

@foundation:context/shared/common-system-base.md
```

### Exemplar: recipes instructions

See [amplifier-bundle-recipes/context/recipe-instructions.md](https://github.com/microsoft/amplifier-bundle-recipes):
- Single source of truth for recipe system instructions
- Referenced by both `behaviors/recipes.yaml` AND `bundle.md`
- No duplication

---

## Bundle Directory Structure

### Thin Bundle (Recommended)

```
my-bundle/
├── bundle.md                 # Thin: includes + context refs only
├── behaviors/
│   └── my-capability.yaml    # Reusable behavior
├── agents/                   # Agent definitions
│   ├── agent-one.md
│   └── agent-two.md
├── context/
│   └── instructions.md       # Consolidated instructions
├── docs/                     # Additional documentation
├── README.md
├── LICENSE
├── SECURITY.md
└── CODE_OF_CONDUCT.md
```

### Bundle with Local Modules

```
my-bundle/
├── bundle.md
├── behaviors/
│   └── my-capability.yaml
├── agents/
├── context/
├── modules/                  # Local modules (when needed)
│   └── tool-my-capability/
│       ├── pyproject.toml    # Module's package config
│       └── my_module/
├── docs/
├── README.md
└── ...
```

**Note**: No `pyproject.toml` at the root. Only modules inside `modules/` need their own `pyproject.toml`.

---

## Creating a Bundle Step-by-Step

### Step 1: Decide Your Pattern

**Ask yourself**:
- Does my bundle add capability to foundation? → **Use thin bundle + behavior pattern**
- Is my bundle standalone (no foundation dependency)? → Declare everything you need
- Do I want my capability reusable by other bundles? → **Create a behavior**

### Step 2: Create Behavior (if adding to foundation)

Create `behaviors/my-capability.yaml`:

```yaml
bundle:
  name: my-capability-behavior
  version: 1.0.0
  description: Adds X capability

agents:
  include:
    - my-capability:my-agent

context:
  include:
    - my-capability:context/instructions.md
```

### Step 3: Create Consolidated Instructions

Create `context/instructions.md`:

```markdown
# My Capability Instructions

You have access to the my-capability tool for [purpose].

## Available Agents

- **my-agent** - Does X, useful for Y

## Usage Guidelines

[Instructions for the AI on how to use this capability]
```

### Step 4: Create Agent Definitions

Place agent files in `agents/` with proper frontmatter:

```markdown
---
meta:
  name: my-agent
  description: "Description shown when listing agents. Include usage examples..."
---

# My Agent

You are a specialized agent for [specific purpose].

## Your Capabilities

[Agent-specific instructions]
```

### Step 5: Create Thin bundle.md

```markdown
---
bundle:
  name: my-capability
  version: 1.0.0
  description: Provides X capability

includes:
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main
  - bundle: my-capability:behaviors/my-capability
---

# My Capability

@my-capability:context/instructions.md

---

@foundation:context/shared/common-system-base.md
```

### Step 6: Add README and Standard Files

Create README.md documenting:
- What the bundle provides
- The architecture (thin bundle + behavior pattern)
- How to load/use it

---

## Anti-Patterns to Avoid

### ❌ Duplicating Foundation

```yaml
# DON'T DO THIS when you include foundation
includes:
  - bundle: foundation

tools:
  - module: tool-filesystem     # Foundation has this!
    source: git+https://...

session:
  orchestrator:                 # Foundation has this!
    module: loop-streaming
```

**Why it's bad**: Creates maintenance burden, version conflicts, misses foundation updates.

**Fix**: Remove duplicated declarations. Foundation provides them.

### ❌ Inline Instructions in bundle.md

```yaml
---
bundle:
  name: my-bundle
---

# Instructions

[500 lines of instructions here]

## Usage

[More instructions]
```

**Why it's bad**: Can't be reused by behavior, hard to maintain, can't be referenced separately.

**Fix**: Move to `context/instructions.md` and reference with `@my-bundle:context/instructions.md`.

### ❌ Skipping the Behavior Pattern

```yaml
# DON'T DO THIS for capability bundles
---
bundle:
  name: my-capability

includes:
  - bundle: foundation

agents:
  include:
    - my-capability:agent-one
    - my-capability:agent-two
---

[All instructions inline]
```

**Why it's bad**: Your capability can't be added to other bundles without including your whole bundle.

**Fix**: Create `behaviors/my-capability.yaml` with agents + context, then include it.

### ❌ Fat Bundles for Simple Capabilities

```yaml
# DON'T create complex bundles when a behavior would suffice
---
bundle:
  name: simple-feature
  version: 1.0.0

includes:
  - bundle: foundation

session:
  orchestrator: ...    # Unnecessary
  context: ...         # Unnecessary

tools:
  - module: tool-x     # Could be in behavior
    source: ...

agents:
  include:             # Could be in behavior
    - simple-feature:agent-a
---

[Instructions that could be in context/]
```

**Fix**: If you're just adding agents + maybe a tool, use a behavior YAML only.

### ❌ Using @ Prefix in YAML

```yaml
# DON'T DO THIS - @ prefix is for markdown only
context:
  include:
    - "@my-bundle:context/instructions.md"   # ❌ @ doesn't belong here

agents:
  include:
    - "@my-bundle:my-agent"                  # ❌ @ doesn't belong here
```

```yaml
# DO THIS - bare namespace:path in YAML
context:
  include:
    - my-bundle:context/instructions.md      # ✅ No @ in YAML

agents:
  include:
    - my-bundle:my-agent                     # ✅ No @ in YAML
```

**Why it's wrong**: The `@` prefix is markdown syntax for eager file loading. YAML sections use bare `namespace:path` references. Using `@` in YAML causes **silent failure** - the path won't resolve and content won't load, with no error message.

### ❌ Using Repository Name as Namespace

```yaml
# If loading: git+https://github.com/microsoft/amplifier-bundle-recipes@main
# And bundle.name in that repo is: "recipes"

# DON'T DO THIS
agents:
  include:
    - amplifier-bundle-recipes:recipe-author   # ❌ Repo name

# DO THIS
agents:
  include:
    - recipes:recipe-author                    # ✅ bundle.name value
```

**Why it's wrong**: The namespace is ALWAYS `bundle.name` from the YAML frontmatter, regardless of the git URL, repository name, or file path.

### ❌ Including Subdirectory in Paths

```yaml
# If loading: git+https://...@main#subdirectory=bundles/foo
# And bundle.name is: "foo"

# DON'T DO THIS
context:
  include:
    - foo:bundles/foo/context/instructions.md   # ❌ Redundant path

# DO THIS
context:
  include:
    - foo:context/instructions.md               # ✅ Relative to bundle location
```

**Why it's wrong**: When loaded via `#subdirectory=X`, the bundle root IS `X/`. Paths are relative to that root, so including the subdirectory in the path duplicates it.

### ❌ Using context.include in bundle.md Instead of @mentions

```markdown
<!-- DON'T DO THIS in bundle.md -->
---
bundle:
  name: my-bundle
context:                              # ❌ This is for behavior YAML files
  include:
    - my-bundle:context/instructions.md
---

# Instructions here
```

```markdown
<!-- DO THIS in bundle.md -->
---
bundle:
  name: my-bundle
---

# Instructions

@my-bundle:context/instructions.md    # ✅ Use @mention in markdown body
```

**Why it's wrong**: The `context.include` YAML section is the **behavior pattern** - it's meant for `behaviors/*.yaml` files that programmatically inject context into bundles that include them. Main `bundle.md` files should use `@mentions` directly in the markdown body.

---

## Decision Framework

### When to Include Foundation

| Scenario | Recommendation |
|----------|---------------|
| Adding capability to AI assistants | ✅ Include foundation |
| Creating standalone tool | ❌ Don't need foundation |
| Need base tools (filesystem, bash, web) | ✅ Include foundation |
| Building on existing bundle | ✅ Include that bundle |

### When to Use Behaviors

| Scenario | Recommendation |
|----------|---------------|
| Adding agents + context | ✅ Use behavior |
| Adding tool + agents | ✅ Use behavior |
| Want others to use your capability | ✅ Use behavior |
| Creating a simple bundle variant | ❌ Just use includes |

### When to Create Local Modules

| Scenario | Recommendation |
|----------|---------------|
| Tool is bundle-specific | ✅ Keep in `modules/` |
| Tool is generally useful | ❌ Extract to separate repo |
| Multiple bundles need the tool | ❌ Extract to separate repo |

---

## Bundle File Structure

A bundle is a markdown file with YAML frontmatter:

```markdown
---
bundle:
  name: my-bundle
  version: 1.0.0
  description: What this bundle provides

includes:
  - bundle: foundation              # Inherit from other bundles
  - bundle: my-bundle:behaviors/x   # Include behaviors

# Only declare tools NOT inherited from includes
tools:
  - module: tool-name
    source: ./modules/tool-name     # Local path
    config:
      setting: value

# Control what tools spawned agents inherit
spawn:
  exclude_tools: [tool-task]        # Agents inherit all EXCEPT these
  # OR use explicit list:
  # tools: [tool-a, tool-b]         # Agents get ONLY these tools

agents:
  include:
    - my-bundle:agent-name          # Reference agents in this bundle

# Only declare hooks NOT inherited from includes
hooks:
  - module: hooks-custom
    source: git+https://github.com/...
---

# System Instructions

Your markdown instructions here. This becomes the system prompt.

Reference documentation with @mentions:
@my-bundle:docs/GUIDE.md
```

---

## Source URI Formats

Bundles support multiple source formats for modules:

| Format | Example | Use Case |
|--------|---------|----------|
| Local path | `./modules/my-module` | Modules within the bundle |
| Relative path | `../shared-module` | Sibling directories |
| Git URL | `git+https://github.com/org/repo@main` | External modules |
| Git with subpath | `git+https://github.com/org/repo@main#subdirectory=modules/foo` | Module within larger repo |

**Local paths are resolved relative to the bundle's location.**

---

## Composition with includes:

Bundles can inherit from other bundles:

```yaml
includes:
  - bundle: foundation                    # Well-known bundle name
  - bundle: git+https://github.com/...    # Git URL
  - bundle: ./bundles/variant.yaml        # Local file
  - bundle: my-bundle:behaviors/foo       # Behavior within same bundle
```

**Merge rules**:
- Later bundles override earlier ones
- `session`, `providers`, `tools`, `hooks` are deep-merged by module ID
- `agents` are merged by agent name
- `spawn` is deep-merged (later overrides earlier)
- Markdown instructions replace entirely (later wins)

---

## App-Level Runtime Injection

Bundles define **what** capabilities exist. Apps inject **how** they run at runtime.

### What Apps Inject

| Injection | Source | Example |
|-----------|--------|---------|
| Provider configs | `settings.yaml` providers | API keys, model selection |
| Tool configs | `settings.yaml` modules.tools | `allowed_write_paths` for filesystem |
| Session overrides | Session-scoped settings | Temporary path permissions |

### Settings Structure

```yaml
# ~/.amplifier/settings.yaml
providers:
  - module: provider-anthropic
    config:
      api_key: ${ANTHROPIC_API_KEY}

modules:
  tools:
    - module: tool-filesystem
      config:
        allowed_write_paths:
          - /home/user/projects
          - ~/.amplifier
```

Tool configs are **deep-merged by module ID** - your settings extend the bundle's config, not replace it.

### Implications for Bundle Authors

**Don't declare in bundles:**
- Provider API keys or model preferences → App injects from settings
- Environment-specific paths → App injects via tool config
- User preferences → App handles them

**This enables:**
- Same bundle works across environments
- Secrets stay out of version control
- Apps can restrict/expand tool capabilities per context

### The Full Composition Chain

```
Foundation → Your bundle → App settings → Session overrides
    ↓            ↓              ↓               ↓
 (tools)     (agents)     (providers,      (temporary
                          tool configs)     permissions)
```

### Policy Behaviors

Some behaviors are **app-level policies** that should:
- Only apply to root/interactive sessions (not sub-agents or recipe steps)
- Be added by the app, not baked into bundles
- Be configurable per-app context

**Examples of policy behaviors:**
- Notifications (don't notify for every sub-agent)
- Cost tracking alerts
- Session duration limits

**Pattern for bundle authors:**
If your behavior should be a policy (root-only, app-controlled):
1. **Don't include it in your bundle.md** - provide it as a separate behavior
2. **Document it as a policy behavior** - so apps know to compose it
3. **Check `parent_id` in hooks** - skip sub-sessions by default

```python
# In your hook
async def handle_event(self, event: str, data: dict) -> HookResult:
    # Policy behavior: skip sub-sessions
    if data.get("parent_id"):
        return HookResult(action="continue")
    # ... root session logic
```

**Pattern for app developers:**
Configure policy behaviors in `settings.yaml`:

```yaml
config:
  notifications:
    desktop:
      enabled: true
    push:
      enabled: true
      service: ntfy
      topic: "my-topic"
```

The app composes these behaviors onto bundles at runtime, only for root sessions.

For detailed guidance, see [POLICY_BEHAVIORS.md](POLICY_BEHAVIORS.md).

---

## Using @mentions for Context

Reference files in your bundle's instructions without a separate `context:` section:

```markdown
---
bundle:
  name: my-bundle
---

# Instructions

Follow the guidelines in @my-bundle:docs/GUIDELINES.md

For API details, see @my-bundle:docs/API.md
```

**Format**: `@namespace:path/to/file.md`

The namespace is the bundle name. Paths are relative to the bundle root.

### Syntax Quick Reference

There are two different syntaxes for referencing files, and they are **NOT interchangeable**:

| Location | Syntax | Example |
|----------|--------|---------|
| **Markdown body** (bundle.md, agents/*.md) | `@namespace:path` | `@my-bundle:context/guide.md` |
| **YAML sections** (context.include, agents.include) | `namespace:path` (NO @) | `my-bundle:context/guide.md` |

The `@` prefix is **only** for markdown text that gets processed during instruction loading. YAML sections use bare `namespace:path` references.

See [Anti-Patterns to Avoid](#anti-patterns-to-avoid) for common syntax mistakes.

---

## Load-on-Demand Pattern (Soft References)

Not all context needs to load at session start. Use **soft references** (text without `@`) to make content available without consuming tokens until needed.

### The Problem

Every `@mention` loads content eagerly at session creation, consuming tokens immediately:

```
# These ALL load at session start (~15,000 tokens)
# Syntax: @<bundle>:<path>
foundation:docs/BUNDLE_GUIDE.md      # ~5,700 tokens
amplifier:docs/MODULES.md            # ~4,600 tokens  
recipes:examples/code-review.yaml    # ~5,000 tokens
```

*(Prepend `@` to each line above to see actual eager loading)*

### The Solution: Soft References

Reference files by path WITHOUT the `@` prefix. The AI can load them on-demand via `read_file`:

```markdown
**Documentation (load on demand):**
- Schema: recipes:docs/RECIPE_SCHEMA.md
- Examples: recipes:examples/code-review-recipe.yaml
- Guide: foundation:docs/BUNDLE_GUIDE.md
```

The AI sees these references and can load them when actually needed.

### When to Use Each Pattern

| Pattern | Syntax | Loads | Use When |
|---------|--------|-------|----------|
| **@mention** | `@bundle:path` | Immediately | Content is ALWAYS needed |
| **Soft reference** | `bundle:path` (no @) | On-demand | Content is SOMETIMES needed |
| **Agent delegation** | Delegate to expert agent | When spawned | Content belongs to a specialist |

### Best Practice: Context Sink Agents

For heavy documentation, create specialized "context sink" agents that @mention the docs. The root session stays light; heavy context loads only when that agent is spawned.

**Example**: Instead of @mentioning MODULES.md (~4,600 tokens) in the root bundle:

```
# BAD: Heavy root context (in bundle.md)
amplifier:docs/MODULES.md   # <- @mention loads ~4,600 tokens every session
```

Create an expert agent that owns that knowledge:

```
# GOOD: In agents/ecosystem-expert.md (agent owns this knowledge)
amplifier:docs/MODULES.md            # <- @mention here loads only when agent spawns
amplifier:docs/REPOSITORY_RULES.md   # <- same - deferred loading
```

The root bundle uses a soft reference and delegates:

```markdown
# Root bundle.md
For ecosystem questions, delegate to amplifier:amplifier-expert which has
authoritative access to amplifier:docs/MODULES.md and related documentation.
```

### Key Insight

**Every @mention is a token budget decision.** Ask yourself:
- Is this content needed for EVERY conversation? -> @mention
- Is this content needed for SOME conversations? -> Soft reference
- Does this content belong to a specific domain? -> Move to specialist agent


---

## Loading a Bundle

```bash
# Load from local file
amplifier run --bundle ./bundle.md "prompt"

# Load from git URL
amplifier run --bundle git+https://github.com/org/amplifier-bundle-foo@main "prompt"

# Include in another bundle
includes:
  - bundle: git+https://github.com/org/amplifier-bundle-foo@main
```

---

## Best Practices

### Use the Thin Bundle Pattern

When including foundation, don't redeclare what it provides. Your bundle.md should be minimal.

### Create Behaviors for Reusability

Package your agents + context in `behaviors/` so others can include just your capability.

### Consolidate Instructions

Put instructions in `context/instructions.md`, not inline in bundle.md.

### Keep Modules Local When Possible

For bundle-specific tools, keep them in `modules/` within the bundle:
- Simpler distribution (one repo)
- Versioning stays synchronized
- No external dependency management

Extract to separate repo only when:
- Multiple bundles need the same module
- Module needs independent versioning
- Module is generally useful outside the bundle

### Use Descriptive Agent Metadata

The `meta.description` is shown when listing agents. Include:
- What the agent does
- When to use it
- Usage examples in the description string

### No Root pyproject.toml

Bundles are configuration, not Python packages. Don't add a `pyproject.toml` at the bundle root.

---

## Complete Example: amplifier-bundle-recipes

See [amplifier-bundle-recipes](https://github.com/microsoft/amplifier-bundle-recipes) for the canonical example of the thin bundle + behavior pattern:

```
amplifier-bundle-recipes/
├── bundle.md                 # THIN: 14 lines of YAML, just includes
├── behaviors/
│   └── recipes.yaml          # Behavior: tool + agents + context
├── agents/
│   ├── recipe-author.md      # Conversational recipe creation
│   └── result-validator.md   # Pass/fail validation
├── context/
│   └── recipe-instructions.md  # Consolidated instructions
├── modules/
│   └── tool-recipes/         # Local tool implementation
├── docs/                     # Comprehensive documentation
├── examples/                 # Working examples
├── templates/                # Starter templates
├── README.md
└── ...
```

**Key patterns demonstrated**:
- **Thin bundle.md** - Only includes foundation + behavior
- **Behavior pattern** - `behaviors/recipes.yaml` defines the capability
- **Context de-duplication** - Instructions in `context/recipe-instructions.md`
- **Local module** - `modules/tool-recipes/` with source reference
- **No duplication** - Nothing from foundation is redeclared

---

## Troubleshooting

### "Module not found" errors

- Verify `source:` path is correct relative to bundle location
- Check module has `pyproject.toml` with entry point
- Ensure `mount()` function exists in module

### Agent not loading

- Verify `meta:` frontmatter exists with `name` and `description`
- Check agent file is in `agents/` directory
- Verify `agents: include:` uses correct namespace prefix

### @mentions not resolving

- Verify file exists at the referenced path
- Check namespace matches bundle name
- Ensure path is relative to bundle root

### Behavior not applying

- Verify behavior YAML syntax is correct
- Check include path: `my-bundle:behaviors/name` (not `my-bundle:behaviors/name.yaml`)
- Ensure behavior declares `agents:` and/or `context:` sections

---

## Reference

- **[amplifier-bundle-recipes](https://github.com/microsoft/amplifier-bundle-recipes)** - Canonical example of thin bundle + behavior pattern
- **[URI Formats](URI_FORMATS.md)** - Complete source URI documentation
- **[Validation](VALIDATION.md)** - Bundle validation rules
- **[API Reference](API_REFERENCE.md)** - Programmatic bundle loading
