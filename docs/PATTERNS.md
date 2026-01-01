# Common Patterns

Practical patterns for using Amplifier Foundation.

## Bundle Organization

### Single-File Bundle

For simple configurations:

```markdown
---
bundle:
  name: my-app
  version: 1.0.0

session:
  orchestrator: {module: loop-streaming}
  context: {module: context-simple}

providers:
  - module: provider-anthropic
    source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
---

You are a helpful assistant.
```

### Multi-File Bundle

For bundles with agents and context:

```
my-bundle/
├── bundle.md          # Main configuration
├── agents/
│   ├── bug-hunter.md
│   └── code-reviewer.md
└── context/
    ├── guidelines.md
    └── style.md
```

The bundle can reference these:

```yaml
agents:
  include:
    - bug-hunter
    - code-reviewer

context:
  include:
    - guidelines
    - style
```

## Composition Patterns

### Base + Environment

Layer environment-specific config on a base:

```python
# base.py
base = Bundle(
    name="base",
    session={"orchestrator": "loop-streaming", "context": "context-simple"},
    providers=[{"module": "provider-anthropic", "source": "...", "config": {"default_model": "claude-sonnet-4-5"}}],
)

# dev.py
dev = Bundle(
    name="dev",
    providers=[{"module": "provider-anthropic", "config": {"debug": True}}],
)

# prod.py
prod = Bundle(
    name="prod",
    providers=[{"module": "provider-anthropic", "config": {"timeout": 60}}],
)

# Usage
env = os.getenv("ENV", "dev")
overlay = dev if env == "dev" else prod
bundle = base.compose(overlay)
```

### Includes Chain

Use `includes:` for declarative layering:

```yaml
# base.md
bundle:
  name: base
  version: 1.0.0
session:
  orchestrator: {module: loop-streaming}
providers:
  - module: provider-anthropic
    source: git+...
```

```yaml
# dev.md
bundle:
  name: dev
  version: 1.0.0
includes:
  - ./base.md
providers:
  - module: provider-anthropic
    config: {debug: true}
```

### Feature Bundles

Compose features additively:

```python
# Features as partial bundles
filesystem_tools = Bundle(
    name="filesystem",
    tools=[{"module": "tool-filesystem", "source": "..."}],
)

web_tools = Bundle(
    name="web",
    tools=[{"module": "tool-web", "source": "..."}],
)

# Compose what you need
full = base.compose(filesystem_tools, web_tools)
```

## Provider Patterns

### Multi-Provider Setup

Configure multiple providers for failover:

```yaml
providers:
  - module: provider-anthropic
    source: git+...
    config:
      default_model: claude-sonnet-4-5
      priority: 1

  - module: provider-openai
    source: git+...
    config:
      default_model: gpt-4o
      priority: 2
```

### Mock Provider for Testing

Use mock provider in tests:

```python
test_bundle = Bundle(
    name="test",
    providers=[{
        "module": "provider-mock",
        "source": "git+https://github.com/microsoft/amplifier-module-provider-mock@main",
        "config": {"responses": ["Hello!", "How can I help?"]},
    }],
)
```

## Session Patterns

### Basic Session

Load, prepare, execute:

```python
bundle = await load_bundle("/path/to/bundle.md")
prepared = await bundle.prepare()

async with prepared.create_session() as session:
    response = await session.execute("Hello!")
    print(response)
```

### Multi-Turn Conversation

Session maintains context:

```python
async with prepared.create_session() as session:
    await session.execute("My name is Alice")
    response = await session.execute("What's my name?")
    # Response knows about Alice
```

### Resuming Sessions

Resume from session ID:

```python
# First session
async with prepared.create_session() as session:
    await session.execute("Remember: X=42")
    session_id = session.session_id

# Later: resume
async with prepared.create_session(session_id=session_id) as session:
    response = await session.execute("What is X?")
    # Knows X=42
```

## Agent Patterns

### Defining Agents

In `agents/bug-hunter.md`:

```markdown
---
agent:
  name: bug-hunter
  description: Finds and fixes bugs

providers:
  - module: provider-anthropic
    config:
      default_model: claude-sonnet-4-5
      temperature: 0.3  # More deterministic
---

You are an expert bug hunter. Find bugs systematically.
```

### Spawning Agents

```python
# Load agent as bundle
agent_bundle = await load_bundle("./agents/bug-hunter.md")

# Spawn sub-session
result = await prepared.spawn(
    child_bundle=agent_bundle,
    instruction="Find the bug in auth.py",
    compose=True,            # Compose with parent bundle (default: True)
    parent_session=session,  # Inherit UX from parent
    session_id=None,         # Or provide ID to resume existing sub-session
)

# Returns: {"output": response, "session_id": child_id}
print(result["output"])
```

### Controlling Agent Tool Inheritance

By default, spawned agents inherit all tools from their parent. Configure tool inheritance
in the task tool's config section:

```yaml
# In your bundle.md
tools:
  - module: tool-task
    source: git+https://github.com/microsoft/amplifier-module-tool-task@main
    config:
      exclude_tools: [tool-task]  # Agents inherit all EXCEPT these
```

Or specify an explicit allowlist:

```yaml
tools:
  - module: tool-task
    config:
      inherit_tools: [tool-filesystem, tool-bash]  # Agents get ONLY these
```

**Common pattern**: Prevent agents from delegating further:

```yaml
# Coordinator has task tool for orchestration
tools:
  - module: tool-filesystem
  - module: tool-bash
  - module: tool-task
    config:
      exclude_tools: [tool-task]  # Spawned agents can't delegate
```

This ensures agents do the work themselves rather than trying to spawn sub-agents.

**Design rationale**: Tool inheritance config belongs in tool-task's config section because:
- The task tool is the module that consumes this config
- Follows the pattern of all other tool configs
- Without tool-task mounted, this config is meaningless
- Standard module-config merge rules apply during bundle composition

### Agent Resolution Pattern

Apps typically resolve agent names to bundles:

```python
async def resolve_agent(name: str, agent_configs: dict) -> Bundle:
    """Resolve agent name to Bundle."""
    if name in agent_configs:
        # Inline agent definition
        return Bundle.from_dict(agent_configs[name])

    # Look for agent file
    agent_path = find_agent_file(name)
    if agent_path:
        return await load_bundle(agent_path)

    raise ValueError(f"Unknown agent: {name}")

# Usage
agent_bundle = await resolve_agent("bug-hunter", bundle.agents)
result = await prepared.spawn(agent_bundle, instruction)
```

## Validation Patterns

### Validate Before Use

```python
from amplifier_foundation import load_bundle, validate_bundle_or_raise

bundle = await load_bundle(path)
validate_bundle_or_raise(bundle)  # Raises if invalid
prepared = await bundle.prepare()
```

### Validate Completeness

For bundles that should be directly mountable:

```python
from amplifier_foundation import BundleValidator

validator = BundleValidator()
result = validator.validate_completeness(bundle)

if not result.valid:
    print("Missing required sections:")
    for error in result.errors:
        print(f"  - {error}")
```

### Custom Validation

Extend validator for app-specific rules:

```python
class AppValidator(BundleValidator):
    def validate(self, bundle):
        result = super().validate(bundle)

        # Custom rule: must have filesystem tool
        has_filesystem = any(
            t.get("module") == "tool-filesystem"
            for t in bundle.tools
        )
        if not has_filesystem:
            result.add_warning("Missing filesystem tool")

        return result
```

## Registry Patterns

### Named Bundles

Register commonly-used bundles:

```python
registry = BundleRegistry()
registry.register("foundation", "git+https://github.com/microsoft/amplifier-foundation-bundle@main")
registry.register("dev", "./bundles/dev.md")

# Load by name
bundle = await registry.load("foundation")
```

### Bundle Discovery

Auto-register bundles from directory:

```python
from pathlib import Path

def register_bundles(registry: BundleRegistry, directory: Path):
    for path in directory.glob("*.md"):
        name = path.stem  # filename without extension
        registry.register(name, str(path))

# Register all bundles in directory
register_bundles(registry, Path("./bundles"))
```

## Error Handling

### Graceful Loading

```python
from amplifier_foundation import (
    load_bundle,
    BundleNotFoundError,
    BundleLoadError,
    BundleValidationError,
)

try:
    bundle = await load_bundle(path)
except BundleNotFoundError:
    print(f"Bundle not found: {path}")
except BundleLoadError as e:
    print(f"Failed to load bundle: {e}")
except BundleValidationError as e:
    print(f"Invalid bundle: {e}")
```

### Fallback Bundles

```python
async def load_with_fallback(primary: str, fallback: str) -> Bundle:
    try:
        return await load_bundle(primary)
    except BundleError:
        return await load_bundle(fallback)
```

## Performance Patterns

### Cache Resolved Bundles

```python
from amplifier_foundation import DiskCache, BundleRegistry
from pathlib import Path

# DiskCache: Apps decide the cache location
cache_dir = Path("~/.myapp/cache/bundles").expanduser()
cache = DiskCache(cache_dir)

# BundleRegistry uses ~/.amplifier by default, or specify custom home
registry = BundleRegistry(home=Path("~/.myapp").expanduser())

# First load: downloads from git
bundle = await registry.load("git+https://...")

# Subsequent loads: uses cache
bundle = await registry.load("git+https://...")
```

### Prepare Once, Execute Many

```python
# Prepare once (downloads modules)
bundle = await load_bundle(path)
prepared = await bundle.prepare()

# Execute many times
for prompt in prompts:
    async with prepared.create_session() as session:
        response = await session.execute(prompt)
```
