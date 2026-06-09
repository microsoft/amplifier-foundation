"""Slack Bolt app factory with Amplifier session management."""

import asyncio
import logging
import re
from typing import Any, Callable, Awaitable

try:
    from slack_bolt.async_app import AsyncApp
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
    from slack_sdk.web.async_client import AsyncWebClient
except ImportError:
    AsyncApp = None  # type: ignore
    AsyncSocketModeHandler = None  # type: ignore
    AsyncWebClient = None  # type: ignore

from .protocol import SlackConfig
from .approval import SlackApprovalSystem
from .display import SlackDisplaySystem

logger = logging.getLogger(__name__)


class SlackApp:
    """Slack Bolt app with Amplifier session management.
    
    Handles:
    - Socket Mode connection
    - app_mention events (when @bot is mentioned in channels)
    - message events (DMs)
    - Thread awareness (replies stay in thread)
    - Session management keyed by thread_ts
    
    Usage:
        async def session_factory(channel_id, thread_ts, approval, display):
            return await prepared.create_session(
                session_id=thread_ts or channel_id,
                approval_system=approval,
                display_system=display,
            )
        
        config = SlackConfig(bot_token="xoxb-...", app_token="xapp-...")
        slack_app = SlackApp(config, session_factory)
        await slack_app.start()
    """
    
    def __init__(
        self,
        config: SlackConfig,
        session_factory: Callable[
            [str, str | None, SlackApprovalSystem, SlackDisplaySystem],
            Awaitable[Any]
        ],
    ):
        if AsyncApp is None:
            raise ImportError(
                "slack-bolt required: pip install slack-bolt slack-sdk"
            )
        
        self.config = config
        self.session_factory = session_factory
        
        # Session storage keyed by conversation ID (thread_ts or channel_id for DMs)
        self._sessions: dict[str, Any] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._approval_systems: dict[str, SlackApprovalSystem] = {}
        
        # Create Bolt app
        self.app = AsyncApp(
            token=config.bot_token,
            signing_secret=config.signing_secret,
        )
        
        # Register event handlers
        self._register_handlers()
    
    def _register_handlers(self):
        """Register Slack event handlers."""
        
        # Handle @mentions in channels
        @self.app.event("app_mention")
        async def handle_mention(event, say, client):
            await self._handle_message(event, say, client, is_dm=False)
        
        # Handle direct messages
        @self.app.event("message")
        async def handle_message(event, say, client):
            # Only handle DMs (no channel/subtype means DM)
            channel_type = event.get("channel_type", "")
            subtype = event.get("subtype")
            
            # Skip bot messages and other subtypes
            if subtype is not None:
                return
            
            # Handle DMs (im = instant message)
            if channel_type == "im":
                await self._handle_message(event, say, client, is_dm=True)
        
        # Handle approval button clicks
        @self.app.action(re.compile(r"approve_.*"))
        async def handle_approval(ack, body, action):
            await ack()
            
            # Find the approval system for this channel/thread
            channel = body.get("channel", {}).get("id", "")
            thread_ts = body.get("message", {}).get("thread_ts")
            conv_id = self._get_conversation_id(channel, thread_ts)
            
            approval = self._approval_systems.get(conv_id)
            if approval:
                await approval.approval_callback(ack, body, action)
    
    async def _handle_message(
        self,
        event: dict[str, Any],
        say: Callable,
        client: AsyncWebClient,
        is_dm: bool,
    ):
        """Handle an incoming message (mention or DM)."""
        channel_id = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts")
        user = event.get("user", "")
        text = event.get("text", "")
        
        # For mentions, strip the bot mention from the text
        if not is_dm:
            text = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()
        
        if not text:
            return
        
        logger.info(f"Received message from {user} in {channel_id}: {text[:50]}...")
        
        # Get or create session for this conversation
        conv_id = self._get_conversation_id(channel_id, thread_ts)
        
        try:
            session = await self._get_or_create_session(
                conv_id, channel_id, thread_ts, client
            )
            
            # Execute the message through Amplifier
            async with self._session_locks[conv_id]:
                response = await session.execute(text)
            
            # Response is posted via DisplaySystem during execution
            # But also send a final message if needed
            if response and not response.startswith("["):  # Skip if looks like metadata
                await self._post_response(client, channel_id, thread_ts, response)
                
        except Exception as e:
            logger.exception(f"Error handling message: {e}")
            await say(
                text=f"Sorry, I encountered an error: {str(e)[:100]}",
                thread_ts=thread_ts,
            )
    
    def _get_conversation_id(self, channel_id: str, thread_ts: str | None) -> str:
        """Get unique conversation ID for session lookup.
        
        Uses thread_ts if in a thread, otherwise channel_id.
        """
        return f"{channel_id}:{thread_ts}" if thread_ts else channel_id
    
    async def _get_or_create_session(
        self,
        conv_id: str,
        channel_id: str,
        thread_ts: str | None,
        client: AsyncWebClient,
    ) -> Any:
        """Get or create an Amplifier session for a conversation."""
        if conv_id not in self._sessions:
            # Create approval and display systems
            approval = SlackApprovalSystem(client, channel_id, thread_ts)
            display = SlackDisplaySystem(
                client, channel_id, thread_ts, self.config.message_char_limit
            )
            
            # Store approval system for button callbacks
            self._approval_systems[conv_id] = approval
            
            # Create session via factory
            session = await self.session_factory(
                channel_id, thread_ts, approval, display
            )
            
            self._sessions[conv_id] = session
            self._session_locks[conv_id] = asyncio.Lock()
        
        return self._sessions[conv_id]
    
    async def _post_response(
        self,
        client: AsyncWebClient,
        channel_id: str,
        thread_ts: str | None,
        response: str,
    ):
        """Post a response message to Slack."""
        from .formatting import markdown_to_blocks, split_long_message
        
        chunks = split_long_message(response, self.config.message_char_limit)
        
        for chunk in chunks:
            blocks = markdown_to_blocks(chunk)
            await client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=chunk[:100] + "..." if len(chunk) > 100 else chunk,
                blocks=blocks,
            )
    
    async def start(self):
        """Start the Slack app with Socket Mode."""
        if not self.config.socket_mode:
            raise ValueError("HTTP mode not yet implemented - use socket_mode=True")
        
        handler = AsyncSocketModeHandler(self.app, self.config.app_token)
        logger.info("Starting Slack bot with Socket Mode...")
        await handler.start_async()
    
    async def stop(self):
        """Stop the Slack app."""
        # Clean up sessions
        self._sessions.clear()
        self._session_locks.clear()
        self._approval_systems.clear()


async def create_slack_app(
    config: SlackConfig,
    session_factory: Callable[
        [str, str | None, SlackApprovalSystem, SlackDisplaySystem],
        Awaitable[Any]
    ],
) -> SlackApp:
    """Create and configure a SlackApp instance.
    
    Convenience function for creating a SlackApp with the standard configuration.
    
    Args:
        config: Slack configuration with tokens
        session_factory: Async function that creates Amplifier sessions
        
    Returns:
        Configured SlackApp ready to start
        
    Example:
        from amplifier_foundation import load_bundle, compose, prepare
        from amplifier_foundation.slack_adapter import create_slack_app, SlackConfig
        
        async def main():
            # Set up Amplifier
            bundle = await load_bundle("path/to/bundle.yaml")
            composed = await compose(bundle)
            prepared = await prepare(composed)
            
            # Session factory
            async def factory(channel_id, thread_ts, approval, display):
                return await prepared.create_session(
                    session_id=thread_ts or channel_id,
                    approval_system=approval,
                    display_system=display,
                )
            
            # Create and start Slack app
            config = SlackConfig(
                bot_token=os.environ["SLACK_BOT_TOKEN"],
                app_token=os.environ["SLACK_APP_TOKEN"],
            )
            app = await create_slack_app(config, factory)
            await app.start()
    """
    return SlackApp(config, session_factory)
