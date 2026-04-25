"""Telegram bot launcher.

Thin wrapper that wires together:
- AgentOrchestrator (core agent logic)
- TelegramBot (message handling)
- CronScheduler (scheduled tasks + notification)

All business logic lives in core modules. This script only does setup.

Usage:
    .venv/bin/python scripts/test_telegram.py
"""

import asyncio
import logging
import os
import signal
import subprocess

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from simpleclaw.agent import AgentOrchestrator
from simpleclaw.channels.telegram_bot import TelegramBot
from simpleclaw.config import load_daemon_config, load_telegram_config
from simpleclaw.daemon.scheduler import CronScheduler
from simpleclaw.daemon.store import DaemonStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

CONFIG_PATH = "config.yaml"


def _kill_existing_bots():
    """Kill any other test_telegram.py processes to prevent 409 Conflict."""
    my_pid = os.getpid()
    try:
        result = subprocess.run(
            ["pgrep", "-f", "test_telegram.py"],
            capture_output=True, text=True,
        )
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            pid = int(line.strip())
            if pid != my_pid:
                logger.warning("Killing existing bot process PID %d", pid)
                os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def _create_telegram_notifier(bot_token: str, chat_id: int):
    """Create an async notifier that sends cron results to Telegram."""
    async def notifier(job_name: str, text: str) -> None:
        from telegram import Bot
        tg_bot = Bot(token=bot_token)
        async with tg_bot:
            await tg_bot.send_message(chat_id=chat_id, text=text[:4096])

    return notifier


async def main():
    _kill_existing_bots()

    tg_config = load_telegram_config(CONFIG_PATH)
    if not tg_config["bot_token"]:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in .env")
        return

    # Core modules
    orchestrator = AgentOrchestrator(CONFIG_PATH)

    whitelist = tg_config["whitelist"]
    bot = TelegramBot(
        bot_token=tg_config["bot_token"],
        whitelist_user_ids=whitelist["user_ids"],
        whitelist_chat_ids=whitelist["chat_ids"],
        message_handler=orchestrator.process_message,
    )

    # Cron scheduler — notifier is the only external wiring
    daemon_config = load_daemon_config(CONFIG_PATH)
    daemon_store = DaemonStore(daemon_config["db_path"])
    apscheduler = AsyncIOScheduler()

    notify_chat_id = whitelist["user_ids"][0] if whitelist["user_ids"] else None
    notifier = None
    if notify_chat_id:
        notifier = _create_telegram_notifier(
            tg_config["bot_token"], notify_chat_id
        )
        logger.info("Cron → Telegram notification enabled (chat_id=%d)", notify_chat_id)

    cron = CronScheduler(
        daemon_store, apscheduler,
        agent_orchestrator=orchestrator,
        notifier=notifier,
    )

    # Start cron
    apscheduler.start()
    cron.load_persisted_jobs()
    jobs = cron.list_jobs()
    logger.info("Cron scheduler started with %d jobs.", len(jobs))
    for j in jobs:
        status = "ON" if j.enabled else "OFF"
        logger.info("  [%s] %s: %s → %s", status, j.name, j.cron_expression, j.action_reference[:60])

    # Start bot
    print("Starting Telegram bot...")
    print(f"Whitelist User IDs: {whitelist['user_ids']}")
    print("Press Ctrl+C to stop.\n")

    await bot.start()
    if not bot.is_running:
        print("Bot failed to start. Check your token.")
        return

    print("Bot is running!\n")

    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGINT, stop_event.set)
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)

    await stop_event.wait()
    print("\nStopping...")
    if apscheduler.running:
        apscheduler.shutdown(wait=False)
    await bot.stop()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
