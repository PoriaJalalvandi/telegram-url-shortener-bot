import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import patch

import database
from app import app
from utils import create_short_link, create_custom_link
from validators import normalize_expires_at


class TestExpirationValidation(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
        patcher = patch("validators.datetime", wraps=datetime)
        clock = patcher.start()
        clock.now.return_value = self.now
        self.addCleanup(patcher.stop)

    def test_none_means_no_expiration(self):
        self.assertIsNone(normalize_expires_at(None))

    def test_future_and_offsets_normalize_to_utc(self):
        for value, expected in (
            ("2026-11-16T12:00:00Z", "2026-11-16T12:00:00+00:00"),
            ("2026-11-16T15:30:00+03:30", "2026-11-16T12:00:00+00:00"),
            ("2026-11-16T08:00:00-04:00", "2026-11-16T12:00:00+00:00"),
            ("2026-11-16T12:00:00.123456+00:00", "2026-11-16T12:00:00.123456+00:00"),
        ):
            with self.subTest(value=value):
                self.assertEqual(normalize_expires_at(value), expected)

    def test_rejects_invalid_naive_past_and_current_values(self):
        for value in (
            "", "invalid", "2026-99-99T00:00:00Z", "2026-11-16",
            "2026-11-16T12:00:00", "2026-11-15T11:59:59+00:00",
            self.now.isoformat(), "2026-11-15T15:30:00+03:30", 123,
            self.now + timedelta(days=1),
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "expires_at"):
                    normalize_expires_at(value)


class TestExpirationServices(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        patcher = patch.object(database, "DATABASE", os.path.join(temporary.name, "test.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        database.create_tables()
        database.create_user(123, "tester", "Test")
        self.user_id = database.get_user_by_telegram_id(123)[0]
        self.future = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)

    def test_omitted_expiration_for_both_services(self):
        for result in (
            create_short_link(self.user_id, "https://example.com/auto"),
            create_custom_link(self.user_id, "https://example.com/custom", "custom"),
        ):
            self.assertIsNone(database.get_link_by_short_code(result[1])[6])

    def test_future_expiration_persisted_as_utc_for_both_services(self):
        offset = self.future.astimezone(timezone(timedelta(hours=3, minutes=30))).isoformat()
        for result in (
            create_short_link(self.user_id, "https://example.com/auto", expires_at=offset),
            create_custom_link(self.user_id, "https://example.com/custom", "custom", expires_at=offset),
        ):
            self.assertEqual(database.get_link_by_short_code(result[1])[6], self.future.isoformat())

    def test_invalid_expiration_creates_no_links(self):
        for value in ("invalid", "2099-01-01T00:00:00", "2000-01-01T00:00:00Z"):
            for create in (
                lambda: create_short_link(self.user_id, "https://example.com", expires_at=value),
                lambda: create_custom_link(self.user_id, "https://example.com", "custom", expires_at=value),
            ):
                with self.assertRaises(ValueError):
                    create()
        connection = database.get_connection()
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM links").fetchone()[0], 0)
        finally:
            connection.close()

    def test_duplicate_keeps_original_expiration(self):
        for index, initial in enumerate((None, self.future.isoformat())):
            url = f"https://example.com/{index}"
            first = create_short_link(self.user_id, url, expires_at=initial)
            second = create_short_link(
                self.user_id, url, expires_at=(self.future + timedelta(days=1)).isoformat()
            )
            self.assertEqual(first, second)
            self.assertEqual(database.get_link_by_short_code(first[1])[6], initial)
            self.assertEqual(create_short_link(self.user_id, url), first)
            self.assertEqual(database.get_link_by_short_code(first[1])[6], initial)

    def test_expired_link_is_not_reused(self):
        first = create_short_link(self.user_id, "https://example.com")
        connection = database.get_connection()
        try:
            connection.execute("UPDATE links SET expires_at = ? WHERE id = ?", (
                "2000-01-01T00:00:00+00:00", first[0],
            ))
            connection.commit()
        finally:
            connection.close()
        second = create_short_link(self.user_id, "https://example.com", expires_at=self.future.isoformat())
        self.assertNotEqual(first, second)
        self.assertEqual(database.get_link_by_short_code(second[1])[6], self.future.isoformat())

    def test_custom_alias_remains_unique_with_expiration(self):
        create_custom_link(self.user_id, "https://example.com", "Custom", expires_at=self.future.isoformat())
        with self.assertRaisesRegex(ValueError, "Alias already taken"):
            create_custom_link(self.user_id, "https://other.example", "CUSTOM", expires_at=(self.future + timedelta(days=1)).isoformat())

    def test_created_expiration_respects_flask_checks(self):
        _, code = create_short_link(self.user_id, "https://example.com", expires_at=self.future.isoformat())
        with app.test_client() as client:
            self.assertEqual(client.get("/" + code).status_code, 302)
            with patch("app.datetime", wraps=datetime) as clock:
                clock.now.return_value = self.future
                self.assertEqual(client.get("/" + code).status_code, 410)

    def test_concurrent_duplicate_requests_keep_winning_expiration(self):
        barrier = Barrier(2)
        expirations = [self.future.isoformat(), (self.future + timedelta(days=1)).isoformat()]

        def create(expiration):
            barrier.wait(timeout=10)
            return create_short_link(self.user_id, "https://example.com", expires_at=expiration)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(create, expirations))
        self.assertEqual(results[0], results[1])
        self.assertIn(database.get_link_by_short_code(results[0][1])[6], expirations)
        connection = database.get_connection()
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM links").fetchone()[0], 1)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
