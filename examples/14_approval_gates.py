#!/usr/bin/env python3
"""
Example 12: Approval Gates in Action
====================================

VALUE PROPOSITION:
Demonstrates safety controls and human-in-the-loop capability. Dangerous operations
require explicit approval, showing how to build trust and control into AI systems.

WHAT YOU'LL LEARN:
- ApprovalProvider protocol (kernel mechanism)
- Interactive approval flow
- Risk management patterns
- How the kernel enforces safety gates

AUDIENCE:
Developers (safety-focused), PMs (governance), anyone concerned about AI safety
"""

import asyncio
import os
from pathlib import Path
from typing import Any

from amplifier_core import ToolResult
from amplifier_foundation import Bundle, load_bundle


# =============================================================================
# SECTION 1: Approval Provider Implementation
# =============================================================================

class InteractiveApprovalSystem:
    """Interactive approval system that prompts user before dangerous operations.
    
    This implements the ApprovalSystem protocol, which the kernel calls
    automatically when hooks return action="ask_user".
    """
    
    def __init__(self):
        """Initialize approval system."""
        self.approved_operations = []
        self.denied_operations = []
    
    async def request_approval(
        self, prompt: str, options: list[str], timeout: float, default: str
    ) -> str:
        """Request user approval for an operation.
        
        This method is called by the kernel when a hook returns action="ask_user".
        
        Args:
            prompt: Question to ask user
            options: Available choices
            timeout: Seconds to wait (we'll ignore this for simplicity)
            default: Default action on timeout
            
        Returns:
            Selected option string (one of options)
        """
        print("\n" + "="*60)
        print("⚠️  APPROVAL REQUIRED")
        print("="*60)
        print(f"\n📝 {prompt}")
        print(f"\n💡 Options: {', '.join(options)}")
        print("⚠️  This operation has been flagged as potentially dangerous.")
        print("Please review carefully before approving.")
        
        while True:
            response = input(f"\n❓ Your choice [{'/'.join(options)}]: ").strip()
            
            if response in options:
                if "allow" in response.lower():
                    print("✅ Operation APPROVED")
                    self.approved_operations.append({"prompt": prompt})
                    return response
                else:
                    print("🚫 Operation DENIED - keeping your data safe!")
                    self.denied_operations.append({"prompt": prompt})
                    return response
            else:
                print(f"Invalid response. Please enter one of: {', '.join(options)}")
    
    def get_summary(self) -> dict[str, Any]:
        """Get summary of approval decisions."""
        return {
            "approved_count": len(self.approved_operations),
            "denied_count": len(self.denied_operations),
            "approved": self.approved_operations,
            "denied": self.denied_operations
        }


# =============================================================================
# SECTION 2: Approval Hook (Intercepts Dangerous Operations)
# =============================================================================

class ApprovalHook:
    """Hook that intercepts dangerous operations and requests approval.
    
    This hook checks if operations are dangerous and returns action="ask_user"
    to trigger the kernel's approval system.
    """
    
    def __init__(self, dangerous_patterns: list[str] = None):
        """Initialize approval hook.
        
        Args:
            dangerous_patterns: List of strings indicating dangerous operations
        """
        self.dangerous_patterns = dangerous_patterns or [
            "delete", "remove", "rm", "unlink",
            "drop", "truncate", "destroy", "cleanup"
        ]
    
    def is_dangerous(self, tool_name: str, tool_input: dict) -> bool:
        """Check if an operation is dangerous."""
        # Check tool name
        tool_lower = tool_name.lower()
        for pattern in self.dangerous_patterns:
            if pattern in tool_lower:
                return True
        
        # Check tool input
        input_str = str(tool_input).lower()
        for pattern in self.dangerous_patterns:
            if pattern in input_str:
                return True
        
        return False
    
    async def __call__(self, event: str, data: dict) -> "HookResult":
        """Hook handler that intercepts tool:pre events.
        
        Returns HookResult with action="ask_user" for dangerous operations,
        which triggers the kernel to call the approval system.
        """
        from amplifier_core import HookResult
        
        # Only intercept tool:pre events
        if event != "tool:pre":
            return HookResult(action="continue")
        
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input", {})
        
        # Check if operation is dangerous
        if not self.is_dangerous(tool_name, tool_input):
            # Safe operation, allow it
            return HookResult(action="continue")
        
        # Dangerous operation - ask user for approval
        # The kernel will call approval_system.request_approval()
        return HookResult(
            action="ask_user",
            approval_prompt=f"Allow {tool_name} to execute with input {tool_input}?",
            approval_options=["Allow", "Deny"],
            approval_default="deny"
        )


# =============================================================================
# SECTION 3: File Cleanup Tool (Demonstrates Dangerous Operation)
# =============================================================================

class FileCleanupTool:
    """Custom tool that performs file cleanup operations.
    
    This tool demonstrates dangerous operations that require approval.
    """
    
    @property
    def name(self) -> str:
        return "file_cleanup"
    
    @property
    def description(self) -> str:
        return """Clean up files in a directory by removing temporary and old files.

Input: {"directory": "path/to/dir", "patterns": ["*.tmp", "*.log"]}
Returns: List of files that would be deleted."""
    
    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Directory to clean up"
                },
                "patterns": {
                    "type": "array",
                    "description": "File patterns to match (e.g., *.tmp, *.log)",
                    "items": {"type": "string"}
                }
            },
            "required": ["directory"]
        }
    
    # Mark this tool as requiring approval
    requires_approval = True
    approval_risk_level = "high"
    
    async def execute(self, input: dict[str, Any]) -> ToolResult:
        """Execute file cleanup."""
        directory = input.get("directory", "")
        patterns = input.get("patterns", ["*.tmp", "*.log"])
        
        if not directory:
            return ToolResult(
                success=False,
                error={"message": "No directory provided"}
            )
        
        # For demo, just list what would be cleaned
        mock_files = [
            "temp_cache.tmp",
            "old_debug.log",
            "session_2024.log"
        ]
        
        result = f"""File cleanup scan complete for: {directory}

Files matching patterns {patterns}:
"""
        for f in mock_files:
            result += f"\n  - {f}"
        
        result += "\n\n(Demo mode: files not actually deleted)"
        
        return ToolResult(
            success=True,
            output=result
        )


# =============================================================================
# SECTION 3: Demo Implementation
# =============================================================================

async def demo_approval_gates():
    """Demonstrate approval gates in action."""
    
    print("\n" + "="*60)
    print("Demo: Approval Gates Protecting Dangerous Operations")
    print("="*60)
    
    # Load foundation and provider
    foundation_path = Path(__file__).parent.parent
    foundation = await load_bundle(str(foundation_path))
    provider = await load_bundle(str(foundation_path / "providers" / "anthropic-sonnet.yaml"))
    
    # Add filesystem tool
    tools_config = Bundle(
        name="tools-with-filesystem",
        version="1.0.0",
        tools=[
            {
                "module": "tool-filesystem",
                "source": "git+https://github.com/microsoft/amplifier-module-tool-filesystem@main"
            }
        ]
    )
    
    composed = foundation.compose(provider).compose(tools_config)
    
    print("⏳ Preparing session...")
    prepared = await composed.prepare()
    
    # Create approval system
    approval_system = InteractiveApprovalSystem()
    
    # Create session WITH the approval system (kernel mechanism)
    session = await prepared.create_session(
        approval_system=approval_system  # <-- Pass to kernel
    )
    
    # Create and register approval hook
    # The hook returns action="ask_user" which triggers the approval provider
    approval_hook = ApprovalHook()
    session.coordinator.hooks.register("tool:pre", approval_hook.__call__)
    
    # Register custom file cleanup tool
    cleanup_tool = FileCleanupTool()
    await session.coordinator.mount("tools", cleanup_tool, name=cleanup_tool.name)
    
    print("✅ Session ready with approval gates enabled")
    print("\nThe agent will suggest file operations that require approval.")
    print("You'll be prompted to approve or deny each dangerous operation.")
    
    async with session:
        # Scenario 1: Agent tries to clean up files (requires approval)
        print("\n" + "="*60)
        print("Scenario 1: File Cleanup Operation (Requires Approval)")
        print("="*60)
        print("📝 Asking agent to clean up temp files...")
        
        response1 = await session.execute(
            "Use the file_cleanup tool to clean up temp files in the /tmp directory. "
            "Look for *.tmp and *.log files."
        )
        print(f"\n📤 Agent response:\n{response1}")
        
        # Scenario 2: Agent tries to read a file (safe operation, no approval)
        print("\n" + "="*60)
        print("Scenario 2: Safe Operation (No Approval Required)")
        print("="*60)
        print("📝 Asking agent to read a file (safe operation)...")
        
        response2 = await session.execute(
            "Read the file at ./examples/README.md and tell me what the first example is about."
        )
        print(f"\n📤 Agent response:\n{response2[:300]}...")
    
    # Show summary
    summary = approval_system.get_summary()
    print("\n" + "="*60)
    print("📊 Approval Summary")
    print("="*60)
    print(f"✅ Approved operations: {summary['approved_count']}")
    print(f"🚫 Denied operations: {summary['denied_count']}")
    
    if summary['approved']:
        print("\nApproved:")
        for op in summary['approved']:
            print(f"  - {op['prompt']}")
    
    if summary['denied']:
        print("\nDenied:")
        for op in summary['denied']:
            print(f"  - {op['prompt']}")


# =============================================================================
# SECTION 4: Main Entry Point
# =============================================================================

async def main():
    """Main entry point."""
    
    print("🔒 Approval Gates in Action")
    print("="*60)
    print("\nVALUE: Build trust and control with human-in-the-loop AI")
    print("AUDIENCE: Developers (safety), PMs (governance)")
    print("\nWhat this demonstrates:")
    print("  - ApprovalProvider protocol (kernel mechanism)")
    print("  - Interactive approval flow")
    print("  - Risk management patterns")
    print("  - Graceful operation denial")
    
    # Check prerequisites
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("\n❌ ERROR: Set ANTHROPIC_API_KEY environment variable")
        print("\nExample:")
        print("  export ANTHROPIC_API_KEY='your-key-here'")
        print("  python 12_approval_gates.py")
        return
    
    await demo_approval_gates()
    
    # Summary
    print("\n" + "="*60)
    print("📚 WHAT YOU LEARNED:")
    print("="*60)
    print("  ✓ ApprovalProvider protocol for handling approval UI")
    print("  ✓ Hooks return action='ask_user' to trigger approval")
    print("  ✓ Kernel calls approval_system.request_approval() automatically")
    print("  ✓ Two-part system: Hook detects danger + Provider handles UI")
    print("  ✓ Safety patterns for AI systems")
    
    print("\n💡 KEY INSIGHT:")
    print("  Approval gates use BOTH hooks and approval providers:")
    print("  1. Hook intercepts operations and returns action='ask_user'")
    print("  2. Kernel calls approval_provider.request_approval()")
    print("  3. Provider shows UI and gets user decision")
    print("  4. Kernel enforces the decision")
    
    print("\n💡 NEXT: Try 16_event_debugging.py to see all events in detail")


if __name__ == "__main__":
    asyncio.run(main())
