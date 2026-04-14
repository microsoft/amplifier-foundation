#!/usr/bin/env python3
"""
Example 20: Calendar Integration (Meeting Assistant)
====================================================

AUDIENCE: Everyone - PMs, executives, anyone who schedules meetings
VALUE: Shows practical business automation with calendar APIs
PATTERN: External API integration, natural language scheduling, email/calendar sync

What this demonstrates:
  - Integrating with external APIs (Google Calendar, Outlook)
  - Natural language to structured data (meeting requests)
  - Multi-step workflows (check availability, book, send invites)
  - Real-world business automation

When you'd use this:
  - Meeting scheduling assistants
  - Calendar management automation
  - Event planning workflows
  - Availability checking
  - Automated reminders and follow-ups
"""

import asyncio
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any

from amplifier_core import AmplifierSession
from amplifier_foundation import load_bundle

# ============================================================================
# Mock Calendar API (Simulates Google Calendar / Outlook)
# ============================================================================


class MockCalendar:
    """
    Mock calendar API for demonstration.

    In production, replace with actual Google Calendar API or Microsoft Graph API.
    """

    def __init__(self):
        self.events: list[dict[str, Any]] = [
            {
                "id": "evt1",
                "summary": "Team Standup",
                "start": datetime.now().replace(hour=9, minute=0),
                "end": datetime.now().replace(hour=9, minute=30),
                "attendees": ["alice@company.com", "bob@company.com"],
            },
            {
                "id": "evt2",
                "summary": "Product Review",
                "start": datetime.now().replace(hour=14, minute=0),
                "end": datetime.now().replace(hour=15, minute=0),
                "attendees": ["alice@company.com", "charlie@company.com"],
            },
        ]

    def get_events(self, start_date: datetime, end_date: datetime) -> list[dict[str, Any]]:
        """Get events in date range."""
        return [e for e in self.events if start_date <= e["start"] <= end_date]

    def find_free_slots(self, date: datetime, duration_minutes: int = 60) -> list[dict[str, Any]]:
        """Find available time slots."""
        # Simplified: just find gaps in schedule
        business_hours = range(9, 17)  # 9 AM to 5 PM
        day_events = [e for e in self.events if e["start"].date() == date.date()]

        free_slots = []
        for hour in business_hours:
            slot_start = date.replace(hour=hour, minute=0, second=0, microsecond=0)
            slot_end = slot_start + timedelta(minutes=duration_minutes)

            # Check if this slot conflicts with any event
            conflicts = any(not (slot_end <= e["start"] or slot_start >= e["end"]) for e in day_events)

            if not conflicts and slot_end.hour < 17:
                free_slots.append(
                    {
                        "start": slot_start,
                        "end": slot_end,
                    }
                )

        return free_slots[:5]  # Return top 5 slots

    def create_event(self, summary: str, start: datetime, end: datetime, attendees: list[str]) -> dict[str, Any]:
        """Create a calendar event."""
        event = {
            "id": f"evt{len(self.events) + 1}",
            "summary": summary,
            "start": start,
            "end": end,
            "attendees": attendees,
        }
        self.events.append(event)
        return event


# ============================================================================
# Calendar Assistant
# ============================================================================


class CalendarAssistant:
    """
    AI-powered calendar assistant.

    Features:
    - Parse natural language meeting requests
    - Check availability
    - Suggest optimal meeting times
    - Book meetings
    - Send meeting summaries
    """

    def __init__(self, calendar: MockCalendar, session: AmplifierSession):
        self.calendar = calendar
        self.session = session

    async def parse_meeting_request(self, request: str) -> dict[str, Any]:
        """Parse natural language meeting request into structured data."""
        prompt = f"""Parse this meeting request into structured data:

"{request}"

Extract:
1. Meeting title/purpose
2. Attendees (email addresses if mentioned, otherwise names)
3. Duration (in minutes)
4. Preferred date/time (or "flexible" if not specified)
5. Any special requirements

Return as JSON with keys: title, attendees, duration, preferred_time, requirements"""

        await self.session.execute(prompt)

        # In production, use structured output or parse JSON from response
        # For demo, we'll return a structured dict
        return {
            "title": "Strategy Planning Session",
            "attendees": ["alice@company.com", "bob@company.com"],
            "duration": 60,
            "preferred_time": "flexible",
            "requirements": "Need projector",
        }

    async def find_best_time(self, meeting_data: dict[str, Any]) -> dict[str, Any]:
        """Find the best available time for a meeting."""
        # Get free slots for next 3 days
        base_date = datetime.now()
        all_slots = []

        for day_offset in range(1, 4):
            date = base_date + timedelta(days=day_offset)
            slots = self.calendar.find_free_slots(date, meeting_data["duration"])
            all_slots.extend(slots)

        if not all_slots:
            return {"error": "No available slots found"}

        # Ask AI to pick the best slot
        slots_desc = "\n".join(
            [
                f"{i + 1}. {slot['start'].strftime('%A, %B %d at %I:%M %p')} - {slot['end'].strftime('%I:%M %p')}"
                for i, slot in enumerate(all_slots)
            ]
        )

        prompt = f"""Given these available time slots for a meeting:

{slots_desc}

Meeting details:
- Title: {meeting_data["title"]}
- Duration: {meeting_data["duration"]} minutes
- Attendees: {", ".join(meeting_data["attendees"])}

Which slot is best? Consider:
- Avoiding back-to-back meetings (prefer slots with buffers)
- Mid-morning or mid-afternoon often work well
- Avoiding Monday mornings and Friday afternoons

Respond with just the slot number (1-{len(all_slots)})."""

        response = await self.session.execute(prompt)

        # Parse response (simplified)
        try:
            slot_num = int(response.strip().split()[0]) - 1
            if 0 <= slot_num < len(all_slots):
                return all_slots[slot_num]
        except Exception:
            pass

        # Fallback to first slot
        return all_slots[0]

    async def create_meeting_invitation(self, meeting_data: dict[str, Any], time_slot: dict[str, Any]) -> str:
        """Generate a meeting invitation email."""
        prompt = f"""Write a professional meeting invitation email:

Meeting: {meeting_data["title"]}
Date/Time: {time_slot["start"].strftime("%A, %B %d, %Y at %I:%M %p")}
Duration: {meeting_data["duration"]} minutes
Attendees: {", ".join(meeting_data["attendees"])}
Requirements: {meeting_data.get("requirements", "None")}

Include:
- Clear subject line
- Meeting purpose
- Agenda items (infer from title)
- Calendar details
- Any prep needed
- Friendly, professional tone

Keep it concise."""

        invitation = await self.session.execute(prompt)
        return invitation


# ============================================================================
# Demo Scenarios
# ============================================================================


async def scenario_schedule_meeting():
    """
    Scenario: Schedule a meeting from natural language.

    Full flow: parse request → find time → book → send invite.
    """
    print("\n" + "=" * 80)
    print("SCENARIO 1: Schedule Meeting from Natural Language")
    print("=" * 80)

    # Initialize
    calendar = MockCalendar()
    foundation_path = Path(__file__).parent.parent
    foundation = await load_bundle(str(foundation_path))
    mount_plan = foundation.to_mount_plan()

    session = AmplifierSession(config=mount_plan)
    await session.initialize()

    assistant = CalendarAssistant(calendar, session)

    # Natural language request
    request = """Can you schedule a strategy planning session with Alice and Bob?
    We need about an hour, and sometime next week would be great.
    We'll need a room with a projector."""

    print("\n📝 Meeting Request:")
    print(f'   "{request}"')
    print("\n" + "-" * 80)

    # Step 1: Parse request
    print("\n[1/4] Parsing meeting request...")
    meeting_data = await assistant.parse_meeting_request(request)
    print(f"   ✓ Title: {meeting_data['title']}")
    print(f"   ✓ Attendees: {', '.join(meeting_data['attendees'])}")
    print(f"   ✓ Duration: {meeting_data['duration']} minutes")

    # Step 2: Find available time
    print("\n[2/4] Finding available time slots...")
    time_slot = await assistant.find_best_time(meeting_data)
    if "error" in time_slot:
        print(f"   ✗ {time_slot['error']}")
        return

    print(f"   ✓ Best time: {time_slot['start'].strftime('%A, %B %d at %I:%M %p')}")

    # Step 3: Book the meeting
    print("\n[3/4] Booking meeting...")
    event = calendar.create_event(
        summary=meeting_data["title"],
        start=time_slot["start"],
        end=time_slot["end"],
        attendees=meeting_data["attendees"],
    )
    print(f"   ✓ Meeting booked (ID: {event['id']})")

    # Step 4: Generate invitation
    print("\n[4/4] Generating meeting invitation...")
    invitation = await assistant.create_meeting_invitation(meeting_data, time_slot)

    print("\n" + "=" * 80)
    print("✅ MEETING SCHEDULED")
    print("=" * 80)
    print("\n📧 Invitation Email:")
    print("-" * 80)
    print(invitation)

    await session.cleanup()


async def scenario_check_availability():
    """
    Scenario: Check availability and suggest times.

    Shows calendar inspection and intelligent scheduling.
    """
    print("\n" + "=" * 80)
    print("SCENARIO 2: Check Availability")
    print("=" * 80)

    calendar = MockCalendar()
    foundation_path = Path(__file__).parent.parent
    foundation = await load_bundle(str(foundation_path))
    mount_plan = foundation.to_mount_plan()

    session = AmplifierSession(config=mount_plan)
    await session.initialize()

    # Show today's schedule
    print("\n📅 Today's Schedule:")
    print("-" * 80)
    today_start = datetime.now().replace(hour=0, minute=0, second=0)
    today_end = datetime.now().replace(hour=23, minute=59, second=59)

    events = calendar.get_events(today_start, today_end)
    for event in events:
        print(f"  {event['start'].strftime('%I:%M %p')} - {event['end'].strftime('%I:%M %p')}: {event['summary']}")

    # Find free slots
    print("\n🔍 Available Time Slots (60 min meetings):")
    print("-" * 80)

    free_slots = calendar.find_free_slots(datetime.now(), duration_minutes=60)
    for i, slot in enumerate(free_slots, 1):
        print(f"  {i}. {slot['start'].strftime('%I:%M %p')} - {slot['end'].strftime('%I:%M %p')}")

    # Ask AI for recommendation
    slots_desc = "\n".join([f"{i}. {slot['start'].strftime('%I:%M %p')}" for i, slot in enumerate(free_slots, 1)])

    prompt = f"""Looking at my schedule today:

Busy times:
{chr(10).join([f"- {e['start'].strftime('%I:%M %p')}: {e['summary']}" for e in events])}

Available slots:
{slots_desc}

Someone wants to schedule a 1-hour meeting. Which slot would you recommend and why?
Consider energy levels, lunch breaks, and buffer time."""

    print("\n💭 AI Recommendation:")
    print("-" * 80)

    recommendation = await session.execute(prompt)
    print(recommendation)

    await session.cleanup()


async def scenario_meeting_summary():
    """
    Scenario: Generate meeting summary and action items.

    Post-meeting workflow automation.
    """
    print("\n" + "=" * 80)
    print("SCENARIO 3: Meeting Summary & Follow-up")
    print("=" * 80)

    # Simulated meeting notes
    meeting_notes = """
Meeting: Product Roadmap Q1 2024
Date: Today, 2:00 PM
Attendees: Alice (PM), Bob (Eng), Charlie (Design)

Discussion:
- Reviewed user feedback from beta launch
- Prioritized features for Q1: mobile app, API v2, dashboard redesign
- Bob raised concerns about API timeline - might need extra sprint
- Charlie showed mockups for new dashboard
- Alice will draft PRD for mobile app by Friday

Action Items:
- Alice: Draft mobile app PRD (Due: Friday)
- Bob: Technical spike for API v2 (Due: Next Monday)
- Charlie: Finalize dashboard mockups (Due: Next Wednesday)
- All: Review and comment on Jira tickets by EOW

Next Meeting: January 15, 2024 (2 weeks)
"""

    print("\n📝 Raw Meeting Notes:")
    print("-" * 80)
    print(meeting_notes[:300] + "...")

    foundation_path = Path(__file__).parent.parent
    foundation = await load_bundle(str(foundation_path))
    mount_plan = foundation.to_mount_plan()

    session = AmplifierSession(config=mount_plan)
    await session.initialize()

    # Generate structured summary
    prompt = f"""Create a polished meeting summary from these notes:

{meeting_notes}

Generate:
1. Executive Summary (2-3 sentences)
2. Key Decisions Made
3. Action Items (with owners and due dates)
4. Next Steps
5. Follow-up email to attendees

Format professionally and concisely."""

    print("\n⏳ Generating summary...")

    summary = await session.execute(prompt)

    print("\n" + "=" * 80)
    print("✅ MEETING SUMMARY")
    print("=" * 80)
    print(summary)

    await session.cleanup()


# ============================================================================
# Interactive Menu
# ============================================================================


async def main():
    """Run interactive demo menu."""
    print("\n" + "=" * 80)
    print("📆 Calendar Integration (Meeting Assistant)")
    print("=" * 80)
    print("\nVALUE: Automate meeting scheduling and calendar management")
    print("AUDIENCE: Everyone - PMs, executives, busy professionals")
    print("\nWhat this demonstrates:")
    print("  - Natural language to structured meeting data")
    print("  - Intelligent time slot selection")
    print("  - Automated meeting invitations")
    print("  - Meeting summaries and follow-ups")

    scenarios = [
        ("Schedule Meeting (full flow)", scenario_schedule_meeting),
        ("Check Availability", scenario_check_availability),
        ("Meeting Summary & Follow-up", scenario_meeting_summary),
    ]

    print("\n" + "=" * 80)
    print("Choose a scenario:")
    print("=" * 80)
    for i, (name, _) in enumerate(scenarios, 1):
        print(f"  {i}. {name}")
    print("  q. Quit")
    print("-" * 80)

    choice = input("\nYour choice: ").strip().lower()

    if choice == "q":
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

    print("\n" + "=" * 80)
    print("💡 KEY TAKEAWAYS")
    print("=" * 80)
    print("""
1. **Natural Language → Action**: Users describe what they want, AI handles details
2. **Multi-Step Workflows**: Parse → Find time → Book → Notify
3. **Real-World Integration**: Works with actual calendar APIs (Google, Outlook)
4. **Business Value**: Saves time, reduces scheduling friction

**Implementation with real calendars:**

Google Calendar:
```python
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

service = build('calendar', 'v3', credentials=creds)
events = service.events().list(calendarId='primary').execute()
```

Microsoft Outlook:
```python
import requests
headers = {'Authorization': f'Bearer {access_token}'}
response = requests.get(
    'https://graph.microsoft.com/v1.0/me/calendar/events',
    headers=headers
)
```

**Production considerations:**
- OAuth authentication flow
- Handle timezones properly
- Respect user permissions
- Rate limiting and retries
- Privacy and data protection
- Integration with email for invites

**Use cases:**
- Executive assistants (automate scheduling)
- Team meeting coordination
- Interview scheduling
- Event planning
- Calendar optimization (suggest better meeting times)
- Meeting prep and follow-up automation
""")


if __name__ == "__main__":
    asyncio.run(main())
