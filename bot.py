"""Telegram command interface. Run explicitly to start local long polling."""

import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from database import create_user, get_user_by_telegram_id, get_link_by_short_code
from utils import create_short_link
from validators import validate_url, normalize_expires_at
from authorization import load_admin_ids
from configuration import get_base_url, validate_runtime
from operational_errors import configure_logging, telegram_error, log_error
from bot_ui import main_menu, result_buttons, send, status, format_utc
from telegram_exports import export_command, export_callback, export_pending, export_flows, custom_export_range
from telegram_reports import (
    admin_dashboard as admin, admin_callback, mylinks, overview, daily, campaigns,
    devices, stats, alllinks,
)


HELP_TEXT = (
    "PoriaLinkBot · Help\n\n"
    "Shorten a link\n"
    "Send one HTTP/HTTPS URL, then choose Never, 24 Hours, 7 Days, 30 Days, or Custom Date.\n"
    "For a custom expiration, enter YYYY-MM-DD HH:MM +03:30 with a timezone.\n"
    "Existing links keep their original expiration. Results show UTC dates.\n\n"
    "Your commands\n/start — Main Menu\n/mylinks — Your latest 10 links in private chat\n"
    "/cancel — Cancel pending link creation or export\n/help — These instructions\n\n"
    "Administrators · Private chats only\n/admin — Dashboard\n"
    "/overview · /stats <short_code> · /daily · /campaigns · /devices · /alllinks\n"
    "/export — Private CSV downloads with UTC date filters\n\n"
    "No administrator access? You can still shorten URLs and view your own links."
)
WELCOME_TEXT = "Welcome to PoriaLinkBot\n\nShorten a URL and choose when it expires. Send a link or choose an option below."

INVALID_URL_TEXT = "Please send a single valid HTTP/HTTPS URL, such as https://example.com/page."
FLOW_TTL = 30 * 60
CUSTOM_PROMPT = "Enter a future date/time with a timezone: YYYY-MM-DD HH:MM +03:30, or /cancel."


def pending_flows(context):
    # PTB user_data is scoped to the Telegram user; the second key isolates chats.
    flows = context.user_data.setdefault("expiration_flows", {})
    now = time.monotonic()
    for chat_id in list(flows):
        if now - flows[chat_id]["started"] >= FLOW_TTL:
            del flows[chat_id]
    return flows


async def shorten_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    url = (message.text or "").strip()
    if context:
        export = export_pending(update, context)
        if export and export["custom"] and not validate_url(url):
            await custom_export_range(update, context)
            return
        if validate_url(url):
            export_flows(context).pop(update.effective_chat.id, None)
    if not validate_url(url):
        flow = pending_flows(context).get(update.effective_chat.id) if context else None
        if flow and flow["custom"]:
            try:
                expiration = normalize_expires_at(url)
            except ValueError:
                await message.reply_text("Invalid expiration. " + CUSTOM_PROMPT)
                return
            await finish_link(update, context, flow, expiration)
            return
        if flow:
            await message.reply_text("Choose an expiration button, send another URL, or /cancel.")
            return
        await message.reply_text(INVALID_URL_TEXT)
        return

    flow = {"url": url, "nonce": secrets.token_hex(8), "started": time.monotonic(), "custom": False}
    pending_flows(context)[update.effective_chat.id] = flow
    buttons = [
        InlineKeyboardButton(label, callback_data=f"expire:{flow['nonce']}:{option}")
        for label, option in (
            ("Never", "never"), ("24 Hours", "24h"), ("7 Days", "7d"),
            ("30 Days", "30d"), ("Custom Date", "custom"),
        )
    ]
    await message.reply_text(
        "Choose when this link should expire (selection expires in 30 minutes):",
        reply_markup=InlineKeyboardMarkup([buttons[:2], buttons[2:4], buttons[4:]]),
    )


async def select_expiration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    flow = pending_flows(context).get(update.effective_chat.id)
    parts = (query.data or "").split(":")
    if not flow or len(parts) != 3 or parts[:2] != ["expire", flow["nonce"]]:
        await update.effective_message.reply_text("This selection is no longer active. Send a URL to begin again.")
        return
    option = parts[2]
    if option == "custom":
        flow["custom"] = True
        await update.effective_message.reply_text(CUSTOM_PROMPT)
        return
    durations = {"24h": 1, "7d": 7, "30d": 30}
    if option != "never" and option not in durations:
        await update.effective_message.reply_text("Please use an expiration button or /cancel.")
        return
    expiration = None if option == "never" else (
        datetime.now(timezone.utc) + timedelta(days=durations[option])
    ).isoformat()
    await finish_link(update, context, flow, expiration)


async def finish_link(update, context, flow, expiration):
    user = update.effective_user
    message = update.effective_message
    try:
        stored_user = get_user_by_telegram_id(user.id)
        if stored_user is None:
            create_user(user.id, user.username, user.first_name)
            stored_user = get_user_by_telegram_id(user.id)
        _, short_code, reused = create_short_link(
            user_id=stored_user[0], original_url=flow["url"], expires_at=expiration,
            return_reused=True,
        )
        link = get_link_by_short_code(short_code)
    except (sqlite3.Error, RuntimeError, ValueError) as error:
        log_error("link_creation_failed", error)
        await message.reply_text("Could not create the link. Try selecting again, send a new URL, or /cancel.")
        return
    pending_flows(context).pop(update.effective_chat.id, None)
    actual = format_utc(link[6])
    feedback = (
        "This short link already exists. Its original expiration remains unchanged."
        if reused else "New short link created."
    )
    short_url = f"{context.bot_data['base_url']}/{short_code}"
    await send(update,
        f"Link ready\n\n{short_url}\n\n{feedback}\nStatus: {status(link[7], link[6])}\nExpiration: {actual}",
        result_buttons(short_url))


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    flow = pending_flows(context).pop(update.effective_chat.id, None)
    export = export_flows(context).pop(update.effective_chat.id, None)
    await update.effective_message.reply_text(
        "Cancelled. Send a URL to start again." if flow or export else "No pending link. Send a URL to begin."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    create_user(
        telegram_user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )
    if context:
        export_flows(context).pop(update.effective_chat.id, None)
    await send(update, WELCOME_TEXT, main_menu(update, context))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_message is not None:
        await send(update, HELP_TEXT, main_menu(update, context))


async def navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    if hasattr(context, "user_data"):
        export_flows(context).pop(update.effective_chat.id, None)
    action = update.callback_query.data
    if action == "nav:mylinks":
        await mylinks(update, context)
    elif action == "nav:help":
        await help_command(update, context)
    elif action == "nav:shorten":
        await send(update, "Send one HTTP/HTTPS URL. Then choose its expiration. Use /cancel to stop.", main_menu(update, context))
    elif action == "nav:about":
        await send(update, "PoriaLinkBot\n\nCreate short links with optional expiration. Repeated destinations reuse your eligible link without changing its expiration. Your link list is private; analytics and CSV exports are administrator-only. No unique-visitor estimates are collected.", main_menu(update, context))
    elif action == "nav:menu":
        await send(update, "PoriaLinkBot · Main Menu", main_menu(update, context))
    else:
        await send(update, "Unknown action. Choose a menu button.", main_menu(update, context))


async def unknown_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("This menu is no longer available. Use /start.")


def build_application():
    """Configure handlers without connecting to Telegram."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("Set the TELEGRAM_BOT_TOKEN environment variable.")
    configure_logging()
    validate_runtime()
    base_url = get_base_url()
    admin_ids = load_admin_ids()

    # PTB uses separate HTTPX clients for API calls and getUpdates polling.
    # Both retain HTTPX's default environment proxy handling (HTTPS_PROXY,
    # ALL_PROXY, and NO_PROXY). Allow extra time for proxy connections.
    application = (
        Application.builder()
        .token(token)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .get_updates_connect_timeout(30.0)
        .get_updates_read_timeout(30.0)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("admin", admin))
    for command, handler in (
        ("mylinks", mylinks), ("overview", overview), ("stats", stats),
        ("daily", daily), ("campaigns", campaigns), ("devices", devices), ("alllinks", alllinks),
        ("export", export_command),
    ):
        application.add_handler(CommandHandler(command, handler))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^adm:"))
    application.add_handler(CallbackQueryHandler(navigation, pattern=r"^nav:"))
    application.add_handler(CallbackQueryHandler(export_callback, pattern=r"^exp:"))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CallbackQueryHandler(select_expiration, pattern=r"^expire:"))
    application.bot_data["base_url"] = base_url
    application.bot_data["admin_ids"] = admin_ids
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, shorten_message))
    application.add_handler(CallbackQueryHandler(unknown_callback))
    application.add_error_handler(telegram_error)
    return application


def main():
    configure_logging()
    try:
        application = build_application()
        application.run_polling()
    except Exception as error:
        log_error("bot_startup_failed", error)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
