"""Todo Display Hooks Module

Visual progress display for todo list tool calls. Renders todo lists with
progress bars, status indicators, and visual hierarchy instead of raw YAML.

Display Modes:
- full: Shows all items with progress bar (default for ≤7 items)
- condensed: Single-line progress summary (default for >7 items)
- auto: Switches between full/condensed based on item count
- none: Disables special rendering (falls through to generic tool display)
"""

import sys
from dataclasses import dataclass
from typing import Any

from amplifier_core import HookResult


def _write(text: str) -> None:
    """Write directly to stdout, bypassing Rich processing."""
    sys.stdout.write(text)
    sys.stdout.flush()


# ANSI color codes
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    
    # Status colors
    GREEN = "\033[32m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"
    
    # Bold variants
    BOLD_CYAN = "\033[1;36m"
    BOLD_GREEN = "\033[1;32m"
    
    # Dim variants
    DIM_GREEN = "\033[2;32m"
    DIM_GRAY = "\033[2;37m"


# Status symbols
class Symbols:
    COMPLETED = "✓"
    IN_PROGRESS = "▶"
    PENDING = "○"
    
    # Progress bar
    FILL = "█"
    EMPTY = "░"
    
    # Box drawing
    TOP_LEFT = "┌"
    TOP_RIGHT = "┐"
    BOTTOM_LEFT = "└"
    BOTTOM_RIGHT = "┘"
    HORIZONTAL = "─"
    VERTICAL = "│"
    HORIZONTAL_DOWN = "┬"


@dataclass
class TodoDisplayConfig:
    """Configuration for todo display rendering."""
    mode: str = "full"  # "full", "condensed", "auto", "none"
    compact_threshold: int = 7  # Switch to condensed at this count (only used when mode="auto")
    show_progress_bar: bool = True
    progress_bar_width: int = 24
    max_width: int = 60  # Max width for item text
    show_border: bool = True
    title: str = "Todo"


class TodoDisplayHooks:
    """Hook handlers for visual todo display."""
    
    def __init__(self, config: TodoDisplayConfig):
        self.config = config
        self._pending_todos: list[dict] | None = None
    
    async def handle_tool_pre(self, _event: str, data: dict[str, Any]) -> HookResult:
        """Capture todo data before tool execution for display after."""
        tool_name = data.get("tool_name", "")
        if tool_name != "todo":
            return HookResult(action="continue")
        
        # Capture the todos being sent (for create/update actions)
        tool_input = data.get("tool_input", {})
        if isinstance(tool_input, dict):
            self._pending_todos = tool_input.get("todos")
        
        return HookResult(action="continue")
    
    async def handle_tool_post(self, _event: str, data: dict[str, Any]) -> HookResult:
        """Render todo progress display after tool execution."""
        tool_name = data.get("tool_name", "")
        if tool_name != "todo":
            return HookResult(action="continue")
        
        if self.config.mode == "none":
            return HookResult(action="continue")
        
        result = data.get("tool_response", data.get("result", {}))
        if isinstance(result, dict):
            output = result.get("output", result)
            if isinstance(output, dict):
                status = output.get("status", "")
                if status in ("created", "updated", "listed"):
                    self._render_todo_display(output, data)
                    # Clear pending todos
                    self._pending_todos = None
                    # Signal that we handled the display
                    return HookResult(action="continue", metadata={"todo_displayed": True})
        
        self._pending_todos = None
        return HookResult(action="continue")
    
    def _render_todo_display(self, output: dict, data: dict) -> None:
        """Render the todo display based on configuration."""
        todos = output.get("todos", [])
        
        # If no todos in output, try to use pending todos or show summary
        if not todos and self._pending_todos:
            todos = self._pending_todos
        
        # Get counts
        count = output.get("count", len(todos))
        completed = output.get("completed", 0)
        in_progress = output.get("in_progress", 0)
        pending = output.get("pending", 0)
        
        # If we have todos, calculate counts from them
        if todos:
            completed = sum(1 for t in todos if t.get("status") == "completed")
            in_progress = sum(1 for t in todos if t.get("status") == "in_progress")
            pending = sum(1 for t in todos if t.get("status") == "pending")
            count = len(todos)
        
        # Determine display mode
        mode = self.config.mode
        if mode == "auto":
            mode = "condensed" if count > self.config.compact_threshold else "full"
        
        # Handle sub-agent indentation
        indent = self._get_indent(data)
        
        # Render based on mode
        if mode == "condensed" or not todos:
            self._render_condensed(count, completed, in_progress, pending, todos, indent)
        else:
            self._render_full(todos, count, completed, indent)
    
    def _get_indent(self, data: dict) -> str:
        """Get indentation prefix for sub-agent context."""
        session_id = data.get("session_id", "")
        # Check if this is a sub-agent (has parent session marker)
        if "_" in session_id and not session_id.startswith("session_"):
            return "  │ "
        return ""
    
    def _render_condensed(
        self, 
        count: int, 
        completed: int, 
        in_progress: int, 
        pending: int,
        todos: list[dict],
        indent: str
    ) -> None:
        """Render single-line condensed progress view."""
        width = self.config.max_width
        
        # Find current task
        current_task = ""
        for todo in todos:
            if todo.get("status") == "in_progress":
                current_task = todo.get("activeForm", todo.get("content", ""))
                break
        
        # Build progress bar
        progress_bar = self._build_progress_bar(completed, count)
        
        # Calculate available space for task text dynamically
        # Content format: " {progress_bar} {completed}/{count} {symbol} {task}"
        # Fixed elements: space(1) + progress_bar + space(1) + count_str + space(1) + symbol(1) + space(1)
        count_str = f"{completed}/{count}"
        fixed_len = 1 + self.config.progress_bar_width + 1 + len(count_str) + 1 + 1 + 1
        # Leave 1 char buffer for padding calculation
        max_task_len = max(10, width - fixed_len - 1)
        
        # Build status line
        if count == completed:
            # All done!
            status = f"{Colors.BOLD_GREEN}{Symbols.COMPLETED} Complete{Colors.RESET}"
        elif current_task:
            # Show current task (dynamically calculated truncation)
            status = f"{Colors.BOLD_CYAN}{Symbols.IN_PROGRESS} {self._truncate(current_task, max_task_len)}{Colors.RESET}"
        else:
            # No current task, show counts
            status = f"{Colors.DIM_GRAY}{pending} pending{Colors.RESET}"
        
        # Render
        if self.config.show_border:
            width = self.config.max_width
            title = f" {self.config.title} "
            top_line = f"{Symbols.TOP_LEFT}{Symbols.HORIZONTAL}{title}{Symbols.HORIZONTAL * (width - len(title) - 1)}{Symbols.TOP_RIGHT}"
            content = f"{Symbols.VERTICAL} {progress_bar} {completed}/{count} {status}"
            # Pad content to width
            visible_len = len(f" {self._strip_ansi(progress_bar)} {completed}/{count} {self._strip_ansi(status)}")
            padding = width - visible_len - 1
            content += " " * max(0, padding) + Symbols.VERTICAL
            bottom_line = f"{Symbols.BOTTOM_LEFT}{Symbols.HORIZONTAL * (width)}{Symbols.BOTTOM_RIGHT}"
            
            _write(f"\n{indent}{Colors.DIM_GRAY}{top_line}{Colors.RESET}\n")
            _write(f"{indent}{Colors.DIM_GRAY}{Symbols.VERTICAL}{Colors.RESET}{content[1:]}\n")
            _write(f"{indent}{Colors.DIM_GRAY}{bottom_line}{Colors.RESET}\n")
        else:
            _write(f"\n{indent}{progress_bar} {completed}/{count} {status}\n")
    
    def _render_full(self, todos: list[dict], count: int, completed: int, indent: str) -> None:
        """Render full list with all items visible."""
        width = self.config.max_width
        
        # Build lines
        lines = []
        
        # Header
        title = f" {self.config.title} "
        if self.config.show_border:
            lines.append(f"{Colors.DIM_GRAY}{Symbols.TOP_LEFT}{Symbols.HORIZONTAL}{title}{Symbols.HORIZONTAL * (width - len(title) - 1)}{Symbols.TOP_RIGHT}{Colors.RESET}")
            lines.append(f"{Colors.DIM_GRAY}{Symbols.VERTICAL}{Colors.RESET}{' ' * width}{Colors.DIM_GRAY}{Symbols.VERTICAL}{Colors.RESET}")
        
        # Items
        for todo in todos:
            status = todo.get("status", "pending")
            content = todo.get("content", "")
            active_form = todo.get("activeForm", content)
            
            if status == "completed":
                symbol = f"{Colors.DIM_GREEN}{Symbols.COMPLETED}{Colors.RESET}"
                text = f"{Colors.DIM_GREEN}{self._truncate(content, width - 4)}{Colors.RESET}"
            elif status == "in_progress":
                symbol = f"{Colors.BOLD_CYAN}{Symbols.IN_PROGRESS}{Colors.RESET}"
                text = f"{Colors.BOLD_CYAN}{self._truncate(active_form, width - 4)}{Colors.RESET}"
            else:  # pending
                symbol = f"{Colors.DIM_GRAY}{Symbols.PENDING}{Colors.RESET}"
                text = f"{Colors.DIM_GRAY}{self._truncate(content, width - 4)}{Colors.RESET}"
            
            if self.config.show_border:
                # Calculate padding (need to account for ANSI codes)
                visible_text = self._strip_ansi(f" {symbol} {text}")
                padding = width - len(visible_text)
                line = f"{Colors.DIM_GRAY}{Symbols.VERTICAL}{Colors.RESET} {symbol} {text}{' ' * max(0, padding)}{Colors.DIM_GRAY}{Symbols.VERTICAL}{Colors.RESET}"
            else:
                line = f" {symbol} {text}"
            
            lines.append(line)
        
        # Progress bar
        if self.config.show_progress_bar:
            if self.config.show_border:
                lines.append(f"{Colors.DIM_GRAY}{Symbols.VERTICAL}{Colors.RESET}{' ' * width}{Colors.DIM_GRAY}{Symbols.VERTICAL}{Colors.RESET}")
            
            progress_bar = self._build_progress_bar(completed, count)
            progress_text = f" {progress_bar} {completed}/{count}"
            
            if count == completed:
                progress_text += f" {Colors.BOLD_GREEN}{Symbols.COMPLETED} Complete{Colors.RESET}"
            
            if self.config.show_border:
                visible_len = len(self._strip_ansi(progress_text))
                padding = width - visible_len
                lines.append(f"{Colors.DIM_GRAY}{Symbols.VERTICAL}{Colors.RESET}{progress_text}{' ' * max(0, padding)}{Colors.DIM_GRAY}{Symbols.VERTICAL}{Colors.RESET}")
                lines.append(f"{Colors.DIM_GRAY}{Symbols.VERTICAL}{Colors.RESET}{' ' * width}{Colors.DIM_GRAY}{Symbols.VERTICAL}{Colors.RESET}")
            else:
                lines.append(progress_text)
        
        # Footer
        if self.config.show_border:
            lines.append(f"{Colors.DIM_GRAY}{Symbols.BOTTOM_LEFT}{Symbols.HORIZONTAL * (width)}{Symbols.BOTTOM_RIGHT}{Colors.RESET}")
        
        # Print all lines
        _write("\n")
        for line in lines:
            _write(f"{indent}{line}\n")
    
    def _build_progress_bar(self, completed: int, total: int) -> str:
        """Build a visual progress bar."""
        if total == 0:
            return f"{Colors.DIM_GRAY}{Symbols.EMPTY * self.config.progress_bar_width}{Colors.RESET}"
        
        filled = int((completed / total) * self.config.progress_bar_width)
        empty = self.config.progress_bar_width - filled
        
        return (
            f"{Colors.GREEN}{Symbols.FILL * filled}{Colors.RESET}"
            f"{Colors.DIM_GRAY}{Symbols.EMPTY * empty}{Colors.RESET}"
        )
    
    def _truncate(self, text: str, max_len: int) -> str:
        """Truncate text with ellipsis if too long."""
        if len(text) <= max_len:
            return text
        return text[:max_len - 1] + "…"
    
    def _strip_ansi(self, text: str) -> str:
        """Remove ANSI codes from text for length calculation."""
        import re
        return re.sub(r'\033\[[0-9;]*m', '', text)


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mount the todo display hooks module.
    
    Config options:
        mode: "full" | "condensed" | "auto" | "none" (default: "auto")
        compact_threshold: int (default: 7) - item count to switch to condensed
        show_progress_bar: bool (default: True)
        progress_bar_width: int (default: 24)
        max_width: int (default: 60)
        show_border: bool (default: True)
        title: str (default: "Todo")
    """
    config = config or {}
    
    display_config = TodoDisplayConfig(
        mode=config.get("mode", "full"),
        compact_threshold=config.get("compact_threshold", 7),
        show_progress_bar=config.get("show_progress_bar", True),
        progress_bar_width=config.get("progress_bar_width", 24),
        max_width=config.get("max_width", 60),
        show_border=config.get("show_border", True),
        title=config.get("title", "Todo"),
    )
    
    hooks = TodoDisplayHooks(display_config)
    
    # Register hooks with high priority to run before generic tool display
    # Lower number = higher priority
    coordinator.hooks.register("tool:pre", hooks.handle_tool_pre, priority=5)
    coordinator.hooks.register("tool:post", hooks.handle_tool_post, priority=5)
    
    return {
        "name": "hooks-todo-display",
        "version": "0.1.0",
        "description": "Visual progress display for todo lists",
        "config": {
            "mode": display_config.mode,
            "compact_threshold": display_config.compact_threshold,
        },
    }
