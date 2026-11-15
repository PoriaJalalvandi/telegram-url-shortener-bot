import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from authorization import ACCESS_DENIED, admin_only, is_admin, load_admin_ids


class TestAdminConfiguration(unittest.TestCase):
    def test_missing_or_blank_grants_no_access(self):
        for environment in ({}, {"TELEGRAM_ADMIN_IDS": ""}, {"TELEGRAM_ADMIN_IDS": "  "}):
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(load_admin_ids(), frozenset())

    def test_multiple_ids_are_normalized_and_deduplicated(self):
        with patch.dict(os.environ, {"TELEGRAM_ADMIN_IDS": " 12345, 67890, 0012345 "}):
            self.assertEqual(load_admin_ids(), frozenset({12345, 67890}))

    def test_malformed_configuration_rejected_as_a_whole(self):
        for value in ("12345,admin", "12345,", ",12345", "12345,,67890", "-12345", "0",
                      "+12345", "123.45", "1e5", "۱۲۳۴۵", "12345 67890", str(2**52)):
            with self.subTest(value=value), patch.dict(os.environ, {"TELEGRAM_ADMIN_IDS": value}):
                with self.assertRaises(ValueError) as error:
                    load_admin_ids()
                self.assertNotIn(value, str(error.exception))

    def test_invalid_configuration_stops_startup_before_building(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token", "TELEGRAM_ADMIN_IDS": "12345,bad"}):
            with patch("bot.Application.builder") as builder:
                with self.assertRaisesRegex(ValueError, "TELEGRAM_ADMIN_IDS"):
                    bot.build_application()
                builder.assert_not_called()


class TestAdminAuthorization(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.update = SimpleNamespace(
            effective_user=SimpleNamespace(id=12345, username="ordinary_name"),
            effective_chat=SimpleNamespace(type="private"),
            effective_message=SimpleNamespace(reply_text=AsyncMock(), text="/admin"),
        )
        self.context = SimpleNamespace(bot_data={"admin_ids": frozenset({12345, 67890})})

    async def test_each_authorized_admin_is_confirmed_without_disclosing_ids(self):
        for user_id in (12345, 67890):
            self.update.effective_user.id = user_id
            self.update.effective_message.reply_text.reset_mock()
            self.assertTrue(is_admin(self.update, self.context.bot_data["admin_ids"]))
            await bot.admin(self.update, self.context)
            self.assertIn("Administrator access confirmed.", self.update.effective_message.reply_text.await_args.args[0])
            self.assertNotIn(str(user_id), self.update.effective_message.reply_text.await_args.args[0])

    async def test_spoofed_username_message_callback_and_claimed_id_are_denied(self):
        self.update.effective_user.id = 99999
        self.update.effective_user.username = "admin"
        self.update.effective_message.text = "/admin 12345"
        self.update.callback_query = SimpleNamespace(data="admin:12345")
        self.context.args = ["12345"]
        await bot.admin(self.update, self.context)
        self.update.effective_message.reply_text.assert_awaited_once_with(ACCESS_DENIED)

    async def test_groups_supergroups_and_channels_deny_even_authorized_sender(self):
        for chat_type in ("group", "supergroup", "channel"):
            with self.subTest(chat_type=chat_type):
                self.update.effective_chat.type = chat_type
                self.update.effective_message.reply_text.reset_mock()
                await bot.admin(self.update, self.context)
                self.update.effective_message.reply_text.assert_awaited_once_with(ACCESS_DENIED)

    async def test_missing_allowlist_denies_access(self):
        self.context.bot_data.clear()
        await bot.admin(self.update, self.context)
        self.update.effective_message.reply_text.assert_awaited_once_with(ACCESS_DENIED)

    async def test_unknown_sender_or_chat_denied(self):
        for attribute in ("effective_user", "effective_chat"):
            original = getattr(self.update, attribute)
            setattr(self.update, attribute, None)
            self.update.effective_message.reply_text.reset_mock()
            await bot.admin(self.update, self.context)
            self.update.effective_message.reply_text.assert_awaited_once_with(ACCESS_DENIED)
            setattr(self.update, attribute, original)

    async def test_decorator_checks_every_invocation_before_running_handler(self):
        handler = AsyncMock(return_value="protected result")
        protected = admin_only(handler)
        self.assertEqual(await protected(self.update, self.context), "protected result")
        self.update.effective_user.id = 99999
        await protected(self.update, self.context)
        handler.assert_awaited_once_with(self.update, self.context)

    async def test_denial_without_message_does_not_run_handler(self):
        self.update.effective_user = None
        self.update.effective_message = None
        handler = AsyncMock()
        await admin_only(handler)(self.update, self.context)
        handler.assert_not_awaited()
