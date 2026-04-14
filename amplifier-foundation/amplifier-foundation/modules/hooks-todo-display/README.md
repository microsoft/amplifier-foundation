# Todo Display Hooks Module

Visual progress display for todo list tool calls. Renders todo lists with progress bars, status indicators, and visual hierarchy instead of raw YAML output.

## Features

- **Full mode**: Shows all items with individual status and progress bar
- **Condensed mode**: Single-line progress summary with current task
- **Auto mode**: Automatically switches to condensed based on item count
- **Configurable**: Adjust thresholds, widths, borders, and more

## Display Examples

### Full Mode (default)

```
┌─ Tasks ──────────────────────────────────────────┐
│                                                  │
│ ✓ Set up environment                             │
│ ✓ Install dependencies                           │
│ ▶ Running tests                                  │
│ ○ Build project                                  │
│ ○ Deploy to staging                              │
│                                                  │
│ ████████████░░░░░░░░░░░░░░░░░░ 2/5               │
│                                                  │
└──────────────────────────────────────────────────┘
```

### Condensed Mode (when mode="auto" or mode="condensed")

```
┌─ Tasks ──────────────────────────────────────────────────┐
│ ████████████░░░░░░░░░░░░░░░░░░░░ 5/12 ▶ Validating schema │
└──────────────────────────────────────────────────────────┘
```

### All Complete

```
┌─ Tasks ──────────────────────────────────────────┐
│ ████████████████████████████████ 5/5 ✓ Complete  │
└──────────────────────────────────────────────────┘
```

## Configuration

```yaml
hooks:
  - module: hooks-todo-display
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/hooks-todo-display
    config:
      # Display modes: "full" (default), "condensed", "auto", "none"
      # mode: full
      
      # Switch to condensed above this item count (only used when mode="auto")
      # compact_threshold: 7
      
      # Show progress bar at bottom
      show_progress_bar: true
      
      # Width of the progress bar
      progress_bar_width: 24
      
      # Maximum width for item text
      max_width: 60
      
      # Show box border around display
      show_border: true
      
      # Title shown in the header
      title: Tasks
```

## Visual Encoding

| Status | Symbol | Color | Text Source |
|--------|--------|-------|-------------|
| Completed | ✓ | Dim green | `content` |
| In Progress | ▶ | Bold cyan | `activeForm` |
| Pending | ○ | Dim gray | `content` |

## Usage

This module is included in the `behavior-todo-reminder` bundle by default. To use it standalone:

```yaml
hooks:
  - module: hooks-todo-display
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/hooks-todo-display
    config:
      mode: full  # or "condensed", "auto", "none"
```

## Disabling

To disable the visual display and fall back to generic tool output:

```yaml
hooks:
  - module: hooks-todo-display
    source: git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=modules/hooks-todo-display
    config:
      mode: none
```
