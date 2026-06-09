"""CLI entry point for Slack adapter.

Usage:
    python -m amplifier_foundation.slack_adapter --bundle path/to/bundle.yaml
    
Environment variables:
    SLACK_BOT_TOKEN: Slack Bot User OAuth Token (xoxb-...)
    SLACK_APP_TOKEN: Slack App-Level Token for Socket Mode (xapp-...)
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


async def main():
    parser = argparse.ArgumentParser(
        description="Run Amplifier Slack adapter with Socket Mode"
    )
    parser.add_argument(
        "--bundle", "-b",
        required=True,
        help="Path to bundle YAML file",
    )
    parser.add_argument(
        "--bot-token",
        default=os.environ.get("SLACK_BOT_TOKEN"),
        help="Slack Bot Token (or set SLACK_BOT_TOKEN env var)",
    )
    parser.add_argument(
        "--app-token",
        default=os.environ.get("SLACK_APP_TOKEN"),
        help="Slack App Token for Socket Mode (or set SLACK_APP_TOKEN env var)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    
    # Validate tokens
    if not args.bot_token:
        logger.error("SLACK_BOT_TOKEN required (env var or --bot-token)")
        sys.exit(1)
    if not args.app_token:
        logger.error("SLACK_APP_TOKEN required for Socket Mode (env var or --app-token)")
        sys.exit(1)
    
    # Import here to allow graceful error if dependencies missing
    try:
        from amplifier_foundation import load_bundle, compose, prepare
        from amplifier_foundation.slack_adapter import SlackApp, SlackConfig
    except ImportError as e:
        logger.error(f"Missing dependencies: {e}")
        logger.error("Install with: pip install amplifier-foundation[slack-adapter]")
        sys.exit(1)
    
    # Load bundle
    bundle_path = Path(args.bundle)
    if not bundle_path.exists():
        logger.error(f"Bundle not found: {bundle_path}")
        sys.exit(1)
    
    logger.info(f"Loading bundle: {bundle_path}")
    bundle = await load_bundle(str(bundle_path))
    composed = await compose(bundle)
    prepared = await prepare(composed)
    
    # Create session factory
    async def session_factory(channel_id, thread_ts, approval, display):
        session_id = thread_ts or channel_id
        logger.debug(f"Creating session: {session_id}")
        return await prepared.create_session(
            session_id=session_id,
            approval_system=approval,
            display_system=display,
        )
    
    # Create and start Slack app
    config = SlackConfig(
        bot_token=args.bot_token,
        app_token=args.app_token,
    )
    
    slack_app = SlackApp(config, session_factory)
    
    logger.info("Starting Slack adapter...")
    try:
        await slack_app.start()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        await slack_app.stop()


if __name__ == "__main__":
    asyncio.run(main())
