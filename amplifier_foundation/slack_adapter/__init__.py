"""Slack adapter for Amplifier - enables Slack bot integration with Amplifier sessions.

This adapter provides:
- SlackApprovalSystem: Interactive approval buttons in Slack
- SlackDisplaySystem: Content posting to Slack channels
- SlackApp: Bolt app factory with session management
- markdown_to_blocks: Markdown to Slack Block Kit conversion

Usage:
    from amplifier_foundation.slack_adapter import SlackApp, SlackApprovalSystem, SlackDisplaySystem
"""

from .protocol import SlackConfig
from .approval import SlackApprovalSystem
from .display import SlackDisplaySystem
from .formatting import markdown_to_blocks, split_long_message
from .app import SlackApp, create_slack_app

__all__ = [
    "SlackConfig",
    "SlackApprovalSystem",
    "SlackDisplaySystem",
    "SlackApp",
    "create_slack_app",
    "markdown_to_blocks",
    "split_long_message",
]
