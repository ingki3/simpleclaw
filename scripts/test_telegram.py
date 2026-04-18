"""Telegram bot with full agent orchestration.

Usage:
    .venv/bin/python scripts/test_telegram.py
"""

import asyncio
import logging
import signal

from simpleclaw.agent import AgentOrchestrator
from simpleclaw.config import load_telegram_config
from simpleclaw.channels.telegram_bot import TelegramBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

CONFIG_PATH = "config.yaml"


async def main():
    tg_config = load_telegram_config(CONFIG_PATH)

    if not tg_config["bot_token"]:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in .env")
        return

    # Initialize agent orchestrator (persona + skills + memory + LLM)
    try:
        orchestrator = AgentOrchestrator(CONFIG_PATH)
    except Exception as exc:
        print(f"ERROR: Failed to create orchestrator: {exc}")
        return

    # Create and start bot with orchestrator as message handler
    whitelist = tg_config["whitelist"]
    bot = TelegramBot(
        bot_token=tg_config["bot_token"],
        whitelist_user_ids=whitelist["user_ids"],
        whitelist_chat_ids=whitelist["chat_ids"],
        message_handler=orchestrator.process_message,
    )

    print("Starting Telegram bot with full agent pipeline...")
    print(f"Whitelist User IDs: {whitelist['user_ids']}")
    print("Press Ctrl+C to stop.\n")

    await bot.start()

    if not bot.is_running:
        print("Bot failed to start. Check your token.")
        return

    print("Bot is running! Send a message in Telegram.\n")

    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGINT, stop_event.set)
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)

    await stop_event.wait()
    print("\nStopping bot...")
    await bot.stop()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
