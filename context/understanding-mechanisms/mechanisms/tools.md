# Tools: LLM-Decided Agent Capabilities

## What It Is

A tool is a capability the LLM can choose to invoke during its execution loop.
Tools follow a structural typing protocol -- any Python class with the right shape
(name, description, input_schema, execute) satisfies the `Tool` protocol. The LLM
sees the tool's name, description, and JSON schema, then decides when to call it.

## How It Works

### The Tool Protocol

```python
class Tool(Protocol):
    @property
    def name(self) -> str: ...           # Snake_case, unique

    @property
    def description(self) -> str: ...     # Rich text the LLM reads

    @property
    def input_schema(self) -> dict: ...   # JSON Schema for parameters

    async def execute(self, input: dict) -> ToolResult: ...
```

The `description` is the single most important field -- it's the LLM's only guide
for when and how to use the tool.

### Tool Dispatch Flow

1. Orchestrator collects all mounted tools from coordinator
2. Builds `ToolSpec` objects (name + description + schema) for each
3. Sends as `ChatRequest.tools` to the LLM
4. LLM returns `ToolCall`s when it decides to use a tool
5. Hooks fire `tool:pre` (can block/modify)
6. `tool.execute(arguments)` runs
7. Hooks fire `tool:post`
8. Result added to context for next LLM turn
9. Loop continues until LLM stops calling tools

### Module Loading

Tools are loaded via a `mount()` function that registers them on the coordinator:

```python
async def mount(coordinator, config) -> Tool:
    tool = MyTool(config)
    await coordinator.mount("tools", tool, name=tool.name)
    return tool
```

Discovery uses entry points (`pyproject.toml`), direct imports, or source URIs.
Supports polyglot transports: Python, WASM, gRPC, Rust sidecar.

### Multi-Tool Modules

A single module can register multiple tools. The filesystem module registers three:
`read_file`, `write_file`, `edit_file` -- all sharing config and coordinator access.

### Tools Calling LLMs

Tools can internally spawn child sessions. The `delegate` tool is the canonical
example: it calls `coordinator.get_capability("session.spawn")` to create an
entire child AmplifierSession with its own orchestrator, tools, and LLM calls.

### Bundle Composition

```yaml
tools:
  - module: tool-filesystem
    source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
  - module: tool-bash
    source: git+https://github.com/microsoft/amplifier-module-tool-bash@main
```

### Key Implementation Files

- `amplifier-core/python/amplifier_core/interfaces.py:131-163` -- Tool protocol
- `amplifier-core/python/amplifier_core/models.py:37-100` -- ToolResult
- `amplifier-module-tool-bash/__init__.py` -- Reference implementation
- `amplifier-module-tool-filesystem/` -- Multi-tool module example
- `amplifier-foundation/modules/tool-delegate/` -- Tool that spawns agents

## Relevance to System Design Bundle

Custom tools could provide structured design capabilities:

- **Design model tool**: A tool that maintains a structured system model (goals,
  constraints, actors, interfaces, dependencies) that the LLM builds incrementally
  during design exploration. Unlike a context file, a tool can validate, transform,
  and persist structured data.

- **Tradeoff analysis tool**: A tool that takes design options and evaluation
  criteria, then produces a structured comparison matrix. The tool enforces the
  analysis framework (latency, complexity, reliability, cost, security, etc.)
  rather than relying on the LLM to remember the framework.

- **Constraint tracker**: A tool that maintains an explicit set of design
  constraints and checks proposed changes against them. Could flag when a design
  decision violates a previously established constraint.

- **Architecture diagram tool**: A tool that generates visual representations
  (dot-viz, mermaid) from the structured system model, giving the LLM a way to
  produce concrete artifacts during design.

However, tools are the heaviest mechanism -- each requires a Python module, entry
point, protocol compliance, and maintenance. For our initial experiments, skills
and context files may deliver faster value. Tools should be reserved for
capabilities that genuinely need structured data manipulation or external system
interaction.

Key question: Which design capabilities need the structure and validation that
tools provide vs. which can be accomplished through prompt engineering (skills/
context)?

## Context Window Impact

Tools affect the context window through their **results** -- every tool call
creates a pair of messages in the conversation history (an assistant message with
the tool_use block, and a tool result message with the output). These are the
primary target of the compaction system.

### The Tool Result Lifecycle

1. **Creation:** Tool executes, result added to `self.messages` via
   `context.add_message()` with `role: "tool"`
2. **Active use:** Result is included in full in subsequent LLM calls
3. **Compaction Level 1-2:** Oldest tool results truncated to 250 chars
   (preserving tool name and call ID for structural integrity)
4. **Compaction Level 3+:** Tool result + its paired assistant message removed
   atomically
5. **Protection:** The 5 most recent tool results are immune to truncation through
   Level 5

### Token Cost Model

| Factor | Detail |
|--------|--------|
| Storage | Message history (permanent until compacted) |
| Compactable | Yes -- first mechanism targeted by compaction |
| Typical size | Varies enormously: `read_file` can return 10K+ tokens; `glob` returns ~100 tokens |
| Protection | Last 5 tool results at full content |
| Pair removal | Tool result always removed with its paired assistant tool_use message |

### Why Tools Are the Primary Compaction Target

The 8-level compaction cascade targets tool results first because:
- Tool results are often large (file contents, search results, command output)
- They're frequently one-shot references (the agent read a file, extracted what it
  needed, and moved on)
- Truncation preserves structural metadata (tool name, call ID) so the agent
  can re-invoke if needed
- Removing old tool results recovers the most tokens with the least information loss

### The Truncation Format

Truncated tool results look like:
```
[truncated: ~5,432 tokens - call tool again if needed] first 250 chars of content...
```

This tells the agent exactly how much was lost and that re-invocation is possible.

### Tool Dispatch and Context Overhead

Each tool call in a single turn creates an assistant message with one or more
`tool_use` blocks, plus one `tool` result message per call. The orchestrator's
tool-calling loop can make multiple LLM calls per user turn:

```
User message -> LLM call 1 (returns 3 tool calls) -> execute tools -> 
LLM call 2 (returns 2 more tool calls) -> execute tools ->
LLM call 3 (returns text response) -> done
```

Each cycle adds tool_use + tool_result messages to history. A turn with 10 tool
calls adds ~20 messages. These accumulate and eventually trigger compaction.

### Tools That Spawn Sessions

Tools like `delegate` and `recipes` internally create child sessions. From the
parent's context perspective, these behave like any other tool: one tool_use
message + one tool_result message. The fact that an entire agent session ran
inside the tool is invisible to the parent's context manager -- only the final
text output appears in the result.

### Design Guidelines

1. **Tool descriptions cost tokens too.** Each tool's name, description, and JSON
   schema are sent as part of the LLM request on every call. A bundle with 20
   tools may spend 2-3K tokens just on tool definitions.
2. **Return concise results.** Tools that return large outputs (full file contents,
   verbose command output) create large entries in message history. Consider
   truncating or summarizing within the tool implementation.
3. **Leverage the "last 5" protection.** The most recent tool results stay at full
   fidelity. Design workflows so the most important tool calls happen last.
4. **Consider tool result size vs skill alternative.** If a tool primarily returns
   static reference information, a skill might be more token-efficient (loaded
   once, compacted normally) vs a tool (result persists until compaction pressure
   hits).
