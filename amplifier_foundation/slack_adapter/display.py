"""Slack display system for posting content to channels."""

import logging
from typing import Any

try:
    from slack_sdk.web.async_client import AsyncWebClient
except ImportError:
    AsyncWebClient = None  # type: ignore

from .formatting import markdown_to_blocks, split_long_message

logger = logging.getLogger(__name__)


class SlackDisplaySystem:
    """Display system that posts content to Slack.
    
    Handles:
    - Markdown to Block Kit conversion
    - Long message splitting (40k char limit)
    - Thread awareness
    
    Usage:
        display = SlackDisplaySystem(client, channel_id, thread_ts)
        await display.display("# Hello\n\nThis is *bold* and `code`")
    """
    
    def __init__(
        self,
        client: "AsyncWebClient",
        channel_id: str,
        thread_ts: str | None = None,
        message_char_limit: int = 39000,
    ):
        if AsyncWebClient is None:
            raise ImportError("slack-sdk required: pip install slack-sdk")
        self.client = client
        self.channel_id = channel_id
        self.thread_ts = thread_ts
        self.message_char_limit = message_char_limit
    
    async def display(self, content: str, metadata: dict[str, Any] | None = None):
        """Display content in Slack.
        
        Args:
            content: Markdown content to display
            metadata: Optional metadata (e.g., {"type": "thinking"} for different styling)
        """
        metadata = metadata or {}
        content_type = metadata.get("type", "response")
        
        # Split long content
        chunks = split_long_message(content, self.message_char_limit)
        
        for i, chunk in enumerate(chunks):
            # Convert to Block Kit
            blocks = markdown_to_blocks(chunk, content_type=content_type)
            
            # Add continuation indicator for multi-part messages
            if len(chunks) > 1:
                part_indicator = f"(Part {i + 1}/{len(chunks)})"
                if blocks and blocks[-1].get("type") == "section":
                    # Append to last section
                    text = blocks[-1].get("text", {})
                    if text.get("type") == "mrkdwn":
                        text["text"] = f"{text['text']}\n\n_{part_indicator}_"
                else:
                    # Add new section for indicator
                    blocks.append({
                        "type": "context",
                        "elements": [{"type": "mrkdwn", "text": part_indicator}],
                    })
            
            try:
                await self.client.chat_postMessage(
                    channel=self.channel_id,
                    thread_ts=self.thread_ts,
                    text=chunk[:100] + "..." if len(chunk) > 100 else chunk,  # Fallback
                    blocks=blocks,
                )
            except Exception as e:
                logger.error(f"Failed to post message: {e}")
                # Fall back to plain text
                await self.client.chat_postMessage(
                    channel=self.channel_id,
                    thread_ts=self.thread_ts,
                    text=chunk,
                )
    
    async def update_status(self, status: str):
        """Post a status update (e.g., "Thinking...", "Processing...")."""
        blocks = [
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"_{status}_"}],
            },
        ]
        
        await self.client.chat_postMessage(
            channel=self.channel_id,
            thread_ts=self.thread_ts,
            text=status,
            blocks=blocks,
        )
