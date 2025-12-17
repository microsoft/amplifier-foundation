# Amplifier Foundation Examples

Progressive examples demonstrating how to use Amplifier Foundation, from basic concepts to sophisticated applications.

## Quick Start

```bash
# Set your API key
export ANTHROPIC_API_KEY='your-key-here'

# Run any example
cd amplifier-foundation
uv run python examples/01_hello_world.py
```

## Examples Overview

### ✨ Tier 1: Quick Start (01-03)

Get running immediately with minimal code.

**[01_hello_world.py](./01_hello_world.py)** - Your first AI agent  
**Value:** Get running in 2 minutes with minimal code  
The simplest possible Amplifier agent. Load foundation, compose with provider, execute a prompt.

**[02_custom_configuration.py](./02_custom_configuration.py)** - Tailor agents via composition  
**Value:** Composition over configuration - swap capabilities, not flags  
Add tools, use streaming orchestrators, and customize behavior by composing different modules.

**[03_custom_tool.py](./03_custom_tool.py)** - Build domain-specific capabilities  
**Value:** Extend Amplifier with your own tools  
Build custom tools (WeatherTool, DatabaseTool) that integrate seamlessly. Learn the Tool protocol.

### 🔧 Tier 2: Foundation Concepts (04-07)

Understand the core mechanisms of Amplifier Foundation.

**[04_load_and_inspect.py](./04_load_and_inspect.py)** - Load and inspect bundles  
Learn how `load_bundle()` works and what a bundle contains. See the mount plan structure.

**[05_composition.py](./05_composition.py)** - Bundle composition and merge rules  
Understand how `compose()` merges configuration. See how session, providers, tools, and instruction fields combine.

**[06_sources_and_registry.py](./06_sources_and_registry.py)** - Loading from remote sources  
Learn source formats (git, file, package). Use BundleRegistry for named bundle management.

**[07_full_workflow.py](./07_full_workflow.py)** - Complete workflow with execution  
See the full flow: `prepare()` → `create_session()` → `execute()`. Interactive demo with provider selection.

### 🏗️ Tier 3: Building Applications (08-10)

Production-quality patterns for real applications.

**[08_cli_application.py](./08_cli_application.py)** - CLI application architecture  
**Value:** Best practices for building real tools  
Application architecture patterns: configuration, logging, error handling, lifecycle management.

**[09_multi_agent_system.py](./09_multi_agent_system.py)** - Coordinate specialized agents  
**Value:** Build sophisticated systems with agent workflows  
Create specialized agents with different tools and instructions. Sequential workflows and context passing.

**[10_provider_comparison.py](./10_provider_comparison.py)** - Provider comparison & model selection  
**Value:** Trivially easy provider swapping  
See how to compare providers and make cost/performance tradeoffs.

### 🎯 Tier 4: Domain Applications (11-13)

Apply Amplifier to specific use cases.

**[11_meeting_notes_to_actions.py](./11_meeting_notes_to_actions.py)** - Meeting notes → action items  
**Audience:** Everyone - PMs, designers, developers  
Transform unstructured meeting notes into organized task lists.

**[12_calendar_assistant.py](./12_calendar_assistant.py)** - Calendar integration  
**Audience:** Everyone  
Automate meeting scheduling with natural language and calendar APIs.

**[13_github_actions_ci.py](./13_github_actions_ci.py)** - GitHub Actions CI/CD  
**Audience:** DevOps engineers  
Integrate Amplifier into automated workflows, code review, test analysis.

### ⚡ Tier 5: Advanced Techniques (14-18)

Power user patterns for production systems.

**[14_approval_gates.py](./14_approval_gates.py)** - Approval gates in action  
Safety controls and human-in-the-loop capability with ApprovalProvider protocol.

**[15_session_persistence.py](./15_session_persistence.py)** - Session persistence & resume  
Persist conversation history, resume sessions, maintain context across restarts.

**[16_event_debugging.py](./16_event_debugging.py)** - Event-driven debugging  
Complete observability of event flow, debugging techniques for complex agents.

**[17_custom_hooks.py](./17_custom_hooks.py)** - Custom hook library  
Performance monitoring, cost tracking, audit logging, error handling patterns.

**[18_multi_model_ensemble.py](./18_multi_model_ensemble.py)** - Multi-model ensemble  
Consensus voting, cost cascading, intelligent routing across multiple models.

## Learning Paths

### 🚀 For Complete Beginners (15 minutes)
Start here to understand Amplifier basics:
1. **01_hello_world.py** - See it work immediately (2 min)
2. **02_custom_configuration.py** - Understand composition (5 min)
3. **03_custom_tool.py** - Build your first custom capability (10 min)

### 🔧 For Understanding Internals (30 minutes)
Deep dive into how Amplifier works:
1. **04_load_and_inspect.py** - Bundle structure
2. **05_composition.py** - Merge rules and composition
3. **06_sources_and_registry.py** - Module resolution
4. **07_full_workflow.py** - Complete preparation and execution flow

### 🏗️ For Building Production Apps (1 hour)
Learn patterns for production-quality applications:
1. **Tier 2 (04-07)** - Understand foundation concepts first
2. **08_cli_application.py** - Application architecture
3. **09_multi_agent_system.py** - Complex multi-agent systems
4. **10_provider_comparison.py** - Model selection strategies

### 🎯 For Domain-Specific Use Cases (30-45 min each)
Pick your domain:
- **11_meeting_notes_to_actions.py** - Productivity (everyone)
- **12_calendar_assistant.py** - Business automation (everyone)
- **13_github_actions_ci.py** - DevOps & CI/CD (engineers)

### ⚡ For Advanced Patterns (2+ hours)
Power user techniques for production systems:
- **14_approval_gates.py** - Safety controls & human-in-the-loop
- **15_session_persistence.py** - Stateful workflows
- **16_event_debugging.py** - Observability & debugging
- **17_custom_hooks.py** - Production monitoring patterns
- **18_multi_model_ensemble.py** - Advanced orchestration

## Key Concepts Demonstrated

### Bundles
Composable configuration units that produce mount plans for AmplifierSession. A bundle specifies which modules to load, how to configure them, and what instructions to provide.

```python
bundle = Bundle(
    name="my-agent",
    providers=[...],  # LLM backends
    tools=[...],      # Capabilities
    hooks=[...],      # Observability
    instruction="..." # System prompt
)
```

### Composition
Combine bundles to create customized agents. Later bundles override earlier ones, allowing progressive refinement.

```python
foundation = await load_bundle("foundation")
custom = Bundle(name="custom", tools=[...])
composed = foundation.compose(custom)  # custom overrides foundation
```

### Preparation
Download and activate all modules before execution. The `prepare()` method resolves module sources (git URLs, local paths) and makes them importable.

```python
prepared = await composed.prepare()  # Downloads modules if needed
session = await prepared.create_session()
```

### Module Sources
Specify where to download modules from. Every module needs a `source` field for `prepare()` to resolve it.

```python
tools=[
    {
        "module": "tool-filesystem",
        "source": "git+https://github.com/microsoft/amplifier-module-tool-filesystem@main"
    }
]
```

### Tool Protocol
Custom tools implement a simple protocol - no inheritance required:

```python
class MyTool:
    @property
    def name(self) -> str:
        return "my-tool"
    
    @property
    def description(self) -> str:
        return "What this tool does..."
    
    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "param": {"type": "string"}
            },
            "required": ["param"]
        }
    
    async def execute(self, input: dict) -> ToolResult:
        return ToolResult(success=True, output="result")
```

## Common Patterns

### Pattern: Hello World
The minimal Amplifier application:

```python
foundation = await load_bundle(foundation_path)
provider = await load_bundle(provider_path)
composed = foundation.compose(provider)
prepared = await composed.prepare()
session = await prepared.create_session()

async with session:
    response = await session.execute("Your prompt")
```

### Pattern: Adding Tools
Compose tools into your agent:

```python
tools = Bundle(
    name="tools",
    tools=[
        {"module": "tool-filesystem", "source": "git+https://..."},
        {"module": "tool-bash", "source": "git+https://..."},
    ]
)
composed = foundation.compose(provider).compose(tools)
```

### Pattern: Custom Tool
Register custom tools after session creation:

```python
# After session is created
await session.coordinator.mount("tools", MyTool(), name="my-tool")

# Then use in session
async with session:
    response = await session.execute("Use my custom tool")
```

### Pattern: Multi-Agent
Sequential agent workflow:

```python
# Agent 1: Design
architect = foundation.compose(provider).compose(architect_config)
prepared1 = await architect.prepare()
session1 = await prepared1.create_session()
async with session1:
    design = await session1.execute("Design the system")

# Agent 2: Implement (uses Agent 1 output)
implementer = foundation.compose(provider).compose(implementer_config)
prepared2 = await implementer.prepare()
session2 = await prepared2.create_session()
async with session2:
    code = await session2.execute(f"Implement: {design}")
```

## Troubleshooting

### "Module not found" Error
Modules need `source` fields so `prepare()` can download them:
```python
{"module": "tool-bash", "source": "git+https://..."}
```

### First Run Takes 30+ Seconds
This is normal - modules are downloaded from GitHub and cached in `~/.amplifier/modules/`. Subsequent runs are fast.

### "API key error"
Set your provider's API key:
```bash
export ANTHROPIC_API_KEY='your-key-here'
# or
export OPENAI_API_KEY='your-key-here'
```

### Path Issues
Examples assume you're running from the `amplifier-foundation` directory:
```bash
cd amplifier-foundation
uv run python examples/XX_example.py
```

If path errors occur, check that `Path(__file__).parent.parent` resolves to the amplifier-foundation directory.

## Architecture Principles

### Composition Over Configuration
Amplifier favors swapping modules over toggling flags. Want streaming? Use `orchestrator: loop-streaming`. Want different tools? Compose a different tool bundle. No complex configuration matrices.

### Protocol-Based
Tools, providers, hooks, and orchestrators implement protocols (duck typing), not base classes. No framework inheritance required - just implement the interface.

### Explicit Sources
Module sources are explicit in configuration. No implicit discovery or magic imports. If you need a module, specify where it comes from: git repository, local path, or package name.

### Preparation Phase
Modules are resolved and downloaded before execution (`prepare()`), not during runtime. This ensures deterministic behavior and clear error messages.

## Next Steps

- **Read the docs:** [amplifier-foundation documentation](../docs/)
- **Explore modules:** Check out pre-built modules on GitHub
- **Build your own:** Use 07_custom_tool.py as a template for custom capabilities
- **Study patterns:** 08_cli_application.py shows application architecture best practices

## Getting Help

- **GitHub Issues:** [Report bugs or ask questions](https://github.com/microsoft/amplifier-foundation/issues)
- **Discussions:** Share your use cases and get help from the community
- **Documentation:** Read the [full documentation](../docs/) for detailed API reference
