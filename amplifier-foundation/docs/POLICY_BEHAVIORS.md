# Policy Behaviors

Policy behaviors are app-level capabilities that get composed onto bundles at runtime, similar to how providers work. Unlike bundle behaviors (which apply to all sessions), policy behaviors are:

- **App-context-dependent**: A CLI wants notifications; a headless service doesn't
- **Root-session-only**: They don't fire for sub-agents or recipe steps
- **User-configurable**: Enabled/disabled via `settings.yaml`

## The Pattern

```
┌─────────────────────────────────────────────────────────────┐
│                    APP (CLI/Service)                        │
│                                                             │
│  settings.yaml:                                             │
│    config:                                                  │
│      providers: [...]         # Which LLM to use            │
│      notifications:           # Policy: how to notify       │
│        desktop:                                             │
│          enabled: true                                      │
│        push:                                                │
│          enabled: true                                      │
│          service: ntfy                                      │
│          topic: "my-topic"                                  │
│                                                             │
│  Runtime: compose policy behaviors onto bundle              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              BUNDLE (e.g., foundation)                      │
│                                                             │
│  Provides mechanisms:                                       │
│  - Tools, agents, context, orchestrator                     │
│  - Hook system for extensibility                            │
│                                                             │
│  Does NOT include policy behaviors                          │
└─────────────────────────────────────────────────────────────┘
```

## Why Policy Behaviors?

Consider notifications: when you run Amplifier, you want to know when the assistant is ready for input. But if you add notifications to a bundle:

```yaml
# DON'T DO THIS - notifications in bundle
includes:
  - bundle: foundation
  - bundle: notify:behaviors/desktop-notifications  # Bad!
```

Every sub-agent spawn, every recipe step, every nested session will fire a notification. You'll get spammed.

**Policy behaviors solve this** by:
1. Being composed by the app layer, not the bundle
2. Only applying to root sessions (checking `parent_id`)
3. Being configurable per-installation

## Bundle Behaviors vs Policy Behaviors

| Aspect | Bundle Behaviors | Policy Behaviors |
|--------|------------------|------------------|
| Included by | Bundle `includes:` | App composes at runtime |
| Applies to | All sessions | Root sessions only |
| Examples | Logging, redaction, tools | Notifications, cost alerts |
| Configured in | Bundle YAML | `settings.yaml` |

## Implementing Policy Behaviors

### For Hook Authors

If your hook should only fire for root sessions, check `parent_id`:

```python
async def handle_event(self, event: str, data: dict) -> HookResult:
    # Skip sub-sessions - this is a policy behavior
    if data.get("parent_id"):
        return HookResult(action="continue")
    
    # Root session logic...
    await self._send_notification(data)
    return HookResult(action="continue")
```

Or make it configurable:

```python
class MyHook:
    def __init__(self, config: dict):
        # Default to root-only for policy behaviors
        self.session_filter = config.get("session_filter", "root_only")
    
    async def handle_event(self, event: str, data: dict) -> HookResult:
        is_root = data.get("parent_id") is None
        
        if self.session_filter == "root_only" and not is_root:
            return HookResult(action="continue")
        elif self.session_filter == "sub_only" and is_root:
            return HookResult(action="continue")
        # "all" runs for everything
        
        # ... hook logic
```

### For App Developers

Apps should inject policy behaviors based on user settings:

```python
def inject_policies(
    settings: dict,
    prepared_bundle: PreparedBundle,
    is_root_session: bool,
) -> None:
    """Inject policy behaviors into bundle's mount plan."""
    if not is_root_session:
        return  # Policies only apply to root sessions
    
    notifications = settings.get("config", {}).get("notifications", {})
    if notifications:
        inject_notification_hooks(notifications, prepared_bundle)
```

### For Bundle Authors

If you're creating a bundle with hooks that should be policy behaviors:

1. **Don't include them in your main bundle** - provide them as separate behaviors
2. **Document them as policy behaviors** - so apps know to compose them
3. **Default to root-only** - check `parent_id` in the hook implementation

```yaml
# Good: Separate behavior file for policy hooks
# behaviors/notifications.yaml
bundle:
  name: notifications-behavior
  description: |
    Policy behavior - should be composed by apps, not included in bundles.
    Only fires for root sessions.

hooks:
  - module: hooks-notify
    config:
      session_filter: root_only
```

## Settings Structure

Policy behaviors are configured under `config:` in `settings.yaml`:

```yaml
config:
  # Providers (existing pattern)
  providers:
    - module: provider-anthropic
      config:
        api_key: ${ANTHROPIC_API_KEY}

  # Notifications policy
  notifications:
    desktop:
      enabled: true
      title: "Amplifier"
      subtitle: "cwd"
      suppress_if_focused: true
      sound: false
    
    push:
      enabled: true
      service: ntfy
      topic: "my-amplifier-alerts"
      server: "https://ntfy.sh"
      priority: default
    
    # Common options
    min_iterations: 1
    show_iteration_count: true
```

### Multiple Methods Simultaneously

You can enable multiple notification methods at once. This is useful when using the same installation from different contexts:

```yaml
config:
  notifications:
    desktop:
      enabled: true           # Works when on desktop
      suppress_if_focused: true
    push:
      enabled: true           # Works when on mobile (Termius, etc.)
      service: ntfy
      topic: "my-topic"
```

- **On desktop**: Desktop toast appears (unless terminal focused)
- **On mobile SSH**: Terminal bell plays + phone gets push notification

## Future Policy Types

The `config:` section can accommodate future policy behaviors:

```yaml
config:
  notifications: {...}        # Implemented
  
  # Future possibilities (not yet implemented)
  cost:
    budget: 10.00
    alert_at: [50, 80, 100]   # Percentage thresholds
  
  session:
    timeout_minutes: 60
    max_iterations: 100
```

## Philosophy Alignment

From `KERNEL_PHILOSOPHY.md`:

> **Mechanism, not policy.** The kernel exposes capabilities and stable contracts. Decisions about behavior belong outside the kernel.

> **Policy lives at the edges.** Scheduling strategies, orchestration styles, provider choices, safety policies... belong in modules.

Policy behaviors embody this principle:
- **Bundles provide mechanism** (hook system, event flow)
- **Apps provide policy** (which hooks fire, when, for whom)

The `settings.yaml` IS the edge where policy decisions live.
