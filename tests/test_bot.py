import os
import tempfile
import unittest
from html import escape
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

import bot
import database


class BotDatabaseTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        patcher = patch.object(
            database, "DATABASE", os.path.join(self.temp_dir.name, "test.db")
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        database.create_tables()
        self.message = SimpleNamespace(reply_text=AsyncMock())
        self.update = SimpleNamespace(
            effective_user=SimpleNamespace(
                id=123456, username="test_user", first_name="Test"
            ),
            effective_message=self.message,
            effective_chat=SimpleNamespace(id=42, type="private"),
        )


class TestBotCommands(BotDatabaseTestCase):
    async def test_start_registers_user_and_replies(self):
        await bot.start(self.update, None)

        user = database.get_user_by_telegram_id(123456)
        self.assertIsNotNone(user)
        self.assertEqual(user[1:4], (123456, "test_user", "Test"))
        self.assertEqual(self.message.reply_text.await_args.args[0], bot.WELCOME_TEXT)
        self.assertIsNotNone(self.message.reply_text.await_args.kwargs["reply_markup"])

    async def test_repeated_start_keeps_one_user(self):
        await bot.start(self.update, None)
        await bot.start(self.update, None)

        connection = database.get_connection()
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1
            )
        finally:
            connection.close()

    async def test_start_without_username(self):
        self.update.effective_user.username = None
        await bot.start(self.update, None)
        self.assertIsNone(database.get_user_by_telegram_id(123456)[2])

    async def test_start_ignores_missing_user_or_message(self):
        with patch("bot.create_user") as create_user:
            await bot.start(
                SimpleNamespace(effective_user=None, effective_message=self.message),
                None,
            )
            self.update.effective_message = None
            await bot.start(self.update, None)
        create_user.assert_not_called()
        self.message.reply_text.assert_not_awaited()

    async def test_help_replies_without_registering_user(self):
        await bot.help_command(self.update, None)
        self.assertEqual(self.message.reply_text.await_args.args[0], escape(bot.HELP_TEXT))
        self.assertIsNone(database.get_user_by_telegram_id(123456))

    async def test_help_ignores_missing_message(self):
        self.update.effective_message = None
        await bot.help_command(self.update, None)
        self.message.reply_text.assert_not_awaited()


class TestBotApplication(unittest.TestCase):
    def test_missing_or_blank_token_fails_before_building(self):
        for environment in ({}, {"TELEGRAM_BOT_TOKEN": "  "}):
            with self.subTest(environment=environment):
                with patch.dict(os.environ, environment, clear=True):
                    with patch("bot.Application.builder") as builder:
                        with self.assertRaisesRegex(ValueError, "TELEGRAM_BOT_TOKEN"):
                            bot.build_application()
                        builder.assert_not_called()

    def test_build_registers_commands_and_url_messages_without_polling(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token", "TELEGRAM_ADMIN_IDS": ""}):
            with patch("bot.Application.builder") as builder:
                settings = builder.return_value.token.return_value
                for method in (
                    "connect_timeout", "read_timeout",
                    "get_updates_connect_timeout", "get_updates_read_timeout",
                ):
                    getattr(settings, method).return_value = settings
                application = settings.build.return_value
                self.assertIs(bot.build_application(), application)

        builder.return_value.token.assert_called_once_with("test-token")
        handlers = [call.args[0] for call in application.add_handler.call_args_list]
        commands = {next(iter(handler.commands)): handler.callback
                    for handler in handlers if hasattr(handler, "commands")}
        self.assertEqual(commands, {
            "start": bot.start, "help": bot.help_command, "admin": bot.admin,
            "cancel": bot.cancel, "mylinks": bot.mylinks, "overview": bot.overview,
            "stats": bot.stats, "daily": bot.daily, "campaigns": bot.campaigns,
            "devices": bot.devices, "alllinks": bot.alllinks, "export": bot.export_command,
        })
        callbacks = [handler.callback for handler in handlers if not hasattr(handler, "commands")]
        self.assertEqual(callbacks, [bot.admin_callback, bot.navigation, bot.export_callback, bot.select_expiration, bot.shorten_message, bot.unknown_callback])
        application.add_error_handler.assert_called_once_with(bot.telegram_error)
        application.run_polling.assert_not_called()

    def test_main_starts_long_polling(self):
        with patch("bot.build_application") as build:
            bot.main()
        build.assert_called_once_with()
        build.return_value.run_polling.assert_called_once_with()


class TestURLMessages(BotDatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.context = SimpleNamespace(bot_data={"base_url": "https://short.example"}, user_data={})
        self.message.text = "https://example.com/page"

    async def begin(self, url="https://example.com/page"):
        self.message.text = url
        await bot.shorten_message(self.update, self.context)
        return self.context.user_data["expiration_flows"][self.update.effective_chat.id]

    async def choose(self, option, nonce=None):
        flow = self.context.user_data["expiration_flows"].get(42)
        self.update.callback_query = SimpleNamespace(
            data=f"expire:{nonce or flow['nonce']}:{option}", answer=AsyncMock()
        )
        await bot.select_expiration(self.update, self.context)

    def links(self):
        connection = database.get_connection()
        try:
            return connection.execute("SELECT * FROM links").fetchall()
        finally:
            connection.close()

    async def test_url_shows_all_options_without_creating_link(self):
        await self.begin()
        self.assertEqual(self.links(), [])
        keyboard = self.message.reply_text.await_args.kwargs["reply_markup"]
        self.assertEqual([button.text for row in keyboard.inline_keyboard for button in row],
                         ["Never", "24 Hours", "7 Days", "30 Days", "Custom Date"])

    async def test_shortening_registers_user_and_uses_sqlite_id(self):
        database.create_user(999, "other", "Other")
        await self.begin()
        with patch("bot.create_short_link", wraps=bot.create_short_link) as create:
            await self.choose("never")
        user = database.get_user_by_telegram_id(123456)
        create.assert_called_once_with(user_id=user[0], original_url=self.message.text, expires_at=None, return_reused=True)
        reply = self.message.reply_text.await_args.args[0]
        self.assertIn("<b>Link ready</b>", reply)
        self.assertIn("https://short.example/", reply)
        self.assertIn("Expiration: Never", reply)
        self.assertIn("New short link created.", reply)
        self.assertNotIn("already exists", reply)
        self.assertEqual(database.get_link_by_short_code(next(line for line in reply.splitlines() if line.startswith("https://")).rsplit("/", 1)[1])[1:3],
                         (user[0], self.message.text))
        self.assertEqual(self.context.user_data["expiration_flows"], {})

    async def test_relative_options_use_selection_time(self):
        for option, days in (("24h", 1), ("7d", 7), ("30d", 30)):
            await self.begin("https://example.com/" + option)
            selected = datetime.now(timezone.utc) + timedelta(hours=1)
            with patch("bot.datetime", wraps=datetime) as clock:
                clock.now.return_value = selected
                await self.choose(option)
            code = next(line for line in self.message.reply_text.await_args.args[0].splitlines() if line.startswith("https://")).rsplit("/", 1)[1]
            self.assertEqual(database.get_link_by_short_code(code)[6],
                             (selected + timedelta(days=days)).isoformat())

    async def test_duplicate_urls_reuse_link_and_actual_expiration(self):
        await self.begin("https://EXAMPLE.com")
        await self.choose("24h")
        first = self.message.reply_text.await_args.args[0]
        await self.begin("https://example.com/")
        await self.choose("never")
        reused = self.message.reply_text.await_args.args[0]
        self.assertEqual(reused.splitlines()[0], first.splitlines()[0])
        self.assertEqual(reused.splitlines()[-1], first.splitlines()[-1])
        self.assertIn("already exists", reused)
        self.assertIn("original expiration remains unchanged", reused)
        self.assertNotIn("New short link created", reused)
        self.assertEqual(len(self.links()), 1)

    async def test_duplicate_never_does_not_acquire_requested_expiration(self):
        await self.begin()
        await self.choose("never")
        await self.begin()
        await self.choose("30d")
        self.assertIn("Expiration: Never", self.message.reply_text.await_args.args[0])
        self.assertIn("already exists", self.message.reply_text.await_args.args[0])
        self.assertIn("original expiration remains unchanged", self.message.reply_text.await_args.args[0])
        self.assertEqual(len(self.links()), 1)

    async def test_custom_date_normalizes_to_utc(self):
        await self.begin()
        await self.choose("custom")
        self.assertEqual(self.links(), [])
        self.message.text = "2099-01-01 12:00 +03:30"
        await bot.shorten_message(self.update, self.context)
        code = next(line for line in self.message.reply_text.await_args.args[0].splitlines() if line.startswith("https://")).rsplit("/", 1)[1]
        self.assertEqual(database.get_link_by_short_code(code)[6], "2099-01-01T08:30:00+00:00")

    async def test_invalid_custom_dates_keep_pending_flow(self):
        await self.begin()
        await self.choose("custom")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        for text in ("invalid", "2099-01-01 12:00", "2000-01-01 12:00 +00:00", now.isoformat()):
            self.message.text = text
            with patch("validators.datetime", wraps=datetime) as clock:
                clock.now.return_value = now
                await bot.shorten_message(self.update, self.context)
            self.assertIn("Invalid expiration", self.message.reply_text.await_args.args[0])
            self.assertEqual(self.links(), [])
            self.assertTrue(self.context.user_data["expiration_flows"][42]["custom"])

    async def test_invalid_messages_do_not_create_users_or_links(self):
        for text in ("hello", "ftp://example.com", "https://example.com https://other.com", "", None):
            self.message.text = text
            self.message.reply_text.reset_mock()
            await bot.shorten_message(self.update, self.context)
            self.message.reply_text.assert_awaited_once_with(bot.INVALID_URL_TEXT)
        self.assertIsNone(database.get_user_by_telegram_id(123456))
        self.assertEqual(self.links(), [])

    async def test_cancel_and_stale_buttons(self):
        flow = await self.begin()
        await bot.cancel(self.update, self.context)
        await self.choose("never", nonce=flow["nonce"])
        self.assertEqual(self.links(), [])
        self.assertIn("no longer active", self.message.reply_text.await_args.args[0])
        await bot.cancel(self.update, self.context)
        self.assertIn("No pending", self.message.reply_text.await_args.args[0])

    async def test_new_url_replaces_old_flow(self):
        old = await self.begin("https://example.com/old")
        new = await self.begin("https://example.com/new")
        await self.choose("never", nonce=old["nonce"])
        self.assertEqual(self.links(), [])
        await self.choose("never", nonce=new["nonce"])
        self.assertEqual(self.links()[0][2], "https://example.com/new")

    async def test_pending_flows_are_isolated_per_chat_and_user(self):
        first = await self.begin()
        self.update.effective_chat.id = 43
        second = await self.begin("https://example.com/other-chat")
        await self.choose("never", nonce=first["nonce"])
        self.assertEqual(self.links(), [])
        other_context = SimpleNamespace(bot_data=self.context.bot_data, user_data={})
        self.update.effective_chat.id = 42
        await bot.select_expiration(self.update, other_context)
        self.assertEqual(self.links(), [])
        await bot.cancel(self.update, self.context)
        self.assertEqual(self.context.user_data["expiration_flows"], {43: second})

    async def test_abandoned_flow_expires_and_unexpected_text_is_helpful(self):
        flow = await self.begin()
        self.message.text = "what next?"
        await bot.shorten_message(self.update, self.context)
        self.assertIn("expiration button", self.message.reply_text.await_args.args[0])
        flow["started"] -= bot.FLOW_TTL
        await self.choose("never", nonce=flow["nonce"])
        self.assertEqual(self.links(), [])
        self.assertIn("no longer active", self.message.reply_text.await_args.args[0])

    async def test_repeated_callback_does_not_create_another_link(self):
        flow = await self.begin()
        await self.choose("never")
        await self.choose("7d", nonce=flow["nonce"])
        self.assertEqual(len(self.links()), 1)

    async def test_database_error_keeps_flow_for_retry(self):
        import sqlite3
        await self.begin()
        with patch("bot.create_short_link", side_effect=sqlite3.OperationalError("test")):
            await self.choose("never")
        self.assertIn("Could not create", self.message.reply_text.await_args.args[0])
        await self.choose("never")
        self.assertEqual(len(self.links()), 1)


class TestBaseURL(unittest.TestCase):
    def test_local_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(bot.get_base_url(), "http://127.0.0.1:5000")

    def test_configured_base_removes_trailing_slash(self):
        with patch.dict(os.environ, {"BASE_URL": "https://short.example/"}):
            self.assertEqual(bot.get_base_url(), "https://short.example")

    def test_invalid_base_fails(self):
        for value in ("", "example.com", "ftp://example.com", "https://example.com/?x=1"):
            with self.subTest(value=value), patch.dict(os.environ, {"BASE_URL": value}):
                with self.assertRaisesRegex(ValueError, "BASE_URL"):
                    bot.get_base_url()


class TestBotNetworkConfiguration(unittest.IsolatedAsyncioTestCase):
    async def test_both_clients_use_longer_timeouts_and_environment_proxy(self):
        # Construct clients only: never initialize the application or send requests.
        environment = {
            "TELEGRAM_BOT_TOKEN": "123456:offline-test-placeholder",
            "HTTPS_PROXY": "http://127.0.0.1:10808",
        }
        with patch.dict(os.environ, environment, clear=True):
            with patch(
                "httpx._client.AsyncHTTPTransport", wraps=httpx.AsyncHTTPTransport
            ) as transport:
                application = bot.build_application()

        requests = application.bot._request
        try:
            for request in requests:
                self.assertEqual(request._client.timeout.connect, 30.0)
                self.assertEqual(request._client.timeout.read, 30.0)
                self.assertTrue(request._client.trust_env)

            proxies = [
                call.kwargs["proxy"].url
                for call in transport.call_args_list
                if call.kwargs.get("proxy") is not None
            ]
            self.assertEqual(
                proxies, [httpx.URL("http://127.0.0.1:10808")] * 2
            )
        finally:
            for request in requests:
                await request.shutdown()


if __name__ == "__main__":
    unittest.main()
