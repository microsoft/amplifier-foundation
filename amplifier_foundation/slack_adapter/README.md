# Slack Adapter for Amplifier Foundation

## Overview

The Slack adapter enables Amplifier agents to interact with users via Slack. It connects your Amplifier bundle to Slack using **Socket Mode**, providing:

- **Real-time messaging** via Slack's WebSocket API (no public endpoints needed)
- **Thread-aware conversations** with per-conversation session management
- **Interactive approvals** with approve/deny buttons for agent actions
- **Rich formatting** with automatic Markdown to Slack Block Kit conversion
- **Direct message and @mention support** in channels

## Quick Start

1. Create a Slack app and get tokens (see [Slack App Setup](#slack-app-setup))
2. Install dependencies:
   ```bash
   pip install amplifier-foundation[slack-adapter]
   ```
3. Set environment variables:
   ```bash
   export SLACK_BOT_TOKEN="xoxb-your-bot-token"
   export SLACK_APP_TOKEN="xapp-your-app-token"
   ```
4. Run the adapter:
   ```bash
   python -m amplifier_foundation.slack_adapter --bundle path/to/bundle.yaml
   ```

## Slack App Setup

### 1. Create a New Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App** → **From scratch**
3. Name your app and select your workspace

### 2. Enable Socket Mode

1. Navigate to **Socket Mode** in the left sidebar
2. Toggle **Enable Socket Mode** to ON
3. Create an **App-Level Token** with the `connections:write` scope
4. Save the token (starts with `xapp-`) — this is your `SLACK_APP_TOKEN`

### 3. Configure Bot Token Scopes

Navigate to **OAuth & Permissions** → **Scopes** → **Bot Token Scopes** and add:

| Scope | Purpose |
|-------|---------|
| `app_mentions:read` | Receive @mention events in channels |
| `channels:history` | Read messages in public channels |
| `channels:join` | Join public channels when invited |
| `chat:write` | Send messages and responses |
| `groups:history` | Read messages in private channels |
| `im:history` | Read direct message history |
| `im:read` | View basic DM info |
| `im:write` | Send direct messages |
| `users:read` | Access user profile information |

### 4. Enable Event Subscriptions

Navigate to **Event Subscriptions**:

1. Toggle **Enable Events** to ON
2. Under **Subscribe to bot events**, add:
   - `app_mention` — When someone @mentions the bot
   - `message.im` — Direct messages to the bot

### 5. Install to Workspace

1. Navigate to **Install App**
2. Click **Install to Workspace**
3. Authorize the requested permissions
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`) — this is your `SLACK_BOT_TOKEN`

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SLACK_BOT_TOKEN` | Yes | Bot User OAuth Token (`xoxb-...`). Found in **OAuth & Permissions** after installing the app. Used to authenticate API calls. |
| `SLACK_APP_TOKEN` | Yes | App-Level Token for Socket Mode (`xapp-...`). Generated in **Basic Information** → **App-Level Tokens**. Required for WebSocket connection. |

## Usage

### CLI

```bash
# Basic usage
python -m amplifier_foundation.slack_adapter --bundle path/to/bundle.yaml

# With explicit tokens
python -m amplifier_foundation.slack_adapter \
  --bundle path/to/bundle.yaml \
  --bot-token xoxb-your-token \
  --app-token xapp-your-token

# With debug logging
python -m amplifier_foundation.slack_adapter \
  --bundle path/to/bundle.yaml \
  --log-level DEBUG
```

### Programmatic Usage

```python
import asyncio
import os
from amplifier_foundation import load_bundle, compose, prepare
from amplifier_foundation.slack_adapter import SlackApp, SlackConfig

async def main():
    # Load and prepare your Amplifier bundle
    bundle = await load_bundle("path/to/bundle.yaml")
    composed = await compose(bundle)
    prepared = await prepare(composed)
    
    # Define session factory
    async def session_factory(channel_id, thread_ts, approval, display):
        session_id = thread_ts or channel_id
        return await prepared.create_session(
            session_id=session_id,
            approval_system=approval,
            display_system=display,
        )
    
    # Configure and start Slack app
    config = SlackConfig(
        bot_token=os.environ["SLACK_BOT_TOKEN"],
        app_token=os.environ["SLACK_APP_TOKEN"],
    )
    
    slack_app = SlackApp(config, session_factory)
    await slack_app.start()

asyncio.run(main())
```

## Configuration

### SlackConfig Options

```python
@dataclass
class SlackConfig:
    bot_token: str              # Required: Bot User OAuth Token (xoxb-...)
    app_token: str | None       # Required for Socket Mode: App-Level Token (xapp-...)
    signing_secret: str | None  # Required for HTTP mode (not yet implemented)
    socket_mode: bool = True    # Use Socket Mode (recommended, default)
    message_char_limit: int = 39000  # Max chars per message (Slack limit: 40000)
```

### Configuration Examples

```python
# Minimal Socket Mode config
config = SlackConfig(
    bot_token="xoxb-...",
    app_token="xapp-...",
)

# With custom message limit
config = SlackConfig(
    bot_token="xoxb-...",
    app_token="xapp-...",
    message_char_limit=20000,  # Split earlier for better readability
)
```

## Architecture

### Session Management

Sessions are keyed by conversation context:
- **Threads**: `{channel_id}:{thread_ts}` — Each thread gets its own session
- **DMs without threads**: `{channel_id}` — Each DM channel gets one session

```
User @mentions bot in #general
    └── Creates session: "C123456:1234567890.123456"
        └── All thread replies share this session

User DMs bot directly
    └── Creates session: "D789012"
        └── All messages in DM share this session
```

### Component Overview

```
┌─────────────────────────────────────────────────────────┐
│                      SlackApp                           │
│  - Socket Mode connection to Slack                      │
│  - Event routing (app_mention, message, actions)        │
│  - Session lifecycle management                         │
└─────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  Session Store  │  │ ApprovalSystem  │  │  DisplaySystem  │
│ (per-convo)     │  │ (per-convo)     │  │ (per-convo)     │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

### Approval System

When an agent requests approval, `SlackApprovalSystem`:

1. Posts an interactive message with approve/deny buttons
2. Waits for user response (with configurable timeout)
3. Returns the selected option (or default on timeout)
4. Updates the message to show the result

```
┌─────────────────────────────────────────┐
│ 🔐 Approval Required                    │
│                                         │
│ Deploy to production environment?       │
│                                         │
│ [Approve]  [Deny]                       │
└─────────────────────────────────────────┘
```

### Display System

`SlackDisplaySystem` handles content output:

- **Markdown conversion**: Converts agent responses to Slack Block Kit
- **Long message handling**: Splits messages exceeding 40k chars
- **Thread awareness**: All responses go to the correct thread
- **Content types**: Supports different styling for responses, thinking, errors

### Formatting Support

The adapter converts common Markdown to Slack's mrkdwn format:

| Markdown | Slack Output |
|----------|--------------|
| `# Header` | **Header** (bold section) |
| `**bold**` | *bold* |
| `*italic*` | _italic_ |
| `` `code` `` | `code` |
| ```` ```code block``` ```` | Code block |
| `[link](url)` | `<url\|link>` |
| `- list item` | • list item |

## Troubleshooting

### Connection Issues

**Error: "SLACK_APP_TOKEN required for Socket Mode"**
- Ensure you've created an App-Level Token in Slack
- Verify the token starts with `xapp-`
- Check it has the `connections:write` scope

**Error: "invalid_auth" or "not_authed"**
- Verify your `SLACK_BOT_TOKEN` starts with `xoxb-`
- Ensure the app is installed to your workspace
- Check tokens haven't been revoked

**Bot doesn't respond to messages**
- Verify Event Subscriptions are enabled
- Check `app_mention` and `message.im` events are subscribed
- Ensure Socket Mode is enabled
- Check the bot is invited to the channel (for non-DM messages)

### Permission Issues

**Error: "channel_not_found"**
- The bot needs to be invited to private channels
- Use `/invite @YourBotName` in the channel

**Error: "not_in_channel"**
- Invite the bot: `/invite @YourBotName`
- Or enable `channels:join` scope for auto-joining public channels

### Message Issues

**Messages are truncated**
- Slack has a 40,000 character limit per message
- The adapter automatically splits long messages
- Adjust `message_char_limit` if needed

**Formatting looks wrong**
- Slack uses mrkdwn, not standard Markdown
- Complex tables and nested formatting may not render perfectly
- Code blocks are preserved but syntax highlighting is limited

### Debug Mode

Enable debug logging to see detailed request/response info:

```bash
python -m amplifier_foundation.slack_adapter \
  --bundle path/to/bundle.yaml \
  --log-level DEBUG
```

### Common Log Messages

| Message | Meaning |
|---------|---------|
| `Starting Slack bot with Socket Mode...` | Connection initiated |
| `Received message from {user} in {channel}` | Message received |
| `Creating session: {id}` | New conversation session |
| `Approval {id} timed out` | User didn't respond in time |
