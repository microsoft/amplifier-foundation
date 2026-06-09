"""Protocol definitions and configuration for Slack adapter."""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class SlackConfig:
    """Configuration for Slack adapter.
    
    Attributes:
        bot_token: Slack Bot User OAuth Token (xoxb-...)
        app_token: Slack App-Level Token for Socket Mode (xapp-...)
        signing_secret: Optional signing secret for HTTP mode verification
        socket_mode: Whether to use Socket Mode (default True, recommended)
        message_char_limit: Max characters per message (Slack limit is 40000)
    """
    bot_token: str
    app_token: str | None = None
    signing_secret: str | None = None
    socket_mode: bool = True
    message_char_limit: int = 39000  # Leave margin below 40k limit
    
    def __post_init__(self):
        if self.socket_mode and not self.app_token:
            raise ValueError("app_token required for Socket Mode")
        if not self.socket_mode and not self.signing_secret:
            raise ValueError("signing_secret required for HTTP mode")


@runtime_checkable
class SessionFactoryProtocol(Protocol):
    """Protocol for session factory - creates Amplifier sessions for conversations."""
    
    async def get_or_create_session(
        self,
        conversation_id: str,
        channel_id: str,
        thread_ts: str | None = None,
    ) -> "AmplifierSessionProtocol":
        """Get existing session or create a new one for a conversation."""
        ...


@runtime_checkable
class AmplifierSessionProtocol(Protocol):
    """Protocol for Amplifier session - executes prompts and returns responses."""
    
    async def execute(self, prompt: str) -> str:
        """Execute a prompt and return the response."""
        ...
