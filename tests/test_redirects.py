import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import patch

import database
from app import app


class RedirectTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        patcher = patch.object(database, "DATABASE", os.path.join(temporary.name, "test.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        database.create_tables()
        database.create_user(12345, "tester", "Test")
        self.user_id = database.get_user_by_telegram_id(12345)[0]
        self.client = app.test_client()
        self.now = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
        clock = patch("app.datetime", wraps=datetime)
        mock_clock = clock.start()
        mock_clock.now.return_value = self.now
        self.addCleanup(clock.stop)

    def create_link(self, expires_at=None, active=True):
        database.create_link(
            self.user_id, "https://example.com/page?source=test", "AbC123",
            expires_at=expires_at,
        )
        if not active:
            connection = database.get_connection()
            try:
                connection.execute("UPDATE links SET is_active = 0")
                connection.commit()
            finally:
                connection.close()

    def assert_gone(self):
        response = self.client.get("/abc123")
        self.assertEqual(response.status_code, 410)
        self.assertNotIn("Location", response.headers)


class TestRedirects(RedirectTestCase):

    def test_active_link_without_expiration_redirects(self):
        self.create_link()
        response = self.client.get("/AbC123")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "https://example.com/page?source=test")

    def test_future_expiration_redirects(self):
        self.create_link((self.now + timedelta(seconds=1)).isoformat())
        self.assertEqual(self.client.get("/AbC123").status_code, 302)

    def test_nonexistent_link_returns_404(self):
        response = self.client.get("/missing")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("Location", response.headers)

    def test_expired_link_returns_410(self):
        self.create_link((self.now - timedelta(seconds=1)).isoformat())
        self.assert_gone()

    def test_exact_expiration_returns_410(self):
        self.create_link(self.now.isoformat())
        self.assert_gone()

    def test_inactive_link_without_expiration_returns_410(self):
        self.create_link(active=False)
        self.assert_gone()

    def test_inactive_unexpired_link_returns_410(self):
        self.create_link((self.now + timedelta(days=1)).isoformat(), active=False)
        self.assert_gone()

    def test_invalid_or_naive_expiration_returns_410(self):
        for value in ("", "not-a-date", "2026-99-99", "2099-01-01T00:00:00", b"invalid"):
            with self.subTest(value=value):
                connection = database.get_connection()
                try:
                    connection.execute("DELETE FROM links")
                    connection.commit()
                finally:
                    connection.close()
                self.create_link(value)
                self.assert_gone()

    def test_offsets_are_compared_as_utc(self):
        # 15:00 +03:30 is 11:30 UTC, before the fixed 12:00 UTC clock.
        self.create_link("2026-11-15T15:00:00+03:30")
        self.assert_gone()

    def test_future_offset_expiration_redirects(self):
        self.create_link("2026-11-15T09:00:00-04:00")
        self.assertEqual(self.client.get("/abc123").status_code, 302)

    def test_lookup_is_case_insensitive(self):
        self.create_link()
        for code in ("abc123", "ABC123", "aBc123"):
            with self.subTest(code=code):
                self.assertEqual(self.client.get("/" + code).status_code, 302)
                self.assertEqual(database.get_link_by_short_code(code)[3], "AbC123")


class TestClickTracking(RedirectTestCase):
    def events(self):
        connection = database.get_connection()
        try:
            return connection.execute("SELECT * FROM click_events ORDER BY id").fetchall()
        finally:
            connection.close()

    def stats(self):
        link = database.get_link_by_short_code("abc123")
        return link[8], link[9]

    def test_first_click_stores_current_utc_timestamp(self):
        self.create_link()
        self.assertEqual(self.stats(), (0, None))
        before = datetime.now(timezone.utc)
        self.assertEqual(self.client.get("/abc123").status_code, 302)
        after = datetime.now(timezone.utc)
        count, timestamp = self.stats()
        self.assertEqual(count, 1)
        parsed = datetime.fromisoformat(timestamp)
        self.assertEqual(parsed.utcoffset(), timedelta(0))
        self.assertGreaterEqual(parsed, before)
        self.assertLessEqual(parsed, after)
        events = self.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][1], database.get_link_by_short_code("abc123")[0])
        self.assertEqual(events[0][2], timestamp)
        self.assertEqual(events[0][3:], (None, None, None, "Unknown", "Unknown") + (None,) * 6)

    def test_multiple_clicks_increment_and_update_timestamp(self):
        self.create_link()
        for index in range(1, 4):
            clicked_at = self.now + timedelta(seconds=index)
            with patch("database.datetime", wraps=datetime) as clock:
                clock.now.return_value = clicked_at
                self.assertEqual(self.client.get("/AbC123").status_code, 302)
            self.assertEqual(self.stats(), (index, clicked_at.isoformat()))
            events = self.events()
            self.assertEqual(len(events), index)
            self.assertEqual(events[-1][2], clicked_at.isoformat())

    def test_concurrent_clicks_have_no_lost_increments(self):
        self.create_link()
        workers = 8
        barrier = Barrier(workers)

        def open_link():
            with app.test_client() as client:
                barrier.wait(timeout=10)
                return client.get("/ABC123").status_code

        with ThreadPoolExecutor(max_workers=workers) as executor:
            responses = list(executor.map(lambda _: open_link(), range(workers)))
        self.assertEqual(responses, [302] * workers)
        self.assertEqual(self.stats()[0], workers)
        events = self.events()
        self.assertEqual(len(events), workers)
        self.assertEqual(len({event[0] for event in events}), workers)
        self.assertIn(self.stats()[1], {event[2] for event in events})

    def test_ineligible_links_do_not_record_clicks(self):
        for expiration, active in (
            ((self.now - timedelta(seconds=1)).isoformat(), True),
            (None, False),
            ("invalid", True),
            ("2099-01-01T00:00:00", True),
        ):
            with self.subTest(expiration=expiration, active=active):
                connection = database.get_connection()
                try:
                    connection.execute("DELETE FROM links")
                    connection.commit()
                finally:
                    connection.close()
                self.create_link(expiration, active)
                self.assert_gone()
                self.assertEqual(self.stats(), (0, None))
                self.assertEqual(self.events(), [])

    def test_missing_code_does_not_increment_other_link(self):
        self.create_link()
        self.assertEqual(self.client.get("/missing").status_code, 404)
        self.assertEqual(self.stats(), (0, None))
        self.assertEqual(self.events(), [])

    def test_failed_update_rolls_back_and_returns_503(self):
        self.create_link()
        connection = database.get_connection()
        try:
            # Fail after the UPDATE to verify that both fields roll back together.
            connection.execute("""
                CREATE TRIGGER reject_click AFTER UPDATE ON links
                BEGIN
                    SELECT RAISE(ABORT, 'test write failure');
                END
            """)
            connection.commit()
        finally:
            connection.close()
        response = self.client.get("/abc123")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("Location", response.headers)
        self.assertEqual(self.stats(), (0, None))
        self.assertEqual(self.events(), [])

    def test_locked_database_returns_503_without_redirect(self):
        self.create_link()
        connection = database.get_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            # Keep the test fast instead of waiting SQLite's default five seconds.
            with patch("database.get_connection", side_effect=lambda: sqlite3.connect(
                database.DATABASE, timeout=0
            )):
                response = self.client.get("/abc123")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("Location", response.headers)
        finally:
            connection.rollback()
            connection.close()
        self.assertEqual(self.stats(), (0, None))
        self.assertEqual(self.events(), [])
        self.assertEqual(self.client.get("/abc123").status_code, 302)
        self.assertEqual(self.stats()[0], 1)
        self.assertEqual(len(self.events()), 1)

    def test_failed_event_insert_rolls_back_and_recovers(self):
        self.create_link()
        self.assertEqual(self.client.get("/abc123").status_code, 302)
        before_stats = self.stats()
        before_events = self.events()
        connection = database.get_connection()
        try:
            connection.execute("""
                CREATE TRIGGER reject_event AFTER INSERT ON click_events
                BEGIN
                    SELECT RAISE(ABORT, 'test event failure');
                END
            """)
            connection.commit()
        finally:
            connection.close()

        response = self.client.get("/abc123")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("Location", response.headers)
        self.assertEqual(self.stats(), before_stats)
        self.assertEqual(self.events(), before_events)

        connection = database.get_connection()
        try:
            connection.execute("DROP TRIGGER reject_event")
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(self.client.get("/abc123").status_code, 302)
        self.assertEqual(self.stats()[0], 2)
        self.assertEqual(len(self.events()), 2)
        self.assertEqual(self.events()[-1][2], self.stats()[1])

    def test_missing_database_target_does_not_insert_event(self):
        with self.assertRaises(sqlite3.DatabaseError):
            database.record_link_click(999999)
        self.assertEqual(self.events(), [])

    def test_classification_persistence_without_raw_headers(self):
        self.create_link()
        user_agents = (
            ("Mozilla/5.0 (Linux; Android 14; Pixel) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36", "Mobile", "Android", 0),
            ("TelegramBot (like TwitterBot)", "Unknown", "Unknown", 1),
            ("unrecognized-private-marker", "Unknown", "Unknown", None),
            ("", "Unknown", "Unknown", None),
        )
        for ua, device, os_name, is_bot in user_agents:
            with self.subTest(ua=ua):
                response = self.client.get("/abc123", headers={"User-Agent": ua})
                self.assertEqual(response.status_code, 302)
                event = self.events()[-1]
                self.assertEqual(event[4], is_bot)
                self.assertEqual(event[6:8], (device, os_name))
                self.assertEqual((event[3], event[5]) + event[8:], (None,) * 8)
                self.assertEqual(event[2], self.stats()[1])
                if ua:
                    self.assertNotIn(ua, event)
        self.assertEqual(self.stats()[0], len(user_agents))

    def test_referrer_origin_persistence(self):
        self.create_link()
        for value, expected in (
            ("https://SOURCE.example:443/private?token=secret#fragment", "https://source.example"),
            ("http://SOURCE.example:8080/private", "http://source.example:8080"),
            (None, None), ("malformed", None), ("ftp://example.com", None),
            ("https://example.com/" + "x" * 2048, None),
        ):
            with self.subTest(value=value):
                headers = {} if value is None else {"Referer": value}
                self.assertEqual(self.client.get("/abc123", headers=headers).status_code, 302)
                event = self.events()[-1]
                self.assertEqual(event[8], expected)
                self.assertEqual(event[2], self.stats()[1])
                if value:
                    self.assertNotIn(value, event)
        self.assertEqual(self.stats()[0], 6)

    def test_referrer_does_not_create_events_for_unavailable_links(self):
        headers = {"Referer": "https://source.example/private?secret=value"}
        self.assertEqual(self.client.get("/missing", headers=headers).status_code, 404)
        for expiration, active in (
            (None, False),
            ((self.now - timedelta(seconds=1)).isoformat(), True),
            ("invalid", True),
        ):
            with self.subTest(expiration=expiration, active=active):
                connection = database.get_connection()
                try:
                    connection.execute("DELETE FROM links")
                    connection.commit()
                finally:
                    connection.close()
                self.create_link(expiration, active)
                self.assertEqual(self.client.get("/abc123", headers=headers).status_code, 410)
                self.assertEqual(self.events(), [])
                self.assertEqual(self.stats(), (0, None))


if __name__ == "__main__":
    unittest.main()
