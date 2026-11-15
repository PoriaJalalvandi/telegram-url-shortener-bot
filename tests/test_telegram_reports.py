import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
import database
import telegram_reports as reports
from authorization import ACCESS_DENIED
from bot_ui import main_menu, result_buttons, send, status


class ReportDatabaseTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        patcher = patch.object(database, "DATABASE", os.path.join(temporary.name, "test.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        database.create_tables()
        for telegram_id in (101, 202, 303):
            database.create_user(telegram_id, "private_username", "Private Name")
        self.owner_id = database.get_user_by_telegram_id(101)[0]
        self.other_id = database.get_user_by_telegram_id(202)[0]
        self.own_link = database.create_link(self.owner_id, "https://own.example/path?utm_campaign=Launch", "Own123")
        self.other_link = database.create_link(self.other_id, "https://other.example/private", "Other123")
        self.update = SimpleNamespace(
            effective_user=SimpleNamespace(id=101),
            effective_chat=SimpleNamespace(id=101, type="private"),
            effective_message=SimpleNamespace(reply_text=AsyncMock()),
            callback_query=None,
        )
        self.context = SimpleNamespace(bot_data={"admin_ids": frozenset({303}), "base_url": "https://short.example"}, args=[])

    def administrator(self):
        self.update.effective_user.id = 303

    def text(self):
        return self.update.effective_message.reply_text.await_args.args[0]

    def callback(self, data):
        self.update.callback_query = SimpleNamespace(data=data, answer=AsyncMock())

    def click(self, link_id=None, is_bot=None, device_type=None, operating_system=None):
        database.record_link_click(self.own_link if link_id is None else link_id,
                                   is_bot=is_bot, device_type=device_type, operating_system=operating_system)


class TestReportQueries(ReportDatabaseTestCase):
    async def test_owned_query_projects_no_analytics_and_limits_to_ten(self):
        for number in range(12):
            database.create_link(self.owner_id, f"https://own.example/{number}", f"own{number}")
        links = database.list_user_links(101)
        self.assertEqual(len(links), 10)
        self.assertEqual(links[0]["short_code"], "own11")
        self.assertNotIn("Other123", [link["short_code"] for link in links])
        self.assertEqual(set(links[0]), {"short_code", "original_url", "expires_at", "is_active", "created_at"})
        self.assertEqual(database.list_user_links("101 OR 1=1"), [])
        self.assertEqual(database.list_user_links(999), [])

    async def test_overview_keeps_historical_and_event_totals_separate(self):
        for classification in (1, 0, None):
            self.click(is_bot=classification)
        connection = database.get_connection()
        try:
            connection.execute("UPDATE links SET click_count = click_count + 50 WHERE id = ?", (self.other_link,))
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(database.get_analytics_overview(), {
            "users": 3, "links": 2, "recorded_clicks": 53, "events": 3,
            "bots": 1, "non_bots": 1, "unknown": 1,
        })
        self.assertEqual(database.get_link_event_summary(self.other_link)["events"], 0)

    async def test_client_queries_group_unknown_and_scope_by_link(self):
        self.click(is_bot=0, device_type="Mobile", operating_system="Android")
        self.click(device_type="Mobile", operating_system="Android")
        self.click(device_type=None, operating_system=None)
        self.click(link_id=self.other_link, device_type="Desktop", operating_system="Linux")
        self.assertEqual(database.get_client_counts("device_type", link_id=self.own_link), [("Mobile", 2), ("Unknown", 1)])
        self.assertEqual(database.get_client_counts("operating_system", link_id=self.other_link), [("Linux", 1)])
        with self.assertRaises(ValueError):
            database.get_client_counts("referrer; DROP TABLE links")

    async def test_all_links_is_limited_and_contains_no_user_identity(self):
        for number in range(12):
            database.create_link(self.other_id, f"https://other.example/{number}", f"other{number}")
        rows = database.list_recent_links()
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0]["short_code"], "other11")
        self.assertNotIn("user_id", rows[0])


class TestTelegramMenus(ReportDatabaseTestCase):
    async def test_user_and_admin_main_menu_visibility(self):
        def labels():
            return [button.text for row in main_menu(self.update, self.context).inline_keyboard for button in row]
        self.assertEqual(labels(), ["🔗 Shorten URL", "📋 My Links", "❔ Help", "ℹ️ About"])
        self.administrator()
        self.assertIn("Admin Dashboard", labels())
        self.update.effective_chat.type = "group"
        self.assertNotIn("Admin Dashboard", labels())

    async def test_result_buttons_use_supported_copy_and_skip_local_open(self):
        for url, expected in (("https://short.example.com/AbC", ["Copy Link", "Main Menu", "Open Link"]),
                              ("http://127.0.0.1:5000/AbC", ["Copy Link", "Main Menu"])):
            keyboard = result_buttons(url)
            buttons = [button for row in keyboard.inline_keyboard for button in row]
            self.assertEqual([button.text for button in buttons], expected)
            self.assertEqual(buttons[0].copy_text.text, url)

    async def test_navigation_validates_exact_callback_values(self):
        for action in ("nav:shorten", "nav:help", "nav:about", "nav:menu", "nav:mylinks:202", "nav:overview"):
            self.callback(action)
            await bot.navigation(self.update, self.context)
            self.update.callback_query.answer.assert_awaited_once()
            self.assertNotIn("Recorded clicks", self.text())
            self.assertNotIn("other.example", self.text())
        self.assertIn("Unknown action", self.text())

    async def test_owned_list_ignores_claimed_owner_arguments(self):
        self.context.args = ["202"]
        self.click()
        await reports.mylinks(self.update, self.context)
        self.assertIn("own.example", self.text())
        self.assertNotIn("other.example", self.text())
        self.assertNotIn("click", self.text().lower())
        self.assertNotIn("private_username", self.text())

    async def test_owned_list_empty_and_group_privacy(self):
        self.update.effective_user.id = 999
        await reports.mylinks(self.update, self.context)
        self.assertIn("No links yet", self.text())
        self.update.effective_user.id = 101
        self.update.effective_chat.type = "group"
        await reports.mylinks(self.update, self.context)
        self.assertIn("private chat", self.text())
        self.assertNotIn("own.example", self.text())

    async def test_text_is_bounded_and_plain_even_with_long_unicode_urls(self):
        await send(self.update, "😀" * 5000 + "<b>untrusted</b>")
        self.assertLessEqual(len(self.text().encode("utf-16-le")) // 2, 3800)
        self.assertEqual(self.update.effective_message.reply_text.await_args.kwargs["parse_mode"], "HTML")
        connection = database.get_connection()
        try:
            connection.execute("UPDATE links SET original_url = ?", ("https://example.com/" + "a" * 10000,))
            connection.commit()
        finally:
            connection.close()
        await reports.mylinks(self.update, self.context)
        self.assertLess(len(self.text()), 3800)
        self.assertIn("…", self.text())

    async def test_status_matches_redirect_policy(self):
        self.assertEqual(status(1, None), "Active")
        self.assertEqual(status(0, None), "Inactive")
        self.assertEqual(status(1, "2000-01-01T00:00:00Z"), "Expired")
        self.assertIn("invalid expiration", status(1, "invalid"))
        self.assertIn("invalid expiration", status(1, "2099-01-01T00:00:00"))

    async def test_ten_long_link_entries_fit_without_losing_entries(self):
        for number in range(10):
            database.create_link(self.owner_id, "https://example.com/" + "😀" * 1000, f"long{number}")
        await reports.mylinks(self.update, self.context)
        self.assertEqual(self.text().count("Destination:"), 10)
        self.assertLessEqual(len(self.text().encode("utf-16-le")) // 2, 3800)
        self.administrator()
        await reports.alllinks(self.update, self.context)
        self.assertEqual(self.text().count("Created:"), 10)
        self.assertLessEqual(len(self.text().encode("utf-16-le")) // 2, 3800)


class TestAdministratorReports(ReportDatabaseTestCase):
    async def test_all_sensitive_commands_reject_ordinary_users_before_queries(self):
        for handler in (reports.admin_dashboard, reports.overview, reports.stats, reports.daily,
                        reports.campaigns, reports.devices, reports.alllinks):
            with self.subTest(handler=handler.__name__):
                await handler(self.update, self.context)
                self.assertEqual(self.text(), ACCESS_DENIED)

    async def test_sensitive_callbacks_recheck_authorization_and_private_chat(self):
        for sender, chat in ((101, "private"), (303, "group"), (303, "supergroup"), (303, "channel")):
            self.update.effective_user.id = sender
            self.update.effective_chat.type = chat
            for action in ("dashboard", "overview", "daily", "campaigns", "stats", "devices", "alllinks"):
                self.callback("adm:" + action)
                await reports.admin_callback(self.update, self.context)
                self.assertEqual(self.text(), ACCESS_DENIED)
                self.update.callback_query.answer.assert_awaited_once_with(ACCESS_DENIED)

    async def test_admin_command_group_denial_for_all_reports(self):
        self.administrator()
        self.update.effective_chat.type = "group"
        for handler in (reports.overview, reports.stats, reports.daily, reports.campaigns, reports.devices, reports.alllinks):
            await handler(self.update, self.context)
            self.assertEqual(self.text(), ACCESS_DENIED)

    async def test_dashboard_and_authorized_callbacks(self):
        self.administrator()
        await reports.admin_dashboard(self.update, self.context)
        keyboard = self.update.effective_message.reply_text.await_args.kwargs["reply_markup"]
        self.assertEqual([button.text for row in keyboard.inline_keyboard for button in row],
                         ["Overview", "Daily Analytics", "Campaigns", "Link Statistics", "Devices", "All Links", "Export Reports", "Main Menu"])
        for action in ("overview", "daily", "campaigns", "devices", "alllinks", "stats", "menu", "overview:forged"):
            self.callback("adm:" + action)
            await reports.admin_callback(self.update, self.context)
            self.update.callback_query.answer.assert_awaited_once()
        self.assertIn("Unknown action", self.text())

    async def test_overview_text_distinguishes_events_from_counters(self):
        self.administrator()
        self.click(is_bot=1)
        await reports.overview(self.update, self.context)
        for value in ("Registered users: 3", "Links: 2", "historical counters", "Click events: 1", "Detected bots: 1", "heuristic"):
            self.assertIn(value, self.text())

    async def test_daily_includes_seven_utc_days_and_explicit_zeros(self):
        self.administrator()
        self.click(is_bot=1)
        with patch("telegram_reports.datetime", wraps=datetime) as clock, patch(
            "telegram_reports.database.get_daily_click_counts", return_value=[("2026-11-15", 3, 1, 1, 1)]
        ) as query:
            clock.now.return_value = datetime(2026, 11, 15, 23, 59, tzinfo=timezone.utc)
            await reports.daily(self.update, self.context)
        self.assertEqual(query.call_args.kwargs, {
            "start": datetime(2026, 11, 9, tzinfo=timezone.utc),
            "end": datetime(2026, 11, 16, tzinfo=timezone.utc),
        })
        self.assertIn("09 Nov 2026 · UTC\nTotal: 0 · Bots: 0 · Likely non-bots: 0 · Unknown: 0", self.text())
        self.assertIn("15 Nov 2026 · UTC\nTotal: 3 · Bots: 1 · Likely non-bots: 1 · Unknown: 1", self.text())
        self.assertEqual(self.text().count("Nov 2026 · UTC"), 7)

    async def test_campaigns_preserve_case_rank_and_limit_combinations(self):
        self.administrator()
        rows = [(f"Source{i}", "Email", f"Campaign{i}", i) for i in range(8)] + [(None, None, None, 9)]
        with patch("telegram_reports.database.get_campaign_counts", return_value=rows):
            await reports.campaigns(self.update, self.context)
        self.assertEqual(self.text().count("Events:"), 5)
        self.assertIn("Untagged", self.text())
        self.assertIn("Campaign7", self.text())
        self.assertIn("Campaign3", self.text())
        self.assertNotIn("Campaign2", self.text())
        self.assertIn("Untagged events: 9", self.text())
        self.assertIn("Total events: 37", self.text())
        self.assertIn("not verified acquisition", self.text())

    async def test_device_and_os_summaries(self):
        self.administrator()
        self.click(device_type="Mobile", operating_system="Android")
        await reports.devices(self.update, self.context)
        self.assertIn("Mobile: 1", self.text())
        self.assertIn("Android: 1", self.text())
        self.assertIn("heuristic", self.text())

    async def test_daily_reports_real_stored_events(self):
        self.administrator()
        instant = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
        with patch("database.datetime", wraps=datetime) as clock:
            clock.now.return_value = instant
            self.click(is_bot=1)
            self.click(is_bot=0)
            self.click()
        with patch("telegram_reports.datetime", wraps=datetime) as clock:
            clock.now.return_value = instant
            await reports.daily(self.update, self.context)
        self.assertIn("15 Nov 2026 · UTC\nTotal: 3 · Bots: 1 · Likely non-bots: 1 · Unknown: 1", self.text())

    async def test_stats_allows_any_link_only_after_authorization(self):
        self.administrator()
        self.context.args = ["oThEr123"]
        self.click(link_id=self.other_link, is_bot=0)
        await reports.stats(self.update, self.context)
        for expected in ("other.example/private", "Expiration: Never", "Status: Active", "historical counter): 1", "Click events: 1", "Likely non-bots: 1"):
            self.assertIn(expected, self.text())
        self.assertNotIn("private_username", self.text())
        self.context.args = ["missing"]
        await reports.stats(self.update, self.context)
        self.assertEqual(self.text(), "Link not found.")
        self.context.args = ["Other123 OR 1=1"]
        await reports.stats(self.update, self.context)
        self.assertIn("Use /stats", self.text())

    async def test_admin_all_links_includes_status_and_creation_without_identities(self):
        self.administrator()
        await reports.alllinks(self.update, self.context)
        for value in ("own.example", "other.example", "Status: Active", "Created:"):
            self.assertIn(value, self.text())
        self.assertNotIn("private_username", self.text())
        self.assertNotIn("Private Name", self.text())

    async def test_empty_reports_and_generic_database_error(self):
        self.administrator()
        await reports.campaigns(self.update, self.context)
        self.assertIn("No campaign events", self.text())
        await reports.devices(self.update, self.context)
        self.assertIn("No events yet", self.text())
        await reports.overview(self.update, self.context)
        self.assertIn("Click events: 0", self.text())
        with patch("telegram_reports.database.get_analytics_overview", side_effect=sqlite3.OperationalError("internal secret")):
            await reports.overview(self.update, self.context)
        self.assertIn("temporarily unavailable", self.text())
        self.assertNotIn("internal secret", self.text())

    async def test_empty_database_overview_and_alllinks(self):
        self.administrator()
        connection = database.get_connection()
        try:
            connection.execute("DELETE FROM links")
            connection.execute("DELETE FROM users")
            connection.commit()
        finally:
            connection.close()
        await reports.alllinks(self.update, self.context)
        self.assertIn("No links yet", self.text())
        await reports.overview(self.update, self.context)
        for expected in ("Registered users: 0", "Links: 0", "Click events: 0", "Detected bots: 0", "Unknown: 0"):
            self.assertIn(expected, self.text())


if __name__ == "__main__":
    unittest.main()
