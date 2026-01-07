# App-Level Policy Examples

This directory contains examples of how apps compose **policy behaviors** onto bundles at runtime.

Policy behaviors are app-level capabilities that:
- Only apply to root sessions (not sub-agents)
- Are configured via `settings.yaml`
- Are composed by the app, not included in bundles

## Examples

### Notifications

Configure desktop and push notifications in `~/.amplifier/settings.yaml`:

```yaml
config:
  notifications:
    desktop:
      enabled: true
      title: "Amplifier"
      subtitle: "cwd"           # "cwd", "git", or custom string
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

**Multiple methods simultaneously**: You can enable both desktop AND push. This is useful when using the same installation from different contexts (local terminal vs mobile SSH).

See [notifications-settings.yaml](notifications-settings.yaml) for a complete example.

## How Apps Inject Policies

Apps follow this pattern to inject policy behaviors:

```python
def inject_notification_policy(
    notifications_config: dict,
    prepared_bundle: PreparedBundle,
    is_root_session: bool,
) -> None:
    """Inject notification behaviors based on config.notifications."""
    # Policies only apply to root sessions
    if not is_root_session:
        return
    
    desktop_config = notifications_config.get("desktop", {})
    push_config = notifications_config.get("push", {})
    
    # Common options apply to all methods
    common = {
        "min_iterations": notifications_config.get("min_iterations", 1),
        "show_iteration_count": notifications_config.get("show_iteration_count", True),
    }
    
    # Inject desktop hook if enabled
    if desktop_config.get("enabled", False):
        inject_hook(prepared_bundle, "hooks-notify", {**common, **desktop_config})
    
    # Inject push hook if enabled
    if push_config.get("enabled", False):
        inject_hook(prepared_bundle, "hooks-notify-push", {**common, **push_config})
```

## Creating New Policy Behaviors

When creating a new policy behavior:

1. **Create the hook module** that implements the behavior
2. **Check `parent_id`** in the hook to skip sub-sessions
3. **Document the settings structure** for apps to configure
4. **Provide injection helper** for apps to compose the behavior

See [POLICY_BEHAVIORS.md](../../docs/POLICY_BEHAVIORS.md) for detailed guidance.
