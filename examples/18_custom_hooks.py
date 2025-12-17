#!/usr/bin/env python3
"""
Example 18: Custom Hook Library
================================

AUDIENCE: Developers building production Amplifier applications
VALUE: Shows how to create reusable, composable hooks for common patterns
PATTERN: Hook library, middleware patterns, aspect-oriented programming

What this demonstrates:
  - Building custom hooks for observability and control
  - Composing multiple hooks together
  - Performance monitoring and metrics collection
  - Error tracking and recovery
  - Audit logging and compliance

When you'd use this:
  - Production applications needing observability
  - Building reusable components across projects
  - Implementing cross-cutting concerns (logging, metrics, tracing)
  - Creating domain-specific monitoring
"""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from amplifier_core import AmplifierSession, HookResult
from amplifier_foundation import load_bundle


# ============================================================================
# Performance Monitoring Hooks
# ============================================================================


class PerformanceMonitor:
    """
    Hook that tracks performance metrics for tools and LLM calls.
    
    Collects:
    - Tool execution time
    - LLM response time
    - Token usage
    - Error rates
    """
    
    def __init__(self):
        self.metrics: dict[str, list[float]] = {}
        self.tool_timings: dict[str, list[float]] = {}
        self.token_usage: dict[str, int] = {"input": 0, "output": 0}
        self.errors: list[dict[str, Any]] = []
        self.start_times: dict[str, float] = {}
    
    async def on_tool_pre(self, event: str, data: dict[str, Any]) -> HookResult:
        """Record tool start time."""
        tool_name = data.get("name", "unknown")
        self.start_times[f"tool:{tool_name}"] = time.time()
        return HookResult(action="continue")
    
    async def on_tool_post(self, event: str, data: dict[str, Any]) -> HookResult:
        """Record tool completion time."""
        tool_name = data.get("name", "unknown")
        key = f"tool:{tool_name}"
        
        if key in self.start_times:
            duration = time.time() - self.start_times[key]
            if tool_name not in self.tool_timings:
                self.tool_timings[tool_name] = []
            self.tool_timings[tool_name].append(duration)
            del self.start_times[key]
        
        return HookResult(action="continue")
    
    async def on_provider_post(self, event: str, data: dict[str, Any]) -> HookResult:
        """Track token usage from provider responses."""
        usage = data.get("usage", {})
        self.token_usage["input"] += usage.get("input_tokens", 0)
        self.token_usage["output"] += usage.get("output_tokens", 0)
        return HookResult(action="continue")
    
    async def on_error(self, event: str, data: dict[str, Any]) -> HookResult:
        """Track errors."""
        self.errors.append({
            "event": event,
            "timestamp": datetime.now().isoformat(),
            "data": data,
        })
        return HookResult(action="continue")
    
    def print_report(self):
        """Print performance report."""
        print("\n" + "="*80)
        print("📊 PERFORMANCE REPORT")
        print("="*80)
        
        # Tool timings
        if self.tool_timings:
            print("\n🔧 Tool Performance:")
            for tool, timings in sorted(self.tool_timings.items()):
                avg_time = sum(timings) / len(timings)
                total_time = sum(timings)
                print(f"  {tool:30} {len(timings):3} calls, "
                      f"avg: {avg_time:.3f}s, total: {total_time:.3f}s")
        
        # Token usage
        print(f"\n💰 Token Usage:")
        print(f"  Input tokens:  {self.token_usage['input']:,}")
        print(f"  Output tokens: {self.token_usage['output']:,}")
        print(f"  Total tokens:  {sum(self.token_usage.values()):,}")
        
        # Errors
        if self.errors:
            print(f"\n❌ Errors: {len(self.errors)}")
            for error in self.errors[:3]:  # Show first 3
                print(f"  - {error['event']}: {error['data'].get('error', 'Unknown')}")


class RateLimiter:
    """
    Hook that implements rate limiting for tools.
    
    Prevents tool spam and enforces reasonable usage patterns.
    """
    
    def __init__(self, max_calls_per_minute: int = 10):
        self.max_calls = max_calls_per_minute
        self.call_times: list[float] = []
    
    async def check_rate_limit(self, event: str, data: dict[str, Any]) -> HookResult:
        """Check if rate limit exceeded."""
        now = time.time()
        
        # Remove calls older than 1 minute
        self.call_times = [t for t in self.call_times if now - t < 60]
        
        if len(self.call_times) >= self.max_calls:
            print(f"\n⚠️  RATE LIMIT: Tool {data.get('name')} blocked "
                  f"({len(self.call_times)} calls in last minute)")
            # Could raise an error or return stop action
            # For demo, just warn and continue
        
        self.call_times.append(now)
        return HookResult(action="continue")


class CostTracker:
    """
    Hook that tracks API costs in real-time.
    
    Provides cost estimates based on token usage and model pricing.
    """
    
    # Simplified pricing (per 1M tokens)
    PRICING = {
        "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
        "claude-haiku": {"input": 0.25, "output": 1.25},
        "claude-opus": {"input": 15.00, "output": 75.00},
        "gpt-4": {"input": 30.00, "output": 60.00},
    }
    
    def __init__(self, model: str = "claude-sonnet-4-5"):
        self.model = model
        self.total_cost = 0.0
        self.breakdown: dict[str, float] = {"input": 0.0, "output": 0.0}
    
    async def track_usage(self, event: str, data: dict[str, Any]) -> HookResult:
        """Calculate cost from token usage."""
        usage = data.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        
        pricing = self.PRICING.get(self.model, self.PRICING["claude-sonnet-4-5"])
        
        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]
        
        self.breakdown["input"] += input_cost
        self.breakdown["output"] += output_cost
        self.total_cost += input_cost + output_cost
        
        return HookResult(action="continue")
    
    def print_summary(self):
        """Print cost summary."""
        print(f"\n💵 Cost Summary (Model: {self.model})")
        print(f"  Input:  ${self.breakdown['input']:.4f}")
        print(f"  Output: ${self.breakdown['output']:.4f}")
        print(f"  Total:  ${self.total_cost:.4f}")


# ============================================================================
# Audit and Compliance Hooks
# ============================================================================


class AuditLogger:
    """
    Hook that creates detailed audit logs for compliance.
    
    Records all actions with timestamps, user context, and outcomes.
    """
    
    def __init__(self, log_file: str | None = None):
        self.log_file = log_file
        self.entries: list[dict[str, Any]] = []
    
    async def log_action(self, event: str, data: dict[str, Any]) -> HookResult:
        """Log an action to the audit trail."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event": event,
            "data": self._sanitize_data(data),
        }
        
        self.entries.append(entry)
        
        # Write to file if specified
        if self.log_file:
            with open(self.log_file, 'a') as f:
                f.write(json.dumps(entry) + "\n")
        
        return HookResult(action="continue")
    
    def _sanitize_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Remove sensitive information from logs."""
        # In production, implement proper PII redaction
        sensitive_keys = ["password", "api_key", "secret", "token"]
        return {k: v for k, v in data.items() if k.lower() not in sensitive_keys}
    
    def print_summary(self):
        """Print audit summary."""
        print(f"\n📋 Audit Summary: {len(self.entries)} events logged")
        if self.log_file:
            print(f"   Log file: {self.log_file}")


class ContentFilter:
    """
    Hook that filters inappropriate content in prompts and responses.
    
    Useful for compliance and safety.
    """
    
    BLOCKED_PATTERNS = ["password", "api_key", "secret"]
    
    async def filter_prompt(self, event: str, data: dict[str, Any]) -> HookResult:
        """Check prompts for sensitive content."""
        if "prompt" in data:
            prompt = data["prompt"].lower()
            for pattern in self.BLOCKED_PATTERNS:
                if pattern in prompt:
                    print(f"\n⚠️  CONTENT FILTER: Blocked pattern '{pattern}' in prompt")
                    # In production, could return HookResult(action="stop")
        
        return HookResult(action="continue")


# ============================================================================
# Error Handling and Recovery Hooks
# ============================================================================


class RetryHandler:
    """
    Hook that implements automatic retry logic for transient failures.
    
    Handles rate limits, network errors, and temporary outages.
    """
    
    def __init__(self, max_retries: int = 3, backoff_factor: float = 2.0):
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.retry_counts: dict[str, int] = {}
    
    async def handle_error(self, event: str, data: dict[str, Any]) -> HookResult:
        """Handle errors with retry logic."""
        error = data.get("error", "")
        
        # Check if this is a retryable error
        retryable = any(pattern in str(error).lower() 
                       for pattern in ["rate limit", "timeout", "503", "429"])
        
        if retryable:
            key = data.get("name", event)
            retry_count = self.retry_counts.get(key, 0)
            
            if retry_count < self.max_retries:
                self.retry_counts[key] = retry_count + 1
                wait_time = (self.backoff_factor ** retry_count)
                
                print(f"\n🔄 RETRY: Attempt {retry_count + 1}/{self.max_retries} "
                      f"after {wait_time:.1f}s")
                
                await asyncio.sleep(wait_time)
                return HookResult(action="retry")  # Note: Amplifier would need to support this
        
        return HookResult(action="continue")


class FallbackHandler:
    """
    Hook that implements fallback strategies when primary approach fails.
    
    Example: Try tool A, fall back to tool B if A fails.
    """
    
    def __init__(self):
        self.fallbacks: dict[str, str] = {
            "tool-web": "tool-bash",  # If web fails, try bash curl
        }
    
    async def handle_failure(self, event: str, data: dict[str, Any]) -> HookResult:
        """Suggest fallback when a tool fails."""
        tool_name = data.get("name", "")
        
        if tool_name in self.fallbacks:
            fallback = self.fallbacks[tool_name]
            print(f"\n💡 FALLBACK: {tool_name} failed, consider using {fallback}")
        
        return HookResult(action="continue")


# ============================================================================
# Demo Scenarios
# ============================================================================


async def scenario_performance_monitoring():
    """Demonstrate performance monitoring hooks."""
    print("\n" + "="*80)
    print("SCENARIO 1: Performance Monitoring")
    print("="*80)
    print("\nTrack performance metrics during execution.")
    print("-"*80)
    
    foundation_path = Path(__file__).parent.parent
    foundation = await load_bundle(str(foundation_path))
    mount_plan = foundation.to_mount_plan()
    
    # Create performance monitor
    perf_monitor = PerformanceMonitor()
    
    # Create session and register hooks
    session = AmplifierSession(config=mount_plan)
    session.coordinator.hooks.register("tool:pre", perf_monitor.on_tool_pre)
    session.coordinator.hooks.register("tool:post", perf_monitor.on_tool_post)
    session.coordinator.hooks.register("provider:post", perf_monitor.on_provider_post)
    session.coordinator.hooks.register("*:error", perf_monitor.on_error)
    
    await session.initialize()
    
    prompt = "Use glob to find all Python files, then read the first one you find."
    
    print(f"\n📝 Prompt: {prompt}\n")
    
    try:
        result = await session.execute(prompt)
        print("\n✅ Task completed")
        
        # Show performance report
        perf_monitor.print_report()
        
    finally:
        await session.cleanup()


async def scenario_cost_tracking():
    """Demonstrate real-time cost tracking."""
    print("\n" + "="*80)
    print("SCENARIO 2: Real-Time Cost Tracking")
    print("="*80)
    print("\nTrack API costs as the session executes.")
    print("-"*80)
    
    foundation_path = Path(__file__).parent.parent
    foundation = await load_bundle(str(foundation_path))
    mount_plan = foundation.to_mount_plan()
    
    # Create cost tracker
    cost_tracker = CostTracker(model="claude-sonnet-4-5")
    
    # Create session and register hooks
    session = AmplifierSession(config=mount_plan)
    session.coordinator.hooks.register("provider:post", cost_tracker.track_usage)
    
    await session.initialize()
    
    prompt = "Explain what Amplifier is and why it's useful in 3 sentences."
    
    print(f"\n📝 Prompt: {prompt}\n")
    
    try:
        result = await session.execute(prompt)
        print("\n✅ Task completed")
        
        # Show cost summary
        cost_tracker.print_summary()
        
    finally:
        await session.cleanup()


async def scenario_audit_logging():
    """Demonstrate audit logging for compliance."""
    print("\n" + "="*80)
    print("SCENARIO 3: Audit Logging")
    print("="*80)
    print("\nCreate detailed audit logs of all actions.")
    print("-"*80)
    
    foundation_path = Path(__file__).parent.parent
    foundation = await load_bundle(str(foundation_path))
    mount_plan = foundation.to_mount_plan()
    
    # Create audit logger
    log_file = f"audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    audit_logger = AuditLogger(log_file=log_file)
    
    # Create session and register hooks
    session = AmplifierSession(config=mount_plan)
    session.coordinator.hooks.register("*", audit_logger.log_action)
    
    await session.initialize()
    
    prompt = "List files in the current directory."
    
    print(f"\n📝 Prompt: {prompt}\n")
    
    try:
        result = await session.execute(prompt)
        print("\n✅ Task completed")
        
        # Show audit summary
        audit_logger.print_summary()
        
    finally:
        await session.cleanup()


async def scenario_composed_hooks():
    """Demonstrate composing multiple hooks together."""
    print("\n" + "="*80)
    print("SCENARIO 4: Composed Hooks")
    print("="*80)
    print("\nCombine multiple hooks for comprehensive monitoring.")
    print("-"*80)
    
    foundation_path = Path(__file__).parent.parent
    foundation = await load_bundle(str(foundation_path))
    mount_plan = foundation.to_mount_plan()
    
    # Create multiple hooks
    perf_monitor = PerformanceMonitor()
    cost_tracker = CostTracker()
    rate_limiter = RateLimiter(max_calls_per_minute=10)
    
    # Create session and register all hooks
    session = AmplifierSession(config=mount_plan)
    
    # Performance monitoring
    session.coordinator.hooks.register("tool:pre", perf_monitor.on_tool_pre)
    session.coordinator.hooks.register("tool:post", perf_monitor.on_tool_post)
    session.coordinator.hooks.register("provider:post", perf_monitor.on_provider_post)
    
    # Cost tracking
    session.coordinator.hooks.register("provider:post", cost_tracker.track_usage)
    
    # Rate limiting
    session.coordinator.hooks.register("tool:pre", rate_limiter.check_rate_limit)
    
    await session.initialize()
    
    prompt = "Find all Python files and show me their sizes."
    
    print(f"\n📝 Prompt: {prompt}\n")
    
    try:
        result = await session.execute(prompt)
        print("\n✅ Task completed")
        
        # Show all reports
        perf_monitor.print_report()
        cost_tracker.print_summary()
        
    finally:
        await session.cleanup()


# ============================================================================
# Interactive Menu
# ============================================================================


async def main():
    """Run interactive demo menu."""
    print("\n" + "="*80)
    print("🎣 Custom Hook Library")
    print("="*80)
    print("\nVALUE: Build reusable hooks for production applications")
    print("AUDIENCE: Developers building with Amplifier")
    print("\nWhat this demonstrates:")
    print("  - Performance monitoring and metrics")
    print("  - Real-time cost tracking")
    print("  - Audit logging for compliance")
    print("  - Composing multiple hooks")
    
    scenarios = [
        ("Performance Monitoring", scenario_performance_monitoring),
        ("Cost Tracking", scenario_cost_tracking),
        ("Audit Logging", scenario_audit_logging),
        ("Composed Hooks (all together)", scenario_composed_hooks),
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
1. **Hooks are Middleware**: Cross-cutting concerns without modifying core logic
2. **Composable**: Combine multiple hooks for comprehensive monitoring
3. **Event-Driven**: React to specific events in the execution flow
4. **Production-Ready**: Patterns for metrics, costs, audit logs, errors

**Common hook patterns:**
- Performance monitoring (timing, resource usage)
- Cost tracking (token usage, API costs)
- Audit logging (compliance, debugging)
- Rate limiting (prevent abuse)
- Error handling (retry, fallback)
- Content filtering (safety, compliance)

**Implementation tips:**
- Keep hooks focused (single responsibility)
- Make hooks reusable across projects
- Use async/await properly
- Always return HookResult
- Test hooks independently
- Document expected events and data format
""")


if __name__ == "__main__":
    asyncio.run(main())
