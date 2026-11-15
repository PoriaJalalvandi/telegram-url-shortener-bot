"""Private administrator export selection. State is scoped to user and chat."""

import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.error import TelegramError

from authorization import admin_only
from bot_ui import send
from csv_reports import HEADERS, MAX_BYTES, MAX_ROWS, generate_csv
from operational_errors import log_error


EXPORT_NAMES = {
    "overview": "Overview", "links": "Links", "daily": "Daily Analytics",
    "utm": "UTM Analytics", "devices": "Device and OS Analytics", "events": "Click Events",
}
TTL = 1800


def export_flows(context):
    flows = context.user_data.setdefault("export_flows", {})
    for key in list(flows):
        if time.monotonic() - flows[key]["started"] >= TTL:
            del flows[key]
    return flows


def export_pending(update, context):
    return export_flows(context).get(update.effective_chat.id)


def keyboard(flow, entries):
    rows = [[InlineKeyboardButton(label, callback_data=f"exp:{flow['nonce']}:{action}")]
            for label, action in entries]
    rows.append([InlineKeyboardButton("‹ Back", callback_data="adm:dashboard"),
                 InlineKeyboardButton("Main Menu", callback_data="nav:menu")])
    return InlineKeyboardMarkup(rows)


@admin_only
async def export_command(update, context):
    flow = {"nonce": secrets.token_hex(8), "started": time.monotonic(), "type": None, "custom": False}
    export_flows(context)[update.effective_chat.id] = flow
    await send(update, "Export Reports\n\nChoose a CSV report. Exports are private and use UTC dates.",
               keyboard(flow, [(name, kind) for kind, name in EXPORT_NAMES.items()]))


def date_range(choice, now=None):
    today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    days = {"today": 1, "7days": 7, "30days": 30}[choice]
    return today - timedelta(days=days - 1), today + timedelta(days=1)


def parse_custom_range(text):
    # The dates themselves are UTC: start date inclusive, end date exclusive.
    parts = text.strip().split()
    if len(parts) != 2 or any(len(part) != 10 for part in parts):
        raise ValueError("Invalid range")
    start, end = (datetime.strptime(part, "%Y-%m-%d").replace(tzinfo=timezone.utc) for part in parts)
    if start >= end or (end - start).days > 366:
        raise ValueError("Invalid range")
    return start, end


@admin_only
async def export_callback(update, context):
    await update.callback_query.answer()
    flow = export_pending(update, context)
    parts = (update.callback_query.data or "").split(":")
    if not flow or len(parts) != 3 or parts[:2] != ["exp", flow["nonce"]]:
        await send(update, "This export selection expired. Use /export to start again.")
        return
    action = parts[2]
    if action in EXPORT_NAMES:
        flow.update(type=action, custom=False)
        await send(update, f"Export: {EXPORT_NAMES[action]}\nChoose a UTC date range.", keyboard(flow, [
            ("Today", "today"), ("Last 7 Days", "7days"), ("Last 30 Days", "30days"), ("Custom Date Range", "custom"),
        ]))
    elif flow["type"] in HEADERS and action == "custom":
        flow["custom"] = True
        await send(update, "Enter two UTC dates: YYYY-MM-DD YYYY-MM-DD\nStart is inclusive; end is exclusive. Maximum 366 days. Use /cancel to stop.")
    elif flow["type"] in HEADERS and action in ("today", "7days", "30days"):
        await deliver_export(update, context, flow, *date_range(action))
    else:
        await send(update, "Invalid export choice. Use the buttons or /export.")


@admin_only
async def custom_export_range(update, context):
    flow = export_pending(update, context)
    if not flow or not flow["custom"] or flow["type"] not in HEADERS:
        await send(update, "Use /export to choose a report first.")
        return
    try:
        start, end = parse_custom_range(update.effective_message.text or "")
    except ValueError:
        await send(update, "Invalid range. Enter YYYY-MM-DD YYYY-MM-DD in UTC; end must be later than start, up to 366 days. Or /cancel.")
        return
    await deliver_export(update, context, flow, start, end)


@admin_only
async def deliver_export(update, context, flow, start, end):
    try:
        with generate_csv(flow["type"], start=start, end=end, base_url=context.bot_data["base_url"]) as (document, count, truncated):
            notice = "TRUNCATED: " if truncated else ""
            caption = (f"{notice}{EXPORT_NAMES[flow['type']]} · {count} data rows\n"
                       f"UTC [{start.date()}, {end.date()})\n"
                       "Event dates filtered; Links uses creation dates. Overview inventory/counters are all-time.")
            if truncated:
                caption += f"\nLimit: {MAX_ROWS} rows / {MAX_BYTES // (1024 * 1024)} MiB. Choose a narrower range."
            if count == 0:
                caption += ("\nNo data rows fit within the export limit; CSV contains headers only."
                            if truncated else "\nNo matching data; CSV contains column headers only.")
            filename = f"porialink-{flow['type']}-{start.date()}-{end.date()}.csv"
            # SpooledTemporaryFile.name can be None. Avoid PTB's eager load_file
            # filename guessing; the context keeps this stream open until upload ends.
            upload = InputFile(document, filename=filename, read_file_handle=False)
            await update.effective_message.reply_document(
                document=upload,
                caption=caption, parse_mode=None,
            )
        export_flows(context).pop(update.effective_chat.id, None)
    except (sqlite3.Error, OSError, TelegramError) as error:
        log_error("export_failed", error)
        await send(update, "Export unavailable. Please retry or choose a smaller range.")
