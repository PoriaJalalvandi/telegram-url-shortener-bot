import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import TelegramError

import database
import bot
from app import app
from bot_ui import result_buttons
from configuration import get_base_url, production_mode, public_http_url, validate_runtime
from operational_errors import configure_logging, telegram_error, SAFE_MESSAGE
from utils import create_short_link


class TestProductionConfiguration(unittest.TestCase):
    def test_development_defaults_and_mode_validation(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(production_mode())
            self.assertEqual(get_base_url(), "http://127.0.0.1:5000")
        with patch.dict(os.environ, {"APP_ENV": "prodction"}, clear=True):
            with self.assertRaises(ValueError):
                validate_runtime()

    def test_production_requires_public_https_and_absolute_database(self):
        environment = {"APP_ENV": "production"}
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(ValueError):
                validate_runtime()
            os.environ["BASE_URL"] = "https://links.example.com"
            with self.assertRaises(ValueError):
                validate_runtime()
            for value in ("url_shortener.db", "", ":memory:"):
                os.environ["DATABASE_PATH"] = value
                with self.assertRaises(ValueError):
                    validate_runtime()
            os.environ["DATABASE_PATH"] = "/tmp/porialink-readiness.db"
            validate_runtime()  # Configuration only; no database is created.

    def test_invalid_or_private_production_base_urls_are_rejected(self):
        for value in ("http://links.example.com", "https://localhost", "https://127.0.0.2",
                      "https://10.0.0.1", "https://[::1]", "https://private.internal",
                      "https://links.example.com/path", "https://user:secret@links.example.com",
                      "https://links.example.com?secret=value", "https://bad_host.com", "https://example.com:"):
            with self.subTest(value=value), patch.dict(os.environ, {"APP_ENV": "production", "BASE_URL": value}, clear=True):
                with self.assertRaises(ValueError) as error:
                    get_base_url()
                self.assertNotIn("secret", str(error.exception))

    def test_normalized_origin_and_default_port(self):
        with patch.dict(os.environ, {"BASE_URL": "https://LiNkS.example.com:443/"}, clear=True):
            self.assertEqual(get_base_url(), "https://links.example.com")

    def test_production_debug_rejected_and_empty_database_rejected(self):
        with patch.dict(os.environ, {"APP_ENV": "production", "BASE_URL": "https://links.example.com", "DATABASE_PATH": "/tmp/porialink.db", "FLASK_DEBUG": "1"}, clear=True):
            with self.assertRaises(ValueError):
                validate_runtime()
        with patch.dict(os.environ, {"DATABASE_PATH": ""}, clear=True):
            with self.assertRaises(ValueError):
                validate_runtime()

    def test_explicit_database_path_persists_across_processes(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            db_path = Path(temporary) / "persistent.db"
            environment = {**os.environ, "APP_ENV": "development", "DATABASE_PATH": str(db_path), "PYTHONPATH": str(root)}
            scripts = (
                "import database; database.create_tables(); database.create_user(123, None, 'Test')",
                "import database; assert database.get_user_by_telegram_id(123)[3] == 'Test'",
            )
            for script in scripts:
                result = subprocess.run([sys.executable, "-B", "-c", script], cwd=temporary, env=environment, capture_output=True)
                self.assertEqual(result.returncode, 0)
            self.assertTrue(db_path.is_file())


class TestPublicLinkButtons(unittest.TestCase):
    def test_no_open_button_for_private_or_unsafe_urls(self):
        for value in ("http://localhost/a", "http://127.0.0.2/a", "http://10.0.0.1/a",
                      "http://192.168.1.1/a", "http://169.254.1.1/a", "https://[::1]/a", "http://224.0.0.1/a",
                      "http://host.local/a", "https://host.internal/a", "https://short.example/a", "https://router.home.arpa/a",
                      "http://0x7f.0x0.0x0.0x1/a", "javascript:alert(1)", "https://user:pass@example.com/a"):
            with self.subTest(value=value):
                self.assertFalse(public_http_url(value))
                labels = [button.text for row in result_buttons(value).inline_keyboard for button in row]
                self.assertNotIn("Open Link", labels)

    def test_copy_and_menu_share_row_for_public_url(self):
        markup = result_buttons("https://links.example.com/AbC123")
        self.assertEqual([button.text for button in markup.inline_keyboard[0]], ["Copy Link", "Main Menu"])
        self.assertEqual(markup.inline_keyboard[1][0].text, "Open Link")


class TestOperationalErrors(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_callback_is_acknowledged_without_echoing_payload(self):
        query = SimpleNamespace(data="secret-forged-action", answer=AsyncMock())
        await bot.unknown_callback(SimpleNamespace(callback_query=query), None)
        query.answer.assert_awaited_once_with("This menu is no longer available. Use /start.")

    async def test_startup_api_failure_exits_without_sensitive_traceback(self):
        with patch("bot.build_application", side_effect=TelegramError("secret token endpoint")):
            with self.assertLogs("porialink.operations", level="WARNING") as logs:
                with self.assertRaises(SystemExit) as stopped:
                    bot.main()
        self.assertEqual(stopped.exception.code, 1)
        self.assertNotIn("secret", " ".join(logs.output))

    async def test_global_handler_redacts_exceptions_and_acknowledges_callback(self):
        secret = "TOKEN_CREDENTIAL_ADMIN_ID_PRIVATE_URL_REPORT"
        update = SimpleNamespace(callback_query=SimpleNamespace(answer=AsyncMock()),
                                 effective_message=SimpleNamespace(reply_text=AsyncMock()))
        with self.assertLogs("porialink.operations", level="WARNING") as logs:
            await telegram_error(update, SimpleNamespace(error=RuntimeError(secret)))
        self.assertNotIn(secret, " ".join(logs.output))
        update.callback_query.answer.assert_awaited_once()
        update.effective_message.reply_text.assert_awaited_once_with(SAFE_MESSAGE, parse_mode=None, disable_web_page_preview=True)

    async def test_notice_and_ack_failures_do_not_escape_or_log_payloads(self):
        update = SimpleNamespace(callback_query=SimpleNamespace(answer=AsyncMock(side_effect=TelegramError("secret ack"))),
                                 effective_message=SimpleNamespace(reply_text=AsyncMock(side_effect=TelegramError("secret notice"))))
        with self.assertLogs("porialink.operations", level="WARNING") as logs:
            await telegram_error(update, SimpleNamespace(error=sqlite3.OperationalError("secret database")))
        self.assertEqual(len(logs.output), 3)
        self.assertNotIn("secret", " ".join(logs.output))

    async def test_polling_error_without_update_is_logged_safely(self):
        with self.assertLogs("porialink.operations", level="WARNING") as logs:
            await telegram_error(None, SimpleNamespace(error=TelegramError("secret")))
        self.assertIn("category=telegram", logs.output[0])
        self.assertNotIn("secret", logs.output[0])

    async def test_request_logging_is_suppressed(self):
        names = ("httpx", "httpcore", "telegram", "werkzeug")
        old_levels = {name: logging.getLogger(name).level for name in names}
        try:
            configure_logging()
            for name in names:
                self.assertFalse(logging.getLogger(name).isEnabledFor(logging.CRITICAL))
        finally:
            for name, level in old_levels.items():
                logging.getLogger(name).setLevel(level)


class TestFlaskReadiness(unittest.TestCase):
    def test_debug_is_off_and_liveness_is_public_without_analytics(self):
        self.assertFalse(app.debug)
        response = app.test_client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("analytics", response.text.lower())

    def test_database_read_failure_is_safe_503(self):
        with patch("app.get_link_by_short_code", side_effect=sqlite3.OperationalError("private database path")):
            with self.assertLogs("porialink.operations", level="WARNING") as logs:
                response = app.test_client().get("/test")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", response.text)
        self.assertNotIn("private", " ".join(logs.output))
        self.assertNotIn("Location", response.headers)

    def test_unexpected_error_is_safe_and_http_errors_stay_http_errors(self):
        with patch("app.get_link_by_short_code", side_effect=RuntimeError("secret destination")):
            with self.assertLogs("porialink.operations", level="WARNING"):
                response = app.test_client().get("/test")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("secret", response.text)
        self.assertEqual(app.test_client().post("/test").status_code, 405)


class TestCaseInsensitiveCollisionRetry(unittest.TestCase):
    def test_real_nocase_constraint_failure_is_retried(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(database, "DATABASE", str(Path(temporary) / "test.db")):
            database.create_tables()
            database.create_user(123, None, "Test")
            user_id = database.get_user_by_telegram_id(123)[0]
            database.create_link(user_id, "https://example.com/old", "abc123")
            with patch("utils.generate_unique_short_code", side_effect=["ABC123", "new123"]):
                result = create_short_link(user_id, "https://example.com/new")
            self.assertEqual(result[1], "new123")


if __name__ == "__main__":
    unittest.main()
