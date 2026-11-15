"""Server-side Telegram administrator checks; no usernames or claimed identities."""

import os
import re
from functools import wraps


ACCESS_DENIED = "Access denied."


def load_admin_ids():
    """Load the entire allowlist or fail startup; never accept a partial list."""
    configured = os.environ.get("TELEGRAM_ADMIN_IDS", "").strip()
    if not configured:
        return frozenset()
    ids = set()
    for item in configured.split(","):
        item = item.strip()
        # Telegram user IDs are positive integers with at most 52 significant bits.
        if not re.fullmatch(r"[0-9]{1,16}", item):
            raise ValueError("TELEGRAM_ADMIN_IDS must contain comma-separated positive Telegram user IDs")
        user_id = int(item)
        if not 0 < user_id < 2**52:
            raise ValueError("TELEGRAM_ADMIN_IDS contains an invalid Telegram user ID")
        ids.add(user_id)
    return frozenset(ids)


def is_admin(update, admin_ids):
    """Authorize only Telegram's authenticated sender in a private bot chat."""
    user = update.effective_user
    chat = update.effective_chat
    return bool(
        user is not None
        and chat is not None
        and chat.type == "private"
        and type(user.id) is int
        and user.id in admin_ids
    )


def admin_only(handler):
    """Check every invocation before entering a protected async command handler."""
    @wraps(handler)
    async def protected(update, context, *args, **kwargs):
        if not is_admin(update, context.bot_data.get("admin_ids", frozenset())):
            query = getattr(update, "callback_query", None)
            if query is not None and getattr(query, "answer", None) is not None:
                await query.answer(ACCESS_DENIED)
            if update.effective_message is not None:
                await update.effective_message.reply_text(ACCESS_DENIED)
            return None
        return await handler(update, context, *args, **kwargs)

    return protected
