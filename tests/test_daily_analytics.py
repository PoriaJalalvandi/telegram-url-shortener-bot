import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import database


class TestDailyClickAnalytics(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        patcher = patch.object(database, "DATABASE", os.path.join(temporary.name, "test.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        database.create_tables()
        database.create_user(123, "tester", "Test")
        user_id = database.get_user_by_telegram_id(123)[0]
        self.link_id = database.create_link(user_id, "https://example.com", "abc123")
        self.other_id = database.create_link(user_id, "https://other.example", "other")
        self.connection = database.get_connection()
        self.addCleanup(self.connection.close)

    def event(self, timestamp, is_bot=None, link_id=None):
        self.connection.execute(
            "INSERT INTO click_events (link_id, clicked_at, is_bot) VALUES (?, ?, ?)",
            (self.link_id if link_id is None else link_id, timestamp, is_bot),
        )
        self.connection.commit()

    def test_multiple_clicks_and_classification_totals(self):
        for is_bot in (1, 1, 0, None, None):
            self.event("2026-11-15T12:00:00+00:00", is_bot)
        rows = database.get_daily_click_counts()
        self.assertEqual(rows, [("2026-11-15", 5, 2, 1, 2)])
        for _, total, bots, non_bots, unknown in rows:
            self.assertEqual(total, bots + non_bots + unknown)

    def test_days_are_chronological_and_empty_days_omitted(self):
        self.event("2026-11-17T12:00:00+00:00", 1)
        self.event("2026-11-15T12:00:00+00:00", 0)
        self.assertEqual(database.get_daily_click_counts(), [
            ("2026-11-15", 1, 0, 1, 0), ("2026-11-17", 1, 1, 0, 0),
        ])

    def test_utc_midnight_and_offset_grouping(self):
        for timestamp in (
            "2026-11-15T23:59:59+00:00",
            "2026-11-16T00:30:00+01:00",  # Previous UTC day
            "2026-11-16T00:00:00Z",
            "2026-11-15T23:30:00-01:00",  # Next UTC day
        ):
            self.event(timestamp)
        self.assertEqual(database.get_daily_click_counts(), [
            ("2026-11-15", 2, 0, 0, 2), ("2026-11-16", 2, 0, 0, 2),
        ])

    def test_link_filter_and_parameter_binding(self):
        self.event("2026-11-15T12:00:00+00:00", 1)
        self.event("2026-11-15T12:00:00+00:00", 0, self.other_id)
        self.assertEqual(database.get_daily_click_counts(link_id=self.link_id), [("2026-11-15", 1, 1, 0, 0)])
        self.assertEqual(database.get_daily_click_counts(link_id=self.other_id), [("2026-11-15", 1, 0, 1, 0)])
        self.assertEqual(database.get_daily_click_counts(link_id="1 OR 1=1"), [])

    def test_aware_bounds_are_utc_inclusive_start_exclusive_end(self):
        for hour in (11, 12, 13, 14):
            self.event(f"2026-11-15T{hour}:00:00+00:00", 0)
        offset = timezone(timedelta(hours=3, minutes=30))
        start = datetime(2026, 11, 15, 15, 30, tzinfo=offset)  # 12 UTC
        end = datetime(2026, 11, 15, 17, 30, tzinfo=offset)  # 14 UTC
        self.assertEqual(database.get_daily_click_counts(start=start, end=end, link_id=self.link_id), [("2026-11-15", 2, 0, 2, 0)])
        self.assertEqual(database.get_daily_click_counts(start=start), [("2026-11-15", 3, 0, 3, 0)])
        self.assertEqual(database.get_daily_click_counts(end=end), [("2026-11-15", 3, 0, 3, 0)])

    def test_invalid_bounds_are_rejected(self):
        for value in (datetime(2026, 11, 15), "2026-11-15", 123):
            for key in ("start", "end"):
                with self.subTest(value=value, key=key), self.assertRaises(ValueError):
                    database.get_daily_click_counts(**{key: value})
        start = datetime(2026, 11, 15, tzinfo=timezone.utc)
        for end in (start, start - timedelta(days=1)):
            with self.assertRaises(ValueError):
                database.get_daily_click_counts(start=start, end=end)

    def test_empty_results_and_invalid_stored_dates(self):
        self.assertEqual(database.get_daily_click_counts(), [])
        self.event("invalid")
        self.assertEqual(database.get_daily_click_counts(), [])
        self.event("2026-11-15T12:00:00+00:00")
        self.assertEqual(database.get_daily_click_counts(link_id=999999), [])
        self.assertEqual(database.get_daily_click_counts(start=datetime(2027, 1, 1, tzinfo=timezone.utc)), [])

    def test_historical_counters_are_not_events(self):
        self.connection.execute("UPDATE links SET click_count = 100 WHERE id = ?", (self.link_id,))
        self.connection.commit()
        self.assertEqual(database.get_daily_click_counts(), [])
        with patch("database.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
            database.record_link_click(self.link_id, is_bot=0)
        self.assertEqual(database.get_daily_click_counts(), [("2026-11-15", 1, 0, 1, 0)])
        self.assertEqual(database.get_link_by_short_code("abc123")[8], 101)


if __name__ == "__main__":
    unittest.main()
