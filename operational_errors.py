"""Log fixed error categories only; never exception messages, updates, or URLs."""

import logging
import sqlite3

from telegram.error import TelegramError


logger = logging.getLogger("porialink.operations")
SAFE_MESSAGE = "Something went wrong. Please try again in a moment."


def configure_logging():
    # HTTP libraries can log Telegram token URLs. Werkzeug logs client IPs/queries.
    for name in ("httpx", "httpcore", "telegram", "werkzeug"):
        logging.getLogger(name).setLevel(logging.CRITICAL + 1)


def log_error(event, error):
    category = "database" if isinstance(error, sqlite3.Error) else (
        "telegram" if isinstance(error, TelegramError) else "unexpected"
    )
    logger.warning("%s category=%s", event, category)


async def telegram_error(update, context):
    log_error("telegram_operation_failed", context.error)
    query = getattr(update, "callback_query", None)
    if query is not None:
        try:
            await query.answer()
        except Exception as error:
            log_error("callback_ack_failed", error)
    message = getattr(update, "effective_message", None)
    if message is not None:
        try:
            await message.reply_text(SAFE_MESSAGE, parse_mode=None, disable_web_page_preview=True)
        except Exception as error:
            log_error("error_notice_failed", error)
