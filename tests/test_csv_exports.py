import csv
import io
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import BadRequest, TelegramError
from telegram import InputFile
from telegram._utils.files import parse_file_input

import bot
import database
import csv_reports
import telegram_exports as exports
from authorization import ACCESS_DENIED
from bot_ui import format_utc, send


class ExportDatabaseTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        patcher = patch.object(database, "DATABASE", os.path.join(temporary.name, "test.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        database.create_tables()
        database.create_user(101, "private", "Private")
        self.link_id = database.create_link(database.get_user_by_telegram_id(101)[0],
            "https://example.com/?utm_source=Telegram&utm_medium=Email&utm_campaign=Launch&utm_content=Hero&utm_term=Term", "AbC123")
        self.connection = database.get_connection()
        self.addCleanup(self.connection.close)
        self.connection.execute("UPDATE links SET created_at = '2026-11-15T12:00:00+00:00'")
        self.connection.commit()
        self.start = datetime(2026, 11, 15, tzinfo=timezone.utc)
        self.end = self.start + timedelta(days=1)
        self.update = SimpleNamespace(
            effective_user=SimpleNamespace(id=101),
            effective_chat=SimpleNamespace(id=101, type="private"),
            effective_message=SimpleNamespace(reply_text=AsyncMock(), reply_document=AsyncMock(), text=""),
            callback_query=None,
        )
        self.context = SimpleNamespace(bot_data={"admin_ids": frozenset({101}), "base_url": "https://short.example"}, user_data={})

    def click(self, instant=None, **metadata):
        with patch("database.datetime", wraps=datetime) as clock:
            clock.now.return_value = instant or self.start + timedelta(hours=12)
            database.record_link_click(self.link_id, **metadata)

    def read_csv(self, kind, **dates):
        with csv_reports.generate_csv(kind, start=dates.get("start", self.start), end=dates.get("end", self.end), base_url="https://short.example") as (document, count, truncated):
            data = document.read()
            self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
            return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig")))), count, truncated

    async def begin(self, kind="events"):
        await exports.export_command(self.update, self.context)
        flow = exports.export_pending(self.update, self.context)
        await self.choose(kind)
        return flow

    async def choose(self, action, nonce=None):
        flow = exports.export_pending(self.update, self.context)
        self.update.callback_query = SimpleNamespace(data=f"exp:{nonce or flow['nonce']}:{action}", answer=AsyncMock())
        await exports.export_callback(self.update, self.context)


class TestCSVGeneration(ExportDatabaseTestCase):
    async def test_all_six_csv_types_have_expected_data_and_headers(self):
        self.click(is_bot=0, device_type="Mobile", operating_system="Android", referrer="https://source.example")
        for kind in csv_reports.HEADERS:
            with self.subTest(kind=kind):
                rows, count, truncated = self.read_csv(kind)
                self.assertEqual(count, 1)
                self.assertFalse(truncated)
                self.assertEqual(tuple(rows[0]), csv_reports.HEADERS[kind])
                if kind == "overview":
                    self.assertEqual(rows[0]["recorded_click_events"], "1")
                    self.assertEqual(rows[0]["all_time_historical_click_counters"], "1")
                elif kind == "links":
                    self.assertEqual(rows[0]["short_url"], "https://short.example/AbC123")
                    self.assertEqual(rows[0]["status"], "Active")
                elif kind == "daily":
                    self.assertEqual(rows[0]["utc_date"], "2026-11-15")
                elif kind == "utm":
                    self.assertEqual(rows[0]["utm_term"], "Term")
                    self.assertEqual(rows[0]["utm_content"], "Hero")
                elif kind == "devices":
                    self.assertEqual(rows[0]["operating_system"], "Android")
                elif kind == "events":
                    self.assertEqual(rows[0]["utm_source"], "Telegram")
                    self.assertEqual(rows[0]["is_bot"], "0")
                    self.assertEqual(rows[0]["clicked_at"], "2026-11-15T12:00:00+00:00")
                    self.assertEqual(rows[0]["referrer"], "https://source.example")
                self.assertNotIn("private", rows[0].values())
                for forbidden in ("ip_address", "user_agent", "telegram_user_id", "visitor_id", "country_code"):
                    self.assertNotIn(forbidden, rows[0])

    async def test_date_filtering_and_all_time_inventory_semantics(self):
        for instant, classification in ((self.start-timedelta(seconds=1), 1), (self.start, 0), (self.end, 1)):
            self.click(instant, is_bot=classification)
        offset = timezone(timedelta(hours=3, minutes=30))
        for kind in ("overview", "daily", "utm", "devices", "events"):
            rows, count, _ = self.read_csv(kind, start=self.start.astimezone(offset), end=self.end.astimezone(offset))
            self.assertEqual(count, 1)
            if kind == "overview":
                self.assertEqual(rows[0]["all_time_historical_click_counters"], "3")
                self.assertEqual(rows[0]["recorded_click_events"], "1")
        rows, count, _ = self.read_csv("links", start=self.end, end=self.end + timedelta(days=1))
        self.assertEqual((rows, count), ([], 0))

    async def test_empty_exports_have_headers_without_fabricated_events(self):
        self.connection.execute("UPDATE links SET click_count = 100")
        self.connection.commit()
        for kind in ("daily", "utm", "devices", "events"):
            self.assertEqual(self.read_csv(kind), ([], 0, False))
        overview, _, _ = self.read_csv("overview")
        self.assertEqual(overview[0]["all_time_historical_click_counters"], "100")
        self.assertEqual(overview[0]["recorded_click_events"], "0")

    async def test_utf8_bom_quoting_and_formula_safety(self):
        self.click()
        self.connection.execute("UPDATE click_events SET utm_campaign = ?, utm_term = ?, utm_content = ?", ("=HYPERLINK(\"bad\")", "سلام, café\nline", "  @SUM(1)"))
        self.connection.commit()
        rows, _, _ = self.read_csv("events")
        self.assertEqual(rows[0]["utm_campaign"], "'=HYPERLINK(\"bad\")")
        self.assertEqual(rows[0]["utm_content"], "'  @SUM(1)")
        self.assertEqual(rows[0]["utm_term"], "سلام, café\nline")
        for value in ("=1", "+1", "-1", "@x", " \t=1", "\rtext", "\ntext", "\ttext", "\ufeff=1"):
            self.assertTrue(csv_reports.safe_cell(value).startswith("'"))

    async def test_row_and_byte_limits_are_explicit(self):
        for _ in range(5):
            self.click()
        with patch("csv_reports.MAX_ROWS", 2):
            rows, count, truncated = self.read_csv("events")
        self.assertEqual((len(rows), count, truncated), (2, 2, True))
        with patch("csv_reports.MAX_BYTES", 400):
            _, _, truncated = self.read_csv("events")
        self.assertTrue(truncated)

    async def test_large_event_export_is_bounded(self):
        self.connection.executemany("INSERT INTO click_events (link_id, clicked_at) VALUES (?, ?)",
                                    [(self.link_id, "2026-11-15T12:00:00+00:00")] * 5002)
        self.connection.commit()
        _, count, truncated = self.read_csv("events")
        self.assertEqual((count, truncated), (5000, True))

    async def test_timestamp_normalization_and_optional_values(self):
        self.connection.execute("UPDATE links SET expires_at = '2099-01-01T12:00:00+03:30'")
        self.connection.commit()
        rows, _, _ = self.read_csv("links")
        self.assertEqual(rows[0]["expires_at"], "2099-01-01T08:30:00+00:00")
        self.assertEqual(csv_reports.utc_timestamp("invalid"), "Invalid timestamp")
        self.assertEqual(csv_reports.utc_timestamp("2099-01-01"), "Invalid timestamp")

    async def test_file_and_generator_close_even_if_consumer_fails(self):
        self.click()
        holder = None
        with self.assertRaises(RuntimeError):
            with csv_reports.generate_csv("events", start=self.start, end=self.end, base_url="https://short.example") as (document, _, _):
                holder = document
                raise RuntimeError("test")
        self.assertTrue(holder.closed)

    async def test_database_query_rejects_invalid_parameters(self):
        with self.assertRaises(ValueError):
            list(database.iter_export_rows("events; DROP TABLE links", start=self.start, end=self.end))
        with self.assertRaises(ValueError):
            list(database.iter_export_rows("events", start=datetime(2026, 1, 1), end=self.end))
        with self.assertRaises(ValueError):
            list(database.iter_export_rows("events", start=self.start, end=self.end, row_limit=100000))


class TestExportTelegramFlow(ExportDatabaseTestCase):
    async def test_options_and_date_ranges(self):
        await exports.export_command(self.update, self.context)
        markup = self.update.effective_message.reply_text.await_args.kwargs["reply_markup"]
        self.assertEqual(len(markup.inline_keyboard), 7)
        await self.choose("events")
        markup = self.update.effective_message.reply_text.await_args.kwargs["reply_markup"]
        self.assertEqual([row[0].text for row in markup.inline_keyboard[:-1]], ["Today", "Last 7 Days", "Last 30 Days", "Custom Date Range"])
        now = datetime(2026, 11, 15, 23, 59, tzinfo=timezone.utc)
        for option, days in (("today", 1), ("7days", 7), ("30days", 30)):
            start, end = exports.date_range(option, now)
            self.assertEqual(end, datetime(2026, 11, 16, tzinfo=timezone.utc))
            self.assertEqual((end-start).days, days)

    async def test_document_send_and_resource_cleanup(self):
        self.click()
        flow = await self.begin()
        captured = {}
        async def capture(**kwargs):
            captured.update(kwargs)
            self.assertIsInstance(kwargs["document"], InputFile)
            captured["bytes"] = kwargs["document"].input_file_content.read()
            self.assertFalse(kwargs["document"].input_file_content.closed)
        self.update.effective_message.reply_document.side_effect = capture
        await exports.deliver_export(self.update, self.context, flow, self.start, self.end)
        self.assertTrue(captured["document"].input_file_content.closed)
        self.assertTrue(captured["bytes"].startswith(b"\xef\xbb\xbf"))
        self.assertIn("1 data rows", captured["caption"])
        self.assertIsNone(exports.export_pending(self.update, self.context))

    async def test_truncated_reader_releases_database_before_upload(self):
        for _ in range(3):
            self.click()
        flow = await self.begin()
        async def capture(**kwargs):
            # An exclusive writer would fail if a truncated reader still held a lock.
            connection = sqlite3.connect(database.DATABASE, timeout=0)
            try:
                connection.execute("BEGIN EXCLUSIVE")
                connection.rollback()
            finally:
                connection.close()
        self.update.effective_message.reply_document.side_effect = capture
        with patch("csv_reports.MAX_ROWS", 2):
            await exports.deliver_export(self.update, self.context, flow, self.start, self.end)
        self.update.effective_message.reply_document.assert_awaited_once()

    async def test_custom_date_validation_and_routing(self):
        await self.begin("daily")
        await self.choose("custom")
        for text in ("invalid", "2026-11-16 2026-11-15", "2026-11-15 2026-11-15", "2020-01-01 2026-11-15", "2026-02-30 2026-03-01"):
            self.update.effective_message.text = text
            await bot.shorten_message(self.update, self.context)
            self.assertIn("Invalid range", self.update.effective_message.reply_text.await_args.args[0])
        self.update.effective_message.text = "2026-11-15 2026-11-16"
        await bot.shorten_message(self.update, self.context)
        self.update.effective_message.reply_document.assert_awaited_once()
        self.assertIn("No matching data", self.update.effective_message.reply_document.await_args.kwargs["caption"])

    async def test_unauthorized_commands_callbacks_and_custom_dates_fail_closed(self):
        flow = await self.begin()
        self.update.effective_user.id = 202
        for handler in (exports.export_command, exports.export_callback, exports.custom_export_range):
            await handler(self.update, self.context)
            self.assertEqual(self.update.effective_message.reply_text.await_args.args[0], ACCESS_DENIED)
        await exports.deliver_export(self.update, self.context, flow, self.start, self.end)
        self.update.effective_message.reply_document.assert_not_awaited()

    async def test_group_and_channel_exports_are_denied(self):
        for chat_type in ("group", "supergroup", "channel"):
            self.update.effective_chat.type = chat_type
            await exports.export_command(self.update, self.context)
            self.assertEqual(self.update.effective_message.reply_text.await_args.args[0], ACCESS_DENIED)
        self.assertEqual(self.context.user_data, {})

    async def test_forged_stale_callbacks_and_scope_isolation(self):
        flow = await self.begin()
        await self.choose("events:forged")
        self.assertIn("expired", self.update.effective_message.reply_text.await_args.args[0])
        await self.choose("today", nonce="wrong")
        self.assertIn("expired", self.update.effective_message.reply_text.await_args.args[0])
        self.update.effective_chat.id = 999
        await self.choose("today", nonce=flow["nonce"])
        self.update.effective_message.reply_document.assert_not_awaited()
        self.update.effective_chat.id = 101
        flow["started"] -= exports.TTL
        await self.choose("today", nonce=flow["nonce"])
        self.assertIsNone(exports.export_pending(self.update, self.context))

    async def test_cancellation_and_menu_navigation_clear_pending_export(self):
        await self.begin()
        await bot.cancel(self.update, self.context)
        self.assertIsNone(exports.export_pending(self.update, self.context))
        await self.begin()
        self.update.callback_query.data = "nav:menu"
        await bot.navigation(self.update, self.context)
        self.assertIsNone(exports.export_pending(self.update, self.context))

    async def test_truncation_is_shown_to_administrator(self):
        for _ in range(3):
            self.click()
        flow = await self.begin()
        with patch("csv_reports.MAX_ROWS", 2):
            await exports.deliver_export(self.update, self.context, flow, self.start, self.end)
        caption = self.update.effective_message.reply_document.await_args.kwargs["caption"]
        self.assertIn("TRUNCATED", caption)
        self.assertIn("narrower range", caption)
        flow = await self.begin()
        with patch("csv_reports.MAX_BYTES", 256):
            await exports.deliver_export(self.update, self.context, flow, self.start, self.end)
        caption = self.update.effective_message.reply_document.await_args.kwargs["caption"]
        self.assertIn("TRUNCATED", caption)
        self.assertIn("No data rows fit", caption)
        self.assertNotIn("No matching data", caption)

    async def test_send_failure_closes_file_and_allows_retry(self):
        flow = await self.begin()
        files = []
        async def fail(**kwargs):
            files.append(kwargs["document"].input_file_content)
            raise TelegramError("private error")
        self.update.effective_message.reply_document.side_effect = fail
        await exports.deliver_export(self.update, self.context, flow, self.start, self.end)
        self.assertTrue(files[0].closed)
        self.assertIsNotNone(exports.export_pending(self.update, self.context))
        self.assertNotIn("private error", self.update.effective_message.reply_text.await_args.args[0])

    async def test_database_error_does_not_send_partial_document(self):
        flow = await self.begin()
        with patch("csv_reports.database.iter_export_rows", side_effect=sqlite3.OperationalError("secret")):
            await exports.deliver_export(self.update, self.context, flow, self.start, self.end)
        self.update.effective_message.reply_document.assert_not_awaited()
        self.assertNotIn("secret", self.update.effective_message.reply_text.await_args.args[0])

    async def test_unnamed_spooled_file_reproduces_ptb_filename_error(self):
        with csv_reports.generate_csv("events", start=self.start, end=self.end, base_url="https://short.example") as (document, _, _):
            self.assertIsNone(document.name)
            # This is the exact PTB conversion previously triggered by reply_document.
            with self.assertRaises(TypeError):
                parse_file_input(document, filename="report.csv")
            document.seek(0)
            upload = InputFile(document, filename="report.csv", read_file_handle=False)
            self.assertIs(parse_file_input(upload), upload)
            self.assertEqual(upload.filename, "report.csv")
            self.assertEqual(upload.mimetype, "text/csv")
            self.assertTrue(upload.input_file_content.read().startswith(b"\xef\xbb\xbf"))

    async def test_full_export_callback_flow_uses_named_csv_for_all_presets(self):
        self.click()
        expected, _, _ = self.read_csv("events")
        instant = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
        captured = []

        async def upload_offline(**kwargs):
            # Exercise PTB's real file conversion rather than accepting any object.
            document = parse_file_input(kwargs["document"])
            self.assertIsInstance(document, InputFile)
            self.assertTrue(document.filename.endswith(".csv"))
            self.assertTrue(document.filename.startswith("porialink-events-"))
            content = document.input_file_content.read()
            self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
            self.assertEqual(list(csv.DictReader(io.StringIO(content.decode("utf-8-sig")))), expected)
            captured.append(document)

        self.update.effective_message.reply_document.side_effect = upload_offline
        for option in ("30days", "today", "7days"):
            with self.subTest(option=option):
                await self.begin("events")  # /export → Select Report
                with patch("telegram_exports.datetime", wraps=datetime) as clock:
                    clock.now.return_value = instant
                    await self.choose(option)  # Date choice → actual document path
                self.assertTrue(captured[-1].input_file_content.closed)
                self.assertIsNone(exports.export_pending(self.update, self.context))
        self.assertEqual(self.update.effective_message.reply_document.await_count, 3)

    async def test_custom_range_uses_same_named_input_file_path(self):
        self.click()
        await self.begin("events")
        await self.choose("custom")
        self.update.effective_message.text = "2026-11-15 2026-11-16"
        captured = []
        async def upload_offline(**kwargs):
            document = parse_file_input(kwargs["document"])
            self.assertIsInstance(document, InputFile)
            self.assertEqual(document.filename, "porialink-events-2026-11-15-2026-11-16.csv")
            self.assertTrue(document.input_file_content.read().startswith(b"\xef\xbb\xbf"))
            captured.append(document)
        self.update.effective_message.reply_document.side_effect = upload_offline
        await exports.custom_export_range(self.update, self.context)
        self.assertEqual(len(captured), 1)
        self.assertTrue(captured[0].input_file_content.closed)


class TestHTMLPresentation(unittest.IsolatedAsyncioTestCase):
    async def test_dynamic_html_is_escaped_and_static_headings_are_bold(self):
        update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=AsyncMock()), callback_query=None)
        await send(update, 'Overview\n<script>x</script> & "quoted"')
        text = update.effective_message.reply_text.await_args.args[0]
        self.assertIn("<b>Overview</b>", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertNotIn("<script>", text)
        self.assertEqual(update.effective_message.reply_text.await_args.kwargs["parse_mode"], "HTML")

    async def test_menu_navigation_edits_and_unchanged_messages_are_ignored(self):
        query = SimpleNamespace(data="nav:menu", edit_message_text=AsyncMock())
        update = SimpleNamespace(callback_query=query, effective_message=SimpleNamespace(reply_text=AsyncMock()))
        await send(update, "Menu")
        query.edit_message_text.assert_awaited_once()
        update.effective_message.reply_text.assert_not_awaited()
        query.edit_message_text.side_effect = BadRequest("Message is not modified")
        await send(update, "Menu")
        update.effective_message.reply_text.assert_not_awaited()
        query.edit_message_text.side_effect = BadRequest("Message cannot be edited")
        await send(update, "Menu")
        update.effective_message.reply_text.assert_awaited_once()

    async def test_readable_dates_preserve_utc(self):
        self.assertEqual(format_utc("2026-11-16T21:08:00+03:30"), "16 Nov 2026 · 17:38 UTC")
        self.assertEqual(format_utc(None), "Never")
        self.assertEqual(format_utc("naive"), "Invalid date")


if __name__ == "__main__":
    unittest.main()
