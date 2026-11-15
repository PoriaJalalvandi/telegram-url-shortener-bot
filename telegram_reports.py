"""Owned-link views and private administrator reporting commands/callbacks."""

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps

import database
from authorization import admin_only
from bot_ui import admin_menu, back_menu, main_menu, send, shorten, status, format_utc
from telegram_exports import export_command, export_flows
from operational_errors import log_error


def report_errors(handler):
    @wraps(handler)
    async def protected(update, context):
        try:
            return await handler(update, context)
        except sqlite3.Error as error:
            log_error("report_failed", error)
            await send(update, "Report temporarily unavailable. Please try again.")
    return protected


def event_lines(summary):
    return (f"Click events: {summary['events']}\n"
            f"Detected bots: {summary['bots']}\n"
            f"Likely non-bots: {summary['non_bots']}\n"
            f"Unknown: {summary['unknown']}")


def link_lines(link, context, *, created=False):
    url = context.bot_data['base_url'] + "/" + link['short_code']
    lines = (f"{shorten(url, 80)}\n"
             f"Destination: {shorten(link['original_url'], 80)}\n"
             f"Expiration: {format_utc(link['expires_at'])}\n"
             f"Status: {status(link['is_active'], link['expires_at'])}")
    if created:
        lines += f"\nCreated: {format_utc(link['created_at'])}"
    return lines


@report_errors
async def mylinks(update, context):
    if not update.effective_user or not update.effective_chat or update.effective_chat.type != "private":
        await send(update, "Open a private chat with the bot to view your links.")
        return
    # Never accept an owner ID from command arguments or callback payloads.
    links = database.list_user_links(update.effective_user.id)
    text = "My Links · Latest 10\n\n" + (
        "\n\n".join(f"{number}. " + link_lines(link, context) for number, link in enumerate(links, 1))
        if links else "No links yet. Send a URL to create your first link."
    )
    await send(update, text, main_menu(update, context))


@admin_only
async def admin_dashboard(update, context):
    if hasattr(context, "user_data"):
        export_flows(context).pop(update.effective_chat.id, None)
    await send(update, "Administrator access confirmed.\n\nPoriaLinkBot · Admin Dashboard\nChoose a report.", admin_menu())


@admin_only
@report_errors
async def overview(update, context):
    summary = database.get_analytics_overview()
    await send(update,
        f"Overview\n\nInventory\nRegistered users: {summary['users']}\nLinks: {summary['links']}\n\nHistorical Counters\n"
        f"Recorded clicks (historical counters): {summary['recorded_clicks']}\n\n"
        + "Recorded Events\n" + event_lines(summary)
        + "\n\nEvents are recorded visits, not unique visitors. Historical clicks may predate events. Bot classification is heuristic.",
        back_menu())


@admin_only
@report_errors
async def daily(update, context):
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start, end = today - timedelta(days=6), today + timedelta(days=1)
    rows = {row[0]: row[1:] for row in database.get_daily_click_counts(start=start, end=end)}
    lines = ["Daily Analytics · Last 7 UTC days", ""]
    for offset in range(7):
        day = (start + timedelta(days=offset)).date().isoformat()
        values = rows.get(day, (0, 0, 0, 0))
        label = format_utc(day + "T00:00:00+00:00").replace(" · 00:00 UTC", " · UTC")
        lines.append(f"{label}\nTotal: {values[0]} · Bots: {values[1]} · Likely non-bots: {values[2]} · Unknown: {values[3]}\n")
    await send(update, "\n".join(lines) + "\nBot classification is heuristic.", back_menu())


def campaign_text(rows):
    if not rows:
        return "No campaign events yet."
    tagged = [row for row in rows if any(value is not None for value in row[:3])]
    untagged = sum(row[3] for row in rows if all(value is None for value in row[:3]))
    top = sorted(tagged, key=lambda row: (-row[3], tuple(value or "" for value in row[:3])))[:5]
    body = "\n\n".join(
        f"Source: {shorten(source if source is not None else 'Untagged', 100)}\n"
        f"Medium: {shorten(medium if medium is not None else 'Untagged', 100)}\n"
        f"Campaign: {shorten(campaign if campaign is not None else 'Untagged', 100)}\nEvents: {count}"
        for source, medium, campaign, count in top
    )
    return f"Total events: {sum(row[3] for row in rows)}\nUntagged events: {untagged}\n\n" + (body or "No tagged campaigns yet.")


@admin_only
@report_errors
async def campaigns(update, context):
    await send(update, "Campaigns · Top 5 combinations\n\n" + campaign_text(database.get_campaign_counts())
               + "\n\nDestination UTM labels; not verified acquisition sources. Counts are click events.", back_menu())


def client_text(link_id=None):
    parts = []
    for title, dimension in (("Devices", "device_type"), ("Operating systems", "operating_system")):
        rows = database.get_client_counts(dimension, link_id=link_id)
        parts.append(title + "\n" + ("\n".join(f"{shorten(label, 70)}: {count}" for label, count in rows) or "No events yet."))
    return "\n\n".join(parts)


@admin_only
@report_errors
async def devices(update, context):
    await send(update, "Client Analytics · Click events\n\n" + client_text()
               + "\n\nDevice, OS, and bot classifications are heuristic.", back_menu())


@admin_only
@report_errors
async def stats(update, context):
    args = context.args or []
    if len(args) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args[0]):
        await send(update, "Use /stats <short_code> in this private chat.", admin_menu())
        return
    link = database.get_link_by_short_code(args[0])
    if link is None:
        await send(update, "Link not found.", admin_menu())
        return
    details = dict(zip(("id", "user_id", "original_url", "short_code", "is_custom", "created_at", "expires_at", "is_active", "click_count", "last_click_at"), link))
    text = ("Link Statistics\n\nLink Details\n" + link_lines(details, context)
            + f"\n\nPerformance\nRecorded clicks (historical counter): {link[8]}\nLast click (UTC): {format_utc(link[9])}\n\n"
            + event_lines(database.get_link_event_summary(link[0])) + "\n\nAudience and Devices\n" + client_text(link[0])
            + "\n\nCampaign Attribution\n" + campaign_text(database.get_campaign_counts(link_id=link[0]))
            + "\n\nEvent totals may differ from historical counters. Classification is heuristic.")
    await send(update, text, back_menu())


@admin_only
@report_errors
async def alllinks(update, context):
    links = database.list_recent_links()
    await send(update, "All Links · Latest 10\n\n" + (
        "\n\n".join(link_lines(link, context, created=True) for link in links) if links else "No links yet."
    ), back_menu())


@admin_only
async def admin_callback(update, context):
    await update.callback_query.answer()
    actions = {
        "adm:dashboard": admin_dashboard, "adm:overview": overview, "adm:daily": daily,
        "adm:campaigns": campaigns, "adm:devices": devices, "adm:alllinks": alllinks,
        "adm:export": export_command,
    }
    action = update.callback_query.data
    if action in actions:
        await actions[action](update, context)
    elif action == "adm:stats":
        await send(update, "Use /stats <short_code> in this private chat.", admin_menu())
    elif action == "adm:menu":
        await send(update, "PoriaLinkBot · Main Menu", main_menu(update, context))
    else:
        await send(update, "Unknown action. Choose a dashboard button.", admin_menu())
