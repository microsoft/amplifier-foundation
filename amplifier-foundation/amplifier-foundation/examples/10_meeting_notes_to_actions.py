#!/usr/bin/env python3
"""
Example 10: Meeting Notes → Action Items
========================================

VALUE PROPOSITION:
Transform unstructured meeting notes into organized, actionable task lists.
A universal productivity problem solved by AI - no more manual note parsing.

WHAT YOU'LL LEARN:
- Text processing and structured extraction
- Converting unstructured data to structured formats
- Practical productivity automation
- JSON and Markdown output formatting

AUDIENCE:
Everyone - PMs, designers, developers, anyone who attends meetings
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from amplifier_foundation import load_bundle

# =============================================================================
# SECTION 1: Sample Meeting Notes
# =============================================================================

SAMPLE_NOTES = """
Product Planning Meeting - Q1 2024 Launch
Date: January 15, 2024
Attendees: Sarah (PM), John (Eng), Maria (Design), Alex (Marketing)

Discussion Points:
- Reviewed the roadmap for Q1 launch
- Need to finalize the landing page design by end of week
- API documentation is incomplete and needs urgent attention
- Marketing campaign should start 2 weeks before launch

Action Items Discussed:
1. John will update the landing page by Friday
2. Sarah needs to review the API docs ASAP - this is blocking the beta release
3. Maria to create social media assets by next Monday
4. We should schedule a follow-up next week to discuss metrics
5. Alex mentioned he'll coordinate with the PR team about the press release

Notes:
- Budget approved for additional contractor if needed
- Launch date tentatively set for Feb 15
- Need to prioritize the API docs review - Sarah emphasized this is critical
"""


# =============================================================================
# SECTION 2: Action Item Extraction
# =============================================================================


async def extract_action_items(meeting_notes: str) -> dict[str, Any]:
    """Extract structured action items from meeting notes.

    Args:
        meeting_notes: Raw meeting notes text

    Returns:
        Dict with extracted action items and metadata
    """

    # Load foundation and provider
    foundation_path = Path(__file__).parent.parent
    foundation = await load_bundle(str(foundation_path))
    provider = await load_bundle(str(foundation_path / "providers" / "anthropic-sonnet.yaml"))

    composed = foundation.compose(provider)

    print("⏳ Analyzing meeting notes...")
    prepared = await composed.prepare()
    session = await prepared.create_session()

    # Craft prompt for structured extraction
    prompt = f"""Analyze these meeting notes and extract all action items.

For each action item, identify:
- task: A clear, concise description of what needs to be done
- owner: The person responsible (if mentioned, otherwise "Unassigned")
- deadline: When it's due (if mentioned, otherwise "No deadline")
- priority: high/medium/low (infer from language like "ASAP", "urgent", "critical")

Meeting Notes:
{meeting_notes}

Return ONLY valid JSON in this exact format:
{{
  "action_items": [
    {{
      "task": "description",
      "owner": "name",
      "deadline": "date or relative time",
      "priority": "high|medium|low"
    }}
  ],
  "meeting_info": {{
    "title": "meeting title if mentioned",
    "date": "date if mentioned"
  }}
}}"""

    async with session:
        response = await session.execute(prompt)

        # Parse JSON from response
        try:
            # Try to extract JSON from markdown code blocks or plain text
            response_text = response.strip()

            # Find JSON in response - look for the first { and last }
            start_idx = response_text.find("{")
            end_idx = response_text.rfind("}")

            if start_idx != -1 and end_idx != -1:
                response_text = response_text[start_idx : end_idx + 1]

            data = json.loads(response_text)
            return data

        except json.JSONDecodeError as e:
            print(f"⚠️  Warning: Could not parse JSON response: {e}")
            print(f"Raw response: {response[:200]}...")

            # Return a fallback structure
            return {
                "action_items": [],
                "meeting_info": {},
                "error": "Failed to parse structured output",
                "raw_response": response,
            }


def format_as_markdown(data: dict[str, Any]) -> str:
    """Format action items as Markdown checklist.

    Args:
        data: Structured action items data

    Returns:
        Formatted markdown string
    """
    output = []

    # Meeting info
    meeting_info = data.get("meeting_info", {})
    if meeting_info.get("title"):
        output.append(f"# {meeting_info['title']}")
    if meeting_info.get("date"):
        output.append(f"**Date:** {meeting_info['date']}")

    output.append("\n## Action Items\n")

    # Action items
    action_items = data.get("action_items", [])

    if not action_items:
        output.append("*No action items found*")
        return "\n".join(output)

    # Group by priority
    high_priority = [item for item in action_items if item.get("priority") == "high"]
    medium_priority = [item for item in action_items if item.get("priority") == "medium"]
    low_priority = [item for item in action_items if item.get("priority") == "low"]

    # High priority items
    if high_priority:
        output.append("### 🔴 High Priority\n")
        for item in high_priority:
            owner = item.get("owner", "Unassigned")
            deadline = item.get("deadline", "No deadline")
            task = item.get("task", "")
            output.append(f"- [ ] {task}")
            output.append(f"  - **Owner:** {owner}")
            output.append(f"  - **Due:** {deadline}\n")

    # Medium priority items
    if medium_priority:
        output.append("### 🟡 Medium Priority\n")
        for item in medium_priority:
            owner = item.get("owner", "Unassigned")
            deadline = item.get("deadline", "No deadline")
            task = item.get("task", "")
            output.append(f"- [ ] {task}")
            output.append(f"  - **Owner:** {owner}")
            output.append(f"  - **Due:** {deadline}\n")

    # Low priority items
    if low_priority:
        output.append("### 🟢 Low Priority\n")
        for item in low_priority:
            owner = item.get("owner", "Unassigned")
            deadline = item.get("deadline", "No deadline")
            task = item.get("task", "")
            output.append(f"- [ ] {task}")
            output.append(f"  - **Owner:** {owner}")
            output.append(f"  - **Due:** {deadline}\n")

    return "\n".join(output)


# =============================================================================
# SECTION 3: Interactive Mode
# =============================================================================


async def interactive_mode():
    """Interactive mode - paste meeting notes."""
    print("\n" + "=" * 60)
    print("Interactive Mode: Paste Your Meeting Notes")
    print("=" * 60)
    print("\nPaste your meeting notes (press Ctrl+D when done):")
    print("-" * 60)

    lines = []
    try:
        while True:
            line = input()
            lines.append(line)
    except EOFError:
        pass

    if not lines:
        print("\n⚠️  No input provided")
        return

    notes = "\n".join(lines)

    # Extract action items
    data = await extract_action_items(notes)

    # Display results
    print("\n" + "=" * 60)
    print("📋 Extracted Action Items")
    print("=" * 60)

    markdown = format_as_markdown(data)
    print("\n" + markdown)

    # Also show JSON
    print("\n" + "=" * 60)
    print("📄 JSON Format (for integrations)")
    print("=" * 60)
    print(json.dumps(data, indent=2))


# =============================================================================
# SECTION 4: Main Entry Point
# =============================================================================


async def main():
    """Main entry point with demo mode."""

    print("🚀 Meeting Notes → Action Items")
    print("=" * 60)
    print("\nVALUE: Automatically extract action items from meeting notes")
    print("AUDIENCE: Everyone - PMs, designers, developers")
    print("\nWhat this demonstrates:")
    print("  - Text processing and structured extraction")
    print("  - Unstructured → structured data transformation")
    print("  - Practical productivity automation")

    # Check prerequisites
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("\n❌ ERROR: Set ANTHROPIC_API_KEY environment variable")
        print("\nExample:")
        print("  export ANTHROPIC_API_KEY='your-key-here'")
        print("  python 10_meeting_notes_to_actions.py")
        return

    # Check if interactive mode requested
    if "--interactive" in sys.argv:
        await interactive_mode()
        return

    # Demo mode with sample notes
    print("\n" + "=" * 60)
    print("Demo Mode: Using Sample Meeting Notes")
    print("=" * 60)
    print("\nSample meeting notes:")
    print("-" * 60)
    print(SAMPLE_NOTES[:300] + "...")
    print("-" * 60)

    # Extract action items
    data = await extract_action_items(SAMPLE_NOTES)

    # Display results
    print("\n" + "=" * 60)
    print("📋 Extracted Action Items")
    print("=" * 60)

    markdown = format_as_markdown(data)
    print("\n" + markdown)

    # Show JSON structure
    print("\n" + "=" * 60)
    print("📄 Structured Data (JSON)")
    print("=" * 60)
    print(json.dumps(data, indent=2))

    # Summary
    print("\n" + "=" * 60)
    print("📚 WHAT YOU LEARNED:")
    print("=" * 60)
    print("  ✓ Extract structured data from unstructured text")
    print("  ✓ Parse and identify owners, deadlines, priorities")
    print("  ✓ Format output for different use cases (Markdown, JSON)")
    print("  ✓ Build practical productivity automation")

    print("\n💡 TIP: Run with --interactive to process your own meeting notes:")
    print("  python 10_meeting_notes_to_actions.py --interactive")


if __name__ == "__main__":
    asyncio.run(main())
