"""Slack approval system with interactive buttons."""

import asyncio
import logging
import uuid
from typing import Literal

try:
    from slack_sdk.web.async_client import AsyncWebClient
except ImportError:
    AsyncWebClient = None  # type: ignore

logger = logging.getLogger(__name__)


class SlackApprovalSystem:
    """Approval system that uses Slack interactive buttons.
    
    When an agent requests approval, this posts a message with approve/deny
    buttons. The approval_callback must be wired to handle Slack interactivity
    payloads and call resolve() with the result.
    
    Usage:
        approval = SlackApprovalSystem(client, channel_id)
        # Wire up approval_callback to Slack interactivity endpoint
        app.action("approve_*")(approval.approval_callback)
        
        # In your session, agent calls:
        result = await approval.request_approval("Deploy to prod?", ["allow", "deny"], 300.0, "deny")
    """
    
    def __init__(
        self,
        client: "AsyncWebClient",
        channel_id: str,
        thread_ts: str | None = None,
    ):
        if AsyncWebClient is None:
            raise ImportError("slack-sdk required: pip install slack-sdk")
        self.client = client
        self.channel_id = channel_id
        self.thread_ts = thread_ts
        self.pending: dict[str, asyncio.Future[str]] = {}
        self._lock = asyncio.Lock()
    
    async def request_approval(
        self,
        prompt: str,
        options: list[str],
        timeout: float,
        default: Literal["allow", "deny"],
    ) -> str:
        """Request approval via Slack interactive message.
        
        Args:
            prompt: The approval prompt to display
            options: List of options (typically ["allow", "deny"])
            timeout: Timeout in seconds before using default
            default: Default option if timeout expires
            
        Returns:
            Selected option from the user or default on timeout
        """
        request_id = str(uuid.uuid4())
        
        # Build Block Kit message with buttons
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"🔐 *Approval Required*\n\n{prompt}",
                },
            },
            {
                "type": "actions",
                "block_id": f"approval_{request_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": opt.capitalize()},
                        "style": "primary" if opt == "allow" else "danger" if opt == "deny" else None,
                        "action_id": f"approve_{request_id}_{opt}",
                        "value": opt,
                    }
                    for opt in options
                ],
            },
        ]
        # Remove None style entries
        for block in blocks:
            if block.get("type") == "actions":
                for elem in block.get("elements", []):
                    if elem.get("style") is None:
                        elem.pop("style", None)
        
        # Post the approval message
        result = await self.client.chat_postMessage(
            channel=self.channel_id,
            thread_ts=self.thread_ts,
            text=f"Approval Required: {prompt}",  # Fallback text
            blocks=blocks,
        )
        message_ts = result.get("ts")
        
        # Create future for this request
        loop = asyncio.get_event_loop()
        future: asyncio.Future[str] = loop.create_future()
        
        async with self._lock:
            self.pending[request_id] = future
        
        try:
            # Wait for response with timeout
            selected = await asyncio.wait_for(future, timeout=timeout)
            
            # Update message to show selection
            await self._update_approval_message(message_ts, prompt, selected, timed_out=False)
            return selected
            
        except asyncio.TimeoutError:
            logger.info(f"Approval {request_id} timed out, using default: {default}")
            
            # Update message to show timeout
            await self._update_approval_message(message_ts, prompt, default, timed_out=True)
            return default
            
        finally:
            async with self._lock:
                self.pending.pop(request_id, None)
    
    async def _update_approval_message(
        self,
        message_ts: str,
        prompt: str,
        result: str,
        timed_out: bool,
    ):
        """Update the approval message to show the result."""
        status = "⏱️ Timed out" if timed_out else "✅ Approved" if result == "allow" else "❌ Denied"
        
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"🔐 *Approval Required*\n\n{prompt}\n\n*Result:* {status} ({result})",
                },
            },
        ]
        
        try:
            await self.client.chat_update(
                channel=self.channel_id,
                ts=message_ts,
                text=f"Approval: {result}",
                blocks=blocks,
            )
        except Exception as e:
            logger.warning(f"Failed to update approval message: {e}")
    
    def resolve(self, request_id: str, option: str):
        """Resolve a pending approval request.
        
        Call this from your Slack interactivity handler when a button is clicked.
        
        Args:
            request_id: The request ID from the action_id (approve_{request_id}_{option})
            option: The selected option
        """
        future = self.pending.get(request_id)
        if future and not future.done():
            future.set_result(option)
    
    async def approval_callback(self, ack, body, action):
        """Slack action callback for approval buttons.
        
        Wire this to your Bolt app:
            @app.action(re.compile(r"approve_.*"))
            async def handle_approval(ack, body, action):
                await approval_system.approval_callback(ack, body, action)
        """
        await ack()
        
        action_id = action.get("action_id", "")
        # Parse: approve_{request_id}_{option}
        parts = action_id.split("_", 2)
        if len(parts) >= 3:
            request_id = parts[1]
            option = parts[2]
            self.resolve(request_id, option)
