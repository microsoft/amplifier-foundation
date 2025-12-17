#!/usr/bin/env python3
"""
Example 12: Approval Gates in Action
=====================================

AUDIENCE: Everyone - security-conscious teams, regulated industries, cautious adopters
VALUE: Shows how Amplifier provides safety and control over AI actions
PATTERN: Interactive approval flow with granular control

What this demonstrates:
  - Safety mechanisms and human-in-the-loop control
  - Granular approval for sensitive operations
  - Audit trail of approved/rejected actions
  - Different approval strategies (always ask, auto-approve certain tools, etc.)

When you'd use this:
  - Production environments where AI shouldn't act without oversight
  - Regulated industries (healthcare, finance) requiring human approval
  - Learning/training scenarios where you want to see what AI plans to do
  - High-stakes operations (deployments, data modifications, API calls)
"""

import asyncio
from pathlib import Path
from typing import Any

from amplifier_core import AmplifierSession, ApprovalProvider, ApprovalRequest, ApprovalResponse
from amplifier_foundation import load_bundle


# ============================================================================
# Custom Approval System Implementation
# ============================================================================


class InteractiveApprovalSystem(ApprovalProvider):
    """
    Approval system that prompts the user interactively.
    
    Features:
    - Shows tool name, arguments, and reasoning
    - Allows approve/reject/approve-all decisions
    - Maintains audit trail of decisions
    - Supports auto-approval rules
    """
    
    def __init__(self, auto_approve_tools: list[str] | None = None):
        self.auto_approve_tools = auto_approve_tools or []
        self.audit_trail: list[dict[str, Any]] = []
        self.approve_all = False
        
    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        """Request approval for a tool use."""
        
        # Check auto-approve rules
        if self.approve_all or request.tool_name in self.auto_approve_tools:
            response = ApprovalResponse(
                approved=True,
                reason="Auto-approved",
                remember=False,
            )
            self._log_decision(request, response, auto=True)
            return response
        
        # Interactive approval
        print("\n" + "="*80)
        print("🚨 APPROVAL REQUIRED")
        print("="*80)
        print(f"\n📋 Tool: {request.tool_name}")
        print(f"📝 Action: {request.action}")
        print(f"⚠️  Risk Level: {request.risk_level}")
        print(f"\n🔧 Details:")
        for key, value in request.details.items():
            # Truncate long values for display
            display_value = str(value)
            if len(display_value) > 200:
                display_value = display_value[:200] + "..."
            print(f"   {key}: {display_value}")
        
        print("\n" + "-"*80)
        print("Options:")
        print("  [y] Approve")
        print("  [n] Reject")
        print("  [a] Approve ALL remaining requests")
        print("  [i] Approve and ignore this tool in future")
        print("-"*80)
        
        while True:
            choice = input("\nYour decision: ").strip().lower()
            
            if choice == 'y':
                response = ApprovalResponse(
                    approved=True,
                    reason="User approved",
                    remember=False,
                )
                break
            elif choice == 'n':
                reason = input("Reason for rejection (optional): ").strip()
                response = ApprovalResponse(
                    approved=False,
                    reason=reason or "User rejected",
                    remember=False,
                )
                break
            elif choice == 'a':
                self.approve_all = True
                response = ApprovalResponse(
                    approved=True,
                    reason="Approved all",
                    remember=False,
                )
                print("\n✅ All future requests will be auto-approved")
                break
            elif choice == 'i':
                self.auto_approve_tools.append(request.tool_name)
                response = ApprovalResponse(
                    approved=True,
                    reason=f"Auto-approving {request.tool_name}",
                    remember=True,
                )
                print(f"\n✅ Will auto-approve all future '{request.tool_name}' requests")
                break
            else:
                print("❌ Invalid choice. Please enter y, n, a, or i.")
        
        self._log_decision(request, response, auto=False)
        return response
    
    def _log_decision(self, request: ApprovalRequest, response: ApprovalResponse, auto: bool):
        """Log approval decision to audit trail."""
        self.audit_trail.append({
            "tool": request.tool_name,
            "action": request.action,
            "approved": response.approved,
            "auto": auto,
            "reason": response.reason,
            "risk_level": request.risk_level,
        })
    
    def print_audit_trail(self):
        """Print the audit trail of all approval decisions."""
        if not self.audit_trail:
            print("\n📊 No approval requests")
            return
            
        print("\n" + "="*80)
        print("📊 AUDIT TRAIL")
        print("="*80)
        for i, entry in enumerate(self.audit_trail, 1):
            status_emoji = "✅" if entry["approved"] else "❌"
            auto_label = " (auto)" if entry["auto"] else ""
            print(f"\n{i}. {status_emoji} {entry['tool']}{auto_label}")
            print(f"   Action: {entry['action']}")
            print(f"   Risk: {entry['risk_level']}")
            if entry["reason"]:
                print(f"   Reason: {entry['reason']}")


# ============================================================================
# Demo Scenarios
# ============================================================================


async def scenario_file_operations():
    """
    Scenario: File operations requiring approval.
    
    Demonstrates approval for potentially destructive operations.
    """
    print("\n" + "="*80)
    print("SCENARIO 1: File Operations with Approval")
    print("="*80)
    print("\nThis scenario asks the AI to create and modify files.")
    print("You'll be prompted to approve each file operation.")
    print("\nTask: Create a simple Python module with tests")
    print("-"*80)
    
    # Load foundation bundle
    foundation_path = Path(__file__).parent.parent
    foundation = await load_bundle(str(foundation_path))
    
    # Create approval system (require approval for all tools)
    approval_system = InteractiveApprovalSystem()
    
    # Compose mount plan
    mount_plan = foundation.to_mount_plan()
    
    # Create session with approval system
    session = AmplifierSession(
        config=mount_plan,
        approval_system=approval_system,
    )
    
    await session.initialize()
    
    prompt = """Create a simple Python module in the current directory:
    
1. Create a file called `calculator.py` with basic add/subtract functions
2. Create a file called `test_calculator.py` with tests for those functions

Please proceed step by step."""
    
    try:
        print("\n⏳ Executing task...")
        result = await session.execute(prompt)
        print("\n" + "="*80)
        print("✅ Task completed")
        print("="*80)
        
    finally:
        approval_system.print_audit_trail()
        await session.cleanup()


async def scenario_selective_approval():
    """
    Scenario: Auto-approve safe tools, require approval for risky ones.
    
    Demonstrates selective approval policies.
    """
    print("\n" + "="*80)
    print("SCENARIO 2: Selective Auto-Approval")
    print("="*80)
    print("\nThis scenario auto-approves 'safe' tools (read operations)")
    print("but requires approval for 'risky' tools (write operations).")
    print("\nTask: Analyze project structure and create a README")
    print("-"*80)
    
    # Load foundation bundle
    foundation_path = Path(__file__).parent.parent
    foundation = await load_bundle(str(foundation_path))
    
    # Create approval system (auto-approve read-only tools)
    approval_system = InteractiveApprovalSystem(
        auto_approve_tools=["tool-read-file", "tool-glob", "tool-grep"]
    )
    
    # Compose mount plan
    mount_plan = foundation.to_mount_plan()
    
    # Create session with approval system
    session = AmplifierSession(
        config=mount_plan,
        approval_system=approval_system,
    )
    
    await session.initialize()
    
    prompt = """Analyze the current project structure:

1. Use glob to find all Python files
2. Read a few key files to understand the project
3. Create a simple README.md summarizing the project

The read operations should auto-approve, but you'll need approval for the write."""
    
    try:
        print("\n⏳ Executing task...")
        result = await session.execute(prompt)
        print("\n" + "="*80)
        print("✅ Task completed")
        print("="*80)
        
    finally:
        approval_system.print_audit_trail()
        await session.cleanup()


async def scenario_api_calls():
    """
    Scenario: Require approval for external API calls.
    
    Demonstrates approval for operations that affect external systems.
    """
    print("\n" + "="*80)
    print("SCENARIO 3: External API Call Approval")
    print("="*80)
    print("\nThis scenario requires approval for any bash commands")
    print("(which could make network requests or modify system state).")
    print("\nTask: Check system information")
    print("-"*80)
    
    # Load foundation bundle
    foundation_path = Path(__file__).parent.parent
    foundation = await load_bundle(str(foundation_path))
    
    # Create approval system
    approval_system = InteractiveApprovalSystem()
    
    # Compose mount plan
    mount_plan = foundation.to_mount_plan()
    
    # Create session with approval system
    session = AmplifierSession(
        config=mount_plan,
        approval_system=approval_system,
    )
    
    await session.initialize()
    
    prompt = """Run these system commands to gather information:

1. Check Python version: python --version
2. Check current directory: pwd
3. List files in current directory: ls -la

Each bash command will require approval."""
    
    try:
        print("\n⏳ Executing task...")
        result = await session.execute(prompt)
        print("\n" + "="*80)
        print("✅ Task completed")
        print("="*80)
        
    finally:
        approval_system.print_audit_trail()
        await session.cleanup()


# ============================================================================
# Interactive Menu
# ============================================================================


async def main():
    """Run interactive demo menu."""
    print("\n" + "="*80)
    print("🚀 Approval Gates in Action")
    print("="*80)
    print("\nVALUE: Shows how Amplifier provides safety and control over AI actions")
    print("AUDIENCE: Security-conscious teams, regulated industries")
    print("\nWhat this demonstrates:")
    print("  - Human-in-the-loop approval for AI actions")
    print("  - Granular control over which tools require approval")
    print("  - Audit trail of all approval decisions")
    print("  - Flexible approval policies (always ask, auto-approve, selective)")
    
    scenarios = [
        ("File Operations (approve each write)", scenario_file_operations),
        ("Selective Approval (auto-approve reads)", scenario_selective_approval),
        ("API/System Calls (approve bash commands)", scenario_api_calls),
    ]
    
    print("\n" + "="*80)
    print("Choose a scenario:")
    print("="*80)
    for i, (name, _) in enumerate(scenarios, 1):
        print(f"  {i}. {name}")
    print("  q. Quit")
    print("-"*80)
    
    choice = input("\nYour choice: ").strip().lower()
    
    if choice == 'q':
        print("\n👋 Goodbye!")
        return
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(scenarios):
            _, scenario_func = scenarios[idx]
            await scenario_func()
        else:
            print("\n❌ Invalid choice")
    except ValueError:
        print("\n❌ Invalid choice")
    
    print("\n" + "="*80)
    print("💡 KEY TAKEAWAYS")
    print("="*80)
    print("""
1. **Safety First**: Approval gates prevent AI from taking actions without oversight
2. **Flexible Policies**: Auto-approve safe operations, require approval for risky ones
3. **Audit Trail**: Every decision is logged for compliance and debugging
4. **Production-Ready**: Use this pattern in production to maintain control
5. **Granular Control**: Approve/reject individual operations, not all-or-nothing

**When to use approval gates:**
- Production environments with sensitive operations
- Regulated industries requiring human oversight
- Training environments to understand AI behavior
- High-stakes operations (deployments, financial transactions)
- Any time you want explicit control over AI actions
""")


if __name__ == "__main__":
    asyncio.run(main())
