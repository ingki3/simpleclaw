"""Bootstrap ordering guards for ``scripts/run_bot.py``."""

from pathlib import Path


RUN_BOT_PATH = Path(__file__).parents[2] / "scripts" / "run_bot.py"


def test_redaction_is_installed_before_telegram_channel_starts():
    source = RUN_BOT_PATH.read_text(encoding="utf-8")

    basic_config = source.index("logging.basicConfig(")
    install = source.index("install_telegram_token_redaction()")
    bot_start = source.index("await bot.start()")

    assert basic_config < install < bot_start
