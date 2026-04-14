# Application Integration Guide

**Purpose**: How to embed Amplifier in your application — web apps, voice assistants, Slack bots, CLI tools, background services, and beyond. Bridges the gap between "I understand bundles" and "I've built a production app with Amplifier."

**Prerequisites**: Familiarity with bundles ([BUNDLE_GUIDE.md](BUNDLE_GUIDE.md)), the prepare/session lifecycle ([CONCEPTS.md](CONCEPTS.md)), and basic tool/hook concepts.

> **A note on reference implementations**: All modules referenced in this guide — `context-simple`, `context-persistent`, `loop-streaming`, `loop-interactive`, `hooks-logging`, and others — are **reference implementations**. The Amplifier ecosystem actively encourages developers to study them for understanding the protocols and contracts, then build custom implementations tailored to their specific needs. The patterns shown here demonstrate one way to accomplish each concern — not the only way.

---

## Table of Contents

1. [The Protocol Boundary Principle](#1-the-protocol-boundary-principle)
2. [The Universal Session Lifecycle](#2-the-universal-session-lifecycle)
3. [Bundle Composition Strategies](#3-bundle-composition-strategies)
4. [Tool Mounting Patterns](#4-tool-mounting-patterns)
5. [Hook Strategies](#5-hook-strategies)
6. [Session Lifecycle Patterns](#6-session-lifecycle-patterns)
7. [Session Persistence and Restoration](#7-session-persistence-and-restoration)
8. [Common Anti-Patterns](#8-common-anti-patterns)
9. [The Protocol Boundary Pattern (In Depth)](#9-the-protocol-boundary-pattern-in-depth)
10. [Cross-References](#10-cross-references)

---

## 1. The Protocol Boundary Principle

This is the single most important concept in this guide.

Your application has its own concerns: HTTP routing, WebSocket management, audio streaming, Slack events, UI rendering. Amplifier has its concerns: session lifecycle, tool dispatch, provider management, hooks, context. These two domains should meet at a **clean boundary** — a thin bridge layer where application-specific events become Amplifier operations and Amplifier events become application-specific outputs.

### The Four Protocol Points

The boundary is implemented through four protocol points where your application meets Amplifier:

| Protocol | Direction | What Your App Provides | Amplifier Invokes It When |
|----------|-----------|------------------------|---------------------------|
| **ApprovalSystem** | Amplifier → App | Implementation of the approval contract | A tool or hook requests human confirmation |
| **DisplaySystem** | Amplifier → App | Implementation of the display contract | An agent wants to show something to the user |
| **StreamingHook** | Amplifier → App | A hook handler that forwards real-time events (content deltas, tool status, thinking, errors) | Any session event fires during execution |
| **Spawn Capability** | Amplifier → App | A capability function registered on the coordinator | Any component needs to create a new Amplifier session |

**On Spawn Capability**: This is the general mechanism for creating new Amplifier sessions from within existing ones. Agent delegation is one common use, but sessions are also spawned by orchestrators managing sub-tasks, recipe steps executing against different bundles, observer patterns running parallel analysis, and any module or pattern that needs an isolated execution context. The spawn capability is the universal "create a new session" entry point.

### Why This Matters

Everything on the application side should know nothing about bundles, coordinators, or tool protocols. Everything on the Amplifier side should know nothing about HTTP, WebSockets, or Slack. The bridge translates between them.

Applications that ignore this boundary end up with direct API calls wrapped in Amplifier labels — getting none of the benefits (hooks, observability, tool dispatch, session continuity) while paying the complexity cost. Applications that respect it get:

- **Testability** — mock either side independently
- **Portability** — same Amplifier session behind web, voice, CLI, or Slack
- **Clarity** — know which side to debug when something breaks
- **Evolvability** — swap your web framework without touching Amplifier; swap your orchestrator without touching your web framework

---

## 2. The Universal Session Lifecycle

Every Amplifier application follows this core pattern, regardless of application type.

### The Seven Steps

```
1. LOAD      →  load_bundle(source)
2. COMPOSE   →  bundle.compose(overlays)         # optional
3. PREPARE   →  await bundle.prepare()
4. CREATE    →  await prepared.create_session(...)
5. MOUNT     →  coordinator.mount("tools", tool)  # optional, post-creation
6. HOOK      →  coordinator.hooks.register(...)   # optional, post-creation
7. EXECUTE   →  await session.execute(prompt)
```

### Minimal Example

```python
from amplifier_core import load_bundle

# Steps 1-3: Once at startup
bundle = await load_bundle("./bundle.md")
prepared = await bundle.prepare()

# Steps 4-7: Per interaction
session = await prepared.create_session(
    session_id="my-session-001",
    approval_system=my_approval_impl,
    display_system=my_display_impl,
)
response = await session.execute("Hello, what can you help with?")
```

### What's Required vs Optional

| Step | Required? | Notes |
|------|-----------|-------|
| **Load** | Yes | From file path, bundle name, or git URI |
| **Compose** | No | Only if you need runtime overlays on the base bundle |
| **Prepare** | Yes | Resolves and downloads all modules — do this once |
| **Create** | Yes | Produces the AmplifierSession |
| **Mount** | No | For tools not declared in the bundle (runtime-dependent tools) |
| **Hook** | No | For app-specific event handling (WebSocket streaming, etc.) |
| **Execute** | Yes | Runs the agent loop |

### Key Opinions

**PreparedBundle is your singleton; sessions are ephemeral.** `prepare()` is expensive — it downloads modules, resolves dependencies, and activates everything. Do it once at application startup. `create_session()` is cheap. Do it per-request, per-conversation, or per-user as your pattern requires.

**`session_cwd` is critical for non-CLI apps.** Without it, file-system tools see the server's working directory, not the user's project or workspace. Always pass it explicitly when creating sessions for web or API applications.

**Composition replaces configuration.** Want different behavior for different environments, users, or modes? Don't toggle flags — compose a different bundle overlay.

### Session ID Reuse

A powerful pattern: reuse the same session ID across create/teardown cycles to make compatible reconfigurations. You can tear down a session and recreate it with the same ID but different providers, tools, hooks, or even a different orchestrator. The session ID provides continuity — especially for context restoration — while the configuration evolves.

```python
# Initial session
session = await prepared.create_session(session_id="user-42")

# Later: tear down, reconfigure, recreate with same ID
await session.close()
session = await prepared_v2.create_session(session_id="user-42")
# Context can be restored; configuration has changed
```

Care should be taken with orchestrator changes (different orchestrators may handle context differently), but swapping providers, adding/removing tools, or adjusting hook configurations are all expected and supported patterns.

---

## 3. Bundle Composition Strategies

Three approaches, each with different tradeoffs.

### Declarative (YAML Includes Chain)

Everything lives in `bundle.md`. Good for stable configurations that rarely change at runtime.

```yaml
---
bundle:
  name: my-app
  version: 1.0.0

includes:
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main
  - behavior: my-app:behaviors/domain-expert

session:
  orchestrator: {module: loop-streaming}
  context: {module: context-simple}
---

You are a helpful domain expert.
```

**When to use**: The configuration is known at authoring time and doesn't change based on runtime conditions.

### Programmatic (Bundle Overlays in Code)

Build bundles in Python at startup. Good for dynamic configuration based on environment, user, or runtime conditions.

```python
from amplifier_core import Bundle

base = Bundle(
    name="my-app",
    session={"orchestrator": "loop-streaming", "context": "context-simple"},
    providers=[{"module": "provider-anthropic", "config": {"default_model": "claude-sonnet-4-5"}}],
)

# Compose environment-specific overlay
if os.environ.get("ENV") == "development":
    overlay = Bundle(name="dev", providers=[
        {"module": "provider-anthropic", "config": {"debug": True}}
    ])
    bundle = base.compose(overlay)
```

**When to use**: Provider selection, model choice, or feature flags depend on runtime environment or user identity.

### Hybrid (The Most Common Production Pattern)

Load a base bundle from file, then compose runtime overlays programmatically. The `bundle.md` declares the stable parts (tools, agents, system prompt). Code adds the dynamic parts (provider based on env vars, hooks based on deployment).

```python
# Stable config from file
bundle = await load_bundle("./bundle.md")

# Dynamic overlay from runtime context
runtime_overlay = Bundle(
    name="runtime",
    providers=[{"module": "provider-anthropic", "config": {
        "default_model": os.environ["MODEL_NAME"],
    }}],
)

composed = bundle.compose(runtime_overlay)
prepared = await composed.prepare()
```

**When to use**: Most production applications. The bundle.md is versioned and reviewed. Runtime concerns are injected at startup.

### Environment-Based Composition

A natural extension of the hybrid approach — compose different overlays for dev, staging, and production:

```python
bundle = await load_bundle("./bundle.md")

env_overlays = {
    "development": Bundle(name="dev", providers=[...]),  # local models, verbose logging
    "staging":     Bundle(name="stg", providers=[...]),  # production models, extra logging
    "production":  Bundle(name="prd", providers=[...]),  # production models, minimal logging
}

composed = bundle.compose(env_overlays[os.environ["ENV"]])
prepared = await composed.prepare()
```

### Anti-Pattern: Decorative bundle.md

A `bundle.md` exists in the repo with valid YAML frontmatter declaring orchestrators, context modules, tools, and hooks. But the application never calls `load_bundle()` on it. The code constructs everything independently. The bundle is documentation that drifts from reality.

**Fix**: If you have a `bundle.md`, load it. Compose runtime overlays on top if needed. If you don't load it, don't have one — a misleading bundle file is worse than no bundle file.

---

## 4. Tool Mounting Patterns

Two approaches for getting tools into a session.

### In-Bundle Declaration

Tools declared in the bundle's YAML frontmatter. They're resolved during `prepare()` and mounted automatically during `create_session()`. Best for tools that are fundamental to the bundle's identity — the tools that define what this agent *can* do.

```yaml
tools:
  - module: tool-filesystem
    source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
  - module: my-graph-tool
    source: local:modules/graph_tool
```

### Post-Creation Mounting

Tools mounted programmatically via `coordinator.mount()` after the session exists. Best for tools that are specific to the application context, not the bundle's identity. Tools that depend on runtime state — a database connection, a WebSocket reference, an API client — often need this pattern because the state doesn't exist until the app is running.

```python
session = await prepared.create_session(...)

# Mount a tool that needs a live database connection
graph_tool = GraphTool(neo4j_driver=app.state.neo4j)
await session.coordinator.mount("tools", graph_tool, name="life_graph")

# Mount a tool that needs a specific WebSocket reference
notify_tool = NotifyTool(websocket=current_ws)
await session.coordinator.mount("tools", notify_tool, name="notify_user")
```

### Decision Framework

| Criterion | In-Bundle | Post-Creation |
|-----------|-----------|---------------|
| Part of the bundle's core capability | Yes | |
| Depends on runtime state (DB, API client) | | Yes |
| App-specific (Slack posting, UI rendering) | | Yes |
| Should be available to ALL sessions | Yes | |
| Varies per session or per user | | Yes |
| Discoverable in bundle YAML for documentation | Yes | |
| Needs application-layer object references | | Yes |

### Anti-Pattern: Bypassing the Session for Tool Execution

If your application calls an LLM directly (not through `session.execute()`) and then manually dispatches tool calls by parsing the response and invoking tool functions, you're not using Amplifier — you're reimplementing it. The orchestrator handles the LLM → tool call → result → LLM loop. Your tools just need to be mounted and implement the Tool protocol. Let the orchestrator do its job.

---

## 5. Hook Strategies

Hooks are how you observe and react to session events. Two distinct patterns.

### Bundle Hooks (Persistent)

Declared in YAML as part of the bundle configuration. They live for the lifetime of the session. Best for cross-cutting concerns that should always be active.

```yaml
hooks:
  - module: hooks-logging
    source: git+https://github.com/microsoft/amplifier-module-hooks-logging@main
  - module: hooks-redaction
    source: git+https://github.com/microsoft/amplifier-module-hooks-redaction@main
```

Use bundle hooks for: event logging, CXDB capture, privacy redaction, cost tracking — anything that should be active regardless of who's connected or what interaction is happening.

### Ephemeral Hooks (Per-Execution)

Registered before a specific execution and unregistered afterward in a `finally` block. Best for app-specific event handling that varies per interaction.

```python
# Register before execution
unreg = session.coordinator.hooks.register(
    "tool:pre", on_tool_start, priority=999, name="_ws_progress"
)
try:
    response = await session.execute(prompt)
finally:
    unreg()  # Always clean up
```

Use ephemeral hooks for: progress updates pushed to a specific WebSocket connection, status messages in a specific Slack thread, UI indicators for a specific user session.

### The Streaming Hook Pattern

The StreamingHook is the third protocol boundary point. Your application implements a hook that receives ALL session events and forwards them to whatever transport your app uses — WebSocket, SSE, Slack messages, or anything else. This is what makes the agent's work visible to the user in real-time.

```python
class WebSocketStreamingHook:
    """Forwards Amplifier session events to a WebSocket client."""

    def __init__(self, websocket):
        self.ws = websocket

    async def on_event(self, event_type, data):
        await self.ws.send_json({
            "type": "session_event",
            "event": event_type,
            "data": data,
        })

# Register per-connection
hook = WebSocketStreamingHook(websocket)
unreg = session.coordinator.hooks.register(
    "*", hook.on_event, priority=999, name="_ws_stream"
)
```

### Decision Framework

| Concern | Hook Type | When Registered |
|---------|-----------|-----------------|
| JSONL event logging | Bundle | Always — declared in YAML |
| CXDB capture | Bundle | Always — declared in YAML |
| Privacy redaction | Bundle | Always — declared in YAML |
| WebSocket streaming to UI | Ephemeral | Per-connection |
| Slack thread updates | Ephemeral | Per-message |
| Progress display | Ephemeral | Per-execution |
| Cost tracking | Bundle or app-layer | Depends on scope |

**Note on policy concerns**: Notifications, cost alerts, and similar organizational policy concerns often belong in the application layer, not in the bundle itself. The bundle provides the mechanisms (hooks, events); the application applies the policies.

---

## 6. Session Lifecycle Patterns

Five distinct patterns for different application types.

### Pattern A: Per-Request Sessions

**When to use**: Each request is independent. No conversation history needed between requests. Document processing, one-shot analysis, webhook handlers.

**Lifecycle**: Request arrives → create session from PreparedBundle → execute → return response → session discarded.

```python
# At startup (once)
bundle = await load_bundle("./bundle.md")
prepared = await bundle.prepare()

# Per request
@app.post("/api/analyze")
async def analyze(request: AnalyzeRequest):
    session = await prepared.create_session(
        session_id=f"req-{uuid4()}",
        approval_system=AutoApproveSystem(),
    )
    try:
        response = await session.execute(request.prompt)
        return {"result": response}
    finally:
        await session.close()
```

**Key insight**: PreparedBundle is the singleton. Sessions are ephemeral and discarded after each request. `prepare()` is expensive; `create_session()` is cheap.

---

### Pattern B: Per-Conversation Sessions

**When to use**: Multiple messages form a conversation. Each conversation needs its own context. Multiple conversations may be active simultaneously. Chat bots, messaging integrations, customer support agents.

**Lifecycle**: First message → lookup or create session (keyed by conversation ID) → execute → persist. Subsequent messages → lookup existing session → execute → persist.

```python
# Session map: one session per conversation
sessions: dict[str, AmplifierSession] = {}
locks: dict[str, asyncio.Lock] = {}

async def handle_message(conversation_id: str, message: str):
    # Lazy session creation
    if conversation_id not in sessions:
        sessions[conversation_id] = await prepared.create_session(
            session_id=conversation_id,
            approval_system=SlackApprovalSystem(conversation_id),
            display_system=SlackDisplaySystem(conversation_id),
        )
        locks[conversation_id] = asyncio.Lock()

    # One execution at a time per conversation
    async with locks[conversation_id]:
        return await sessions[conversation_id].execute(message)
```

**Key insights**:
- **Session map**: `dict[str, AmplifierSession]` keyed by conversation identifier (Slack channel ID, Discord thread, chat room).
- **Lazy creation**: Sessions created on first message, cached for subsequent ones.
- **Per-session locks**: One execution at a time per conversation (`asyncio.Lock`).
- **Cleanup strategy**: Timeout, explicit close, or bounded cache with LRU eviction.
- **Session ID reuse**: When a conversation needs reconfiguration (different provider, added tools), tear down and recreate with the same session ID. Context can be restored; the configuration evolves.

---

### Pattern C: Singleton Session

**When to use**: One user, one agent, continuous context accumulation. The session IS the application. Personal AI assistants, development companions, always-on daemons.

**Lifecycle**: Server startup → create session → restore conversation from previous run → run indefinitely → save periodically → graceful shutdown.

```python
class LifelineApp:
    def __init__(self):
        self.session: AmplifierSession | None = None

    async def startup(self):
        bundle = await load_bundle("./bundle.md")
        prepared = await bundle.prepare()

        self.session = await prepared.create_session(
            session_id="lifeline-main",
            session_cwd="/home/user/workspace",
            approval_system=WebApprovalSystem(),
            display_system=WebDisplaySystem(),
        )

        # Restore prior conversation
        await self._restore_context()

        # Mount runtime-dependent tools
        graph_tool = GraphTool(driver=self.neo4j)
        await self.session.coordinator.mount("tools", graph_tool, name="life_graph")

    async def handle_input(self, message: str):
        return await self.session.execute(message)

    async def shutdown(self):
        await self._save_context()
        await self.session.close()
```

**Key insights**:
- **Long-lived**: The session persists for the lifetime of the process — days or weeks.
- **Conversation persistence**: Save and restore conversation history across process restarts (see [Session Persistence](#7-session-persistence-and-restoration)).
- **Session ID reuse across restarts**: The same session ID reconnects to the same context.
- **Compound intelligence**: The session accumulates context over time — it genuinely gets to know the user.

---

### Pattern D: Voice/Realtime Bridge

**When to use**: A realtime voice protocol (e.g., OpenAI Realtime API, WebRTC) handles user-facing audio and conversational flow. Amplifier provides the tools, intelligence, and agent delegation behind the scenes.

**This is a bridge, not a replacement.** The voice model handles audio I/O, turn-taking, interruptions, and conversational warmth. Amplifier handles tool execution, agent spawning, and graph queries. They meet at the tool boundary.

```
User speaks
    ↓
Voice Model (OpenAI RT / WebRTC)
    ├── Conversational response (audio out)
    └── Tool call: delegate("query the calendar")
         ↓
    Amplifier Session
         ├── Orchestrator dispatches to specialist agent
         ├── Agent queries graph, reasons, produces result
         └── Result returned to voice model
              ↓
         Voice model narrates result to user
```

```python
class VoiceBridge:
    """Connects a realtime voice model to Amplifier tools."""

    def __init__(self, amplifier_session: AmplifierSession):
        self.session = amplifier_session

    async def handle_tool_call(self, tool_name: str, arguments: dict) -> str:
        """Called when the voice model invokes a tool."""
        if tool_name == "delegate":
            # Route through Amplifier's agent delegation
            result = await self.session.execute(arguments["instruction"])
            return result
        elif tool_name == "quick_lookup":
            # Direct tool invocation
            tool = self.session.coordinator.get_tool("quick_lookup")
            return await tool.execute(arguments)
        else:
            return f"Unknown tool: {tool_name}"
```

**Key insights**:
- **The voice model is NOT inside AmplifierSession.** It has its own connection, its own session state, its own conversational flow. Amplifier is a tool backend, not the orchestrator of the voice conversation.
- **Tool whitelisting**: Only expose `delegate` and a few lookup tools to the voice model. Don't expose file-system tools or shell access directly to a conversational interface.
- **Audio never touches Amplifier.** Audio stays between the user and the voice model. Only text (tool calls, results) crosses into Amplifier.
- **The voice model delegates heavy lifting.** The realtime model is optimized for conversation — it delegates reasoning, planning, and data queries to capable models (Sonnet, Opus) via Amplifier agents.

---

### Pattern E: Multi-Session Manager

**When to use**: Your application manages multiple independent sessions with different bundles, configurations, or users. Multi-tenant platforms, A/B testing, ensemble patterns where multiple agents work on the same problem.

```python
class SessionManager:
    def __init__(self):
        self.bundles: dict[str, PreparedBundle] = {}
        self.sessions: dict[str, AmplifierSession] = {}

    async def register_bundle(self, name: str, bundle_path: str):
        bundle = await load_bundle(bundle_path)
        self.bundles[name] = await bundle.prepare()

    async def get_or_create(self, session_id: str, bundle_name: str) -> AmplifierSession:
        if session_id not in self.sessions:
            prepared = self.bundles[bundle_name]
            self.sessions[session_id] = await prepared.create_session(
                session_id=session_id,
                approval_system=self._make_approval(session_id),
                display_system=self._make_display(session_id),
            )
        return self.sessions[session_id]

    async def route(self, session_id: str, bundle_name: str, prompt: str):
        session = await self.get_or_create(session_id, bundle_name)
        return await session.execute(prompt)
```

**Key insights**:
- **Multiple PreparedBundles**: Different bundles for different agent types, user tiers, or A/B variants. Each is prepared once.
- **Routing logic**: The application decides which bundle serves which request — by user role, feature flag, content type, or any other criterion.
- **Resource management**: Bounded session pools, idle eviction, graceful shutdown across all sessions.
- **Bundle cache**: Avoid re-preparing the same bundle. Cache `PreparedBundle` instances by source + config hash.

---

### Decision Matrix

| Application Type | Pattern | Session Lifespan | Key Concern |
|-----------------|---------|-----------------|-------------|
| REST API / webhook handler | A: Per-Request | Seconds | PreparedBundle singleton |
| Chat bot / messaging integration | B: Per-Conversation | Minutes to hours | Session map + per-session locks |
| Personal AI assistant | C: Singleton | Days to weeks | Persistence + compound intelligence |
| Voice assistant with tools | D: Voice Bridge | Session duration | Tool boundary, not orchestrator replacement |
| Multi-persona / multi-tenant | E: Multi-Session Manager | Varies | Routing + resource management |
| Combination (voice + web + proactive) | C + D | Process lifetime | Singleton provides tools to voice bridge |

---

## 7. Session Persistence and Restoration

Three levels, from lightest to most robust.

### Level 1: Session ID Reuse (Built-In)

Pass the same `session_id` to `create_session()` across restarts. The session infrastructure recognizes the ID and can reconnect to any persistence managed by the context module. This is the lightest touch — works out of the box if your context module supports it.

```python
# First run
session = await prepared.create_session(session_id="user-42")
await session.execute("Remember, I prefer morning meetings.")

# After restart — same session_id reconnects to context
session = await prepared.create_session(session_id="user-42")
await session.execute("When should we schedule the review?")
# Agent can access prior context if the context module persists it
```

### Level 2: Manual Context Save/Restore

Reach into the context module to save and restore conversation history. Full control over what's persisted, how it's serialized, and where it's stored.

```python
# Save (after each turn or periodically)
async def save_context(session, path: str):
    ctx = session.coordinator.mount_points.get("context")
    messages = await ctx.get_messages()
    Path(path).write_text(json.dumps(messages, default=str))

# Restore (at startup, after create_session)
async def restore_context(session, path: str):
    if Path(path).exists():
        messages = json.loads(Path(path).read_text())
        ctx = session.coordinator.mount_points.get("context")
        await ctx.set_messages(messages)
```

Good for singleton sessions that need cross-restart continuity with custom serialization.

### Level 3: Persistent Context Module

The context module itself handles persistence transparently. Declare it in your bundle and it manages storage automatically.

```yaml
session:
  context:
    module: context-persistent
    config:
      storage_path: ./data/sessions
      auto_save: true
```

Best when you don't need custom serialization logic and want the context module to own the persistence lifecycle.

### Session ID Reuse for Reconfiguration

As discussed in [Section 2](#2-the-universal-session-lifecycle), you can tear down a session and recreate it with the same ID but different configuration. The session ID provides continuity for context restoration while the bundle composition, providers, tools, or hooks change.

Use cases:
- Hot-swapping a provider when one has an outage
- Adding tools as the user unlocks features or enters a new workflow
- Adjusting the orchestrator for different interaction modes (exploration vs. focused execution)
- Enabling/disabling specific hooks or behaviors

Care should be taken especially with orchestrator changes, but all reconfiguration is possible and expected.

### Anti-Pattern: Dual-Track Conversation History

If your application maintains its own `list[dict]` of messages alongside the context module's internal state, the two will inevitably drift. One gets updated; the other doesn't. Bugs appear when the LLM sees a different history than your app expects.

**Fix**: Use the context module as the single source of truth. If you need the conversation history for your app logic (rendering a chat UI, searching past messages), read it from the context module — don't maintain a parallel copy.

> **Reminder**: `context-simple`, `context-persistent`, and other context modules are reference implementations. If the built-in modules don't fit your persistence needs, study their protocol contract and build one that does. The power is in the protocol, not the specific implementation.

---

## 8. Common Anti-Patterns

Patterns that produce applications that look like they use Amplifier but get none of the benefits.

### 1. Direct API Calls Bypassing the Session

**What it looks like**: Your application calls `anthropic.messages.create()` or `openai.chat.completions.create()` directly, then manually parses tool calls and dispatches them.

**Why it's a problem**: You've reimplemented the orchestrator — but without hooks (no observability), without context management (no conversation continuity), without streaming support, without error recovery, and without the ability to swap components. Every cross-cutting concern must be hand-wired.

**Why it happens**: Developers familiar with LLM APIs start with what they know. Direct calls feel simpler initially. But you're trading initial simplicity for permanent loss of observability, hook support, streaming, session persistence, and the ability to swap orchestrators, providers, or tools without rewriting the loop.

**Fix**: Use `session.execute()`. Mount your tools, register your hooks, and let the orchestrator handle the LLM → tool → result → LLM loop.

### 2. Decorative bundle.md

**What it looks like**: A `bundle.md` exists in the repo with valid YAML frontmatter declaring orchestrators, context modules, tools, and agents. But the application never calls `load_bundle()` on it. The Python code constructs everything independently.

**Why it's a problem**: The bundle is documentation that drifts from reality. New developers read it and form incorrect assumptions about how the application works.

**Fix**: If you have a `bundle.md`, load it with `load_bundle()` and compose runtime overlays on top. If you don't load it, delete it.

### 3. Hand-Rolled Tool Loops

**What it looks like**: A `while True` loop that calls the provider, checks for tool calls, executes them, appends results, and calls the provider again.

```python
# DON'T DO THIS
messages = [{"role": "user", "content": prompt}]
while True:
    response = await client.messages.create(model=model, messages=messages)
    if not response.tool_calls:
        break
    for tc in response.tool_calls:
        result = dispatch_tool(tc)
        messages.append({"role": "tool", "content": result})
```

**Why it's a problem**: This is the orchestrator's job. It handles streaming, hook firing at every stage, error recovery, context window management, cancellation, and provider-specific nuances. Your hand-rolled loop handles none of these.

**Fix**: Mount your tools on the session. The orchestrator runs the loop.

### 4. Ignoring the Protocol Boundary

**What it looks like**: A FastAPI handler directly accesses `coordinator.mount_points`, constructs tool call arguments, manages conversation state, and calls `provider.complete()` — all in the same function.

**Why it's a problem**: Application and Amplifier layers are entangled. You can't test the Amplifier logic without running FastAPI. You can't swap the web framework without rewriting the agent logic. Debugging requires understanding both domains simultaneously.

**Fix**: Your route handler calls `session.execute()`. Your protocol implementations (ApprovalSystem, DisplaySystem, StreamingHook) handle the translation. Nothing else crosses the boundary.

### 5. Fat Bundle When PreparedBundle Suffices

**What it looks like**: Calling `prepare()` per request because you compose a fresh bundle each time.

```python
# DON'T DO THIS
@app.post("/chat")
async def chat(request):
    bundle = await load_bundle("./bundle.md")  # Every request!
    prepared = await bundle.prepare()           # Downloads modules every time!
    session = await prepared.create_session()
    return await session.execute(request.message)
```

**Why it's a problem**: `prepare()` is expensive — it resolves modules, potentially downloads them, and activates dependencies. Doing this per-request adds latency and wastes resources.

**Fix**: Prepare once at startup. Create sessions cheaply from the PreparedBundle.

### 6. Singleton Session Where Per-Conversation Is Needed

**What it looks like**: A multi-user chat bot with one shared session. User A asks about recipes. User B asks about finance. The context bleeds between them.

**Why it's a problem**: Context from user A leaks into user B's conversation. The agent confuses who it's talking to and what was previously discussed.

**Fix**: Use Pattern B (Per-Conversation Sessions) — one session per conversation, keyed by user or channel ID.

---

## 9. The Protocol Boundary Pattern (In Depth)

The architectural pattern that ties everything together.

### Where the Boundary Lives

```
YOUR APPLICATION                    THE BOUNDARY                AMPLIFIER
─────────────────                   ──────────────              ──────────
FastAPI routes                      ApprovalSystem impl         Session lifecycle
WebSocket handlers        ←───→     DisplaySystem impl    ←──→  Orchestrator loop
Slack event listeners               StreamingHook impl          Tool dispatch
Audio stream management             Spawn capability fn         Provider management
UI rendering logic                                              Hook system
State management                                                Context management
```

### The Four Boundary Protocols

**ApprovalSystem** — Amplifier calls your implementation when a tool or hook requests human confirmation. Your app decides how to present the approval (modal dialog, Slack button, CLI prompt) and returns the decision.

```python
class WebApprovalSystem:
    def __init__(self, websocket):
        self.ws = websocket
        self.pending: dict[str, asyncio.Future] = {}

    async def request_approval(self, description: str, context: dict) -> bool:
        request_id = str(uuid4())
        future = asyncio.get_event_loop().create_future()
        self.pending[request_id] = future

        await self.ws.send_json({
            "type": "approval_request",
            "id": request_id,
            "description": description,
            "context": context,
        })

        return await future  # Resolved when user clicks approve/deny

    def resolve(self, request_id: str, approved: bool):
        if request_id in self.pending:
            self.pending[request_id].set_result(approved)
```

**DisplaySystem** — Amplifier calls your implementation when the agent wants to show something to the user. Your app decides the rendering medium.

```python
class WebDisplaySystem:
    def __init__(self, websocket):
        self.ws = websocket

    async def display(self, content: str, metadata: dict | None = None):
        await self.ws.send_json({
            "type": "display",
            "content": content,
            "metadata": metadata or {},
        })
```

**StreamingHook** — Your app registers a hook that receives all session events and forwards them over whatever transport you use. This makes agent work visible in real-time.

```python
class SSEStreamingHook:
    def __init__(self, event_queue: asyncio.Queue):
        self.queue = event_queue

    async def on_content_delta(self, delta: str):
        await self.queue.put({"type": "content", "data": delta})

    async def on_tool_start(self, tool_name: str, args: dict):
        await self.queue.put({"type": "tool_start", "tool": tool_name})

    async def on_tool_end(self, tool_name: str, result: str):
        await self.queue.put({"type": "tool_end", "tool": tool_name})
```

**Spawn Capability** — Registered on the coordinator, called when any component needs to create a new Amplifier session. This is how your application controls session creation for delegates, sub-tasks, recipe steps, and anything else that needs an isolated execution context.

```python
async def spawn_session(config: dict) -> AmplifierSession:
    """Application-controlled session creation."""
    bundle_name = config.get("bundle", "default")
    prepared = app.bundles[bundle_name]

    session = await prepared.create_session(
        session_id=config.get("session_id", str(uuid4())),
        session_cwd=config.get("cwd", app.default_cwd),
        approval_system=app.approval_system,
        display_system=app.display_system,
    )

    # Application can customize spawned sessions
    if config.get("tools"):
        for tool in config["tools"]:
            await session.coordinator.mount("tools", tool)

    return session

# Register on the coordinator
session.coordinator.register_capability("spawn", spawn_session)
```

### What Crosses the Boundary Correctly

| Direction | What Crosses | Example |
|-----------|-------------|---------|
| App → Amplifier | User prompt (text) | `session.execute("Plan a dinner party")` |
| Amplifier → App | Approval request | `approval_system.request_approval("Send email to Sarah?", {...})` |
| Amplifier → App | Display content | `display_system.display("Here are 3 restaurant options...")` |
| Amplifier → App | Session events | `streaming_hook.on_tool_start("life_graph", {...})` |
| Amplifier → App | Spawn request | `spawn_capability({"bundle": "planner", ...})` |
| App → Amplifier | Approval decision | `approval_system.resolve(request_id, True)` |
| App → Amplifier | Session config | `create_session(session_id=..., session_cwd=...)` |

### What Should NOT Cross the Boundary

- Application directly accessing `coordinator.mount_points` for routine operations
- Application hand-calling `provider.complete()` instead of `session.execute()`
- Amplifier code importing `fastapi`, `slack_bolt`, or any application framework
- Application building tool-call JSON manually and injecting it into the context
- Application maintaining parallel conversation state alongside the context module

### Testing at the Boundary

The clean boundary makes testing straightforward:

```python
# Test Amplifier logic without the web framework
class MockApproval:
    async def request_approval(self, desc, ctx):
        return True  # Auto-approve in tests

class MockDisplay:
    def __init__(self):
        self.messages = []
    async def display(self, content, metadata=None):
        self.messages.append(content)

# Test with real Amplifier session, mocked app layer
session = await prepared.create_session(
    approval_system=MockApproval(),
    display_system=MockDisplay(),
)
response = await session.execute("What's on my calendar?")
assert "meeting" in response.lower()
```

```python
# Test app layer without Amplifier
async def test_websocket_handler():
    mock_session = MockSession(responses=["Here are your events..."])
    handler = WebSocketHandler(session=mock_session)
    await handler.on_message({"type": "chat", "message": "What's today?"})
    assert mock_session.execute_called_with == "What's today?"
```

---

## 10. Cross-References

This guide connects to the broader Amplifier documentation:

| Document | Relationship to This Guide |
|----------|---------------------------|
| [CONCEPTS.md](CONCEPTS.md) | Bundle → mount plan → session flow; PreparedBundle concept |
| [BUNDLE_GUIDE.md](BUNDLE_GUIDE.md) | Thin bundle pattern; app-level runtime injection |
| [PATTERNS.md](PATTERNS.md) | Session patterns, composition patterns, performance patterns |
| API Reference | `Bundle`, `load_bundle`, `PreparedBundle`, `ProviderPreference` |
| Example 07 | Full workflow reference implementation |
| Example 08 | CLI application pattern (Pattern A/C) |
| Example 14 | Session persistence (Level 2/3) |
| Example 18 | Custom hooks (ephemeral hook pattern) |

### Suggested New Examples

Two examples that would complement this guide:

- **Example: Web Application** — FastAPI + WebSocket bridge implementing Pattern B (per-conversation) with ephemeral streaming hooks. Demonstrates the Protocol Boundary Pattern with `WebApprovalSystem`, `WebDisplaySystem`, and `WebStreamingHook`.

- **Example: Voice + Agent Bridge** — Pattern D implementation showing a realtime voice model using Amplifier sessions for tool execution and agent delegation. Demonstrates the tool boundary between voice and Amplifier without putting the voice model inside the session.
