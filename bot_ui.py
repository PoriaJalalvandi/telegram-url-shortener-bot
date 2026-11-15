"""Bounded Telegram HTML presentation with all dynamic text escaped."""

from datetime import datetime, timezone
from html import escape

from telegram import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from authorization import is_admin
from telegram.error import BadRequest
from configuration import public_http_url


MAX_MESSAGE_LENGTH = 3800


def shorten(value, limit=200):
    text = str(value)
    # Keep user-controlled labels and URLs on one readable line.
    text = " ".join(text.split())
    raw = text.encode("utf-16-le")
    return text if len(raw) <= limit * 2 else raw[:(limit - 1) * 2].decode("utf-16-le", errors="ignore") + "…"


def main_menu(update, context):
    rows = [
        [InlineKeyboardButton("🔗 Shorten URL", callback_data="nav:shorten"),
         InlineKeyboardButton("📋 My Links", callback_data="nav:mylinks")],
        [InlineKeyboardButton("❔ Help", callback_data="nav:help"),
         InlineKeyboardButton("ℹ️ About", callback_data="nav:about")],
    ]
    if context and is_admin(update, context.bot_data.get("admin_ids", frozenset())):
        rows.append([InlineKeyboardButton("Admin Dashboard", callback_data="adm:dashboard")])
    return InlineKeyboardMarkup(rows)


def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data="adm:" + action)
         for label, action in row]
        for row in (
            (("Overview", "overview"), ("Daily Analytics", "daily")),
            (("Campaigns", "campaigns"), ("Link Statistics", "stats")),
            (("Devices", "devices"), ("All Links", "alllinks")),
            (("Export Reports", "export"),),
            (("Main Menu", "menu"),),
        )
    ])


def back_menu():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("‹ Back", callback_data="adm:dashboard"),
        InlineKeyboardButton("Main Menu", callback_data="nav:menu"),
    ]])


def format_utc(value):
    if value is None:
        return "Never"
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            return "Invalid date"
        parsed = parsed.astimezone(timezone.utc)
        months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        return f"{parsed.day:02d} {months[parsed.month - 1]} {parsed.year} · {parsed:%H:%M} UTC"
    except (TypeError, ValueError, OverflowError):
        return "Invalid date"


def status(is_active, expires_at):
    if is_active != 1:
        return "Inactive"
    if expires_at is not None:
        try:
            expiration = datetime.fromisoformat(expires_at)
            if expiration.utcoffset() is None:
                return "Unavailable (invalid expiration)"
            if expiration.astimezone(timezone.utc) <= datetime.now(timezone.utc):
                return "Expired"
        except (ValueError, TypeError, OverflowError):
            return "Unavailable (invalid expiration)"
    return "Active"


def result_buttons(short_url):
    actions = []
    if len(short_url) <= 256:
        actions.append(InlineKeyboardButton("Copy Link", copy_text=CopyTextButton(short_url)))
    actions.append(InlineKeyboardButton("Main Menu", callback_data="nav:menu"))
    rows = [actions]
    # A public-looking hostname is not proof of DNS/TLS/reachability; operators verify those.
    if public_http_url(short_url) and len(short_url) <= 512:
        rows.append([InlineKeyboardButton("Open Link", url=short_url)])
    return InlineKeyboardMarkup(rows)


async def send(update, text, keyboard=None):
    # Telegram measures length in UTF-16 code units; stay below its 4096 limit.
    raw = text.encode("utf-16-le")
    if len(raw) > MAX_MESSAGE_LENGTH * 2:
        text = raw[:(MAX_MESSAGE_LENGTH - 1) * 2].decode("utf-16-le", errors="ignore") + "…"
    headings = {"Link ready", "Overview", "Inventory", "Historical Counters", "Recorded Events", "Link Details",
                "Performance", "Audience and Devices", "Campaign Attribution", "Devices", "Operating systems"}
    rendered = "\n".join(f"<b>{escape(line)}</b>" if line in headings else escape(line) for line in text.split("\n"))
    options = dict(reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)
    query = getattr(update, "callback_query", None)
    if (query and isinstance(query.data, str) and query.data.startswith(("nav:", "adm:", "exp:"))
            and callable(getattr(query, "edit_message_text", None))):
        try:
            await query.edit_message_text(rendered, **options)
            return
        except BadRequest as error:
            if "message is not modified" in str(error).lower():
                return
    await update.effective_message.reply_text(rendered, **options)
