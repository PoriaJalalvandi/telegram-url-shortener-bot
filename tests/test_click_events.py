import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import database


class TestClickEventsSchema(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        patcher = patch.object(database, "DATABASE", os.path.join(temporary.name, "test.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        database.create_tables()
        database.create_user(123, "tester", "Test")
        self.link_id = database.create_link(
            database.get_user_by_telegram_id(123)[0],
            "https://example.com/?utm_campaign=launch", "abc123",
        )
        self.connection = database.get_connection()
        self.addCleanup(self.connection.close)

    def insert_event(self, link_id=None):
        return self.connection.execute(
            "INSERT INTO click_events (link_id, clicked_at) VALUES (?, ?)",
            (self.link_id if link_id is None else link_id, "2026-11-15T12:00:00+00:00"),
        ).lastrowid

    def test_table_columns_and_required_fields(self):
        columns = {
            row[1]: row for row in self.connection.execute("PRAGMA table_info(click_events)")
        }
        self.assertEqual(set(columns), {
            "id", "link_id", "clicked_at", "visitor_id", "is_bot", "country_code",
            "device_type", "operating_system", "referrer", "utm_source", "utm_medium",
            "utm_campaign", "utm_term", "utm_content",
        })
        self.assertEqual(columns["id"][5], 1)  # Primary key
        self.assertEqual(columns["link_id"][3], 1)
        self.assertEqual(columns["clicked_at"][3], 1)
        for name in set(columns) - {"id", "link_id", "clicked_at"}:
            self.assertEqual(columns[name][3], 0)
        for sql in (
            "INSERT INTO click_events (clicked_at) VALUES ('2026-11-15T12:00:00+00:00')",
            "INSERT INTO click_events (link_id) VALUES (1)",
        ):
            with self.assertRaises(sqlite3.IntegrityError):
                self.connection.execute(sql)

    def test_optional_metadata_defaults_to_null(self):
        event_id = self.insert_event()
        event = self.connection.execute(
            "SELECT * FROM click_events WHERE id = ?", (event_id,)
        ).fetchone()
        self.assertEqual(event[1], self.link_id)
        self.assertEqual(event[3:], (None,) * 11)

    def test_foreign_key_enforced(self):
        self.assertEqual(self.connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        foreign_key = self.connection.execute("PRAGMA foreign_key_list(click_events)").fetchone()
        self.assertEqual(foreign_key[2:5], ("links", "link_id", "id"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_event(link_id=999999)
        self.insert_event()
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("DELETE FROM links WHERE id = ?", (self.link_id,))

    def test_bot_classification_accepts_only_null_zero_or_one(self):
        event_id = self.insert_event()
        for value in (None, 0, 1):
            self.connection.execute("UPDATE click_events SET is_bot = ? WHERE id = ?", (value, event_id))
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("UPDATE click_events SET is_bot = 2")

    def test_analytics_indexes(self):
        indexes = {
            row[1] for row in self.connection.execute("PRAGMA index_list(click_events)")
        }
        self.assertEqual(indexes, {
            "idx_click_events_link_clicked_at", "idx_click_events_clicked_at",
        })
        for name, expected in (
            ("idx_click_events_link_clicked_at", ["link_id", "clicked_at"]),
            ("idx_click_events_clicked_at", ["clicked_at"]),
        ):
            columns = [row[2] for row in self.connection.execute(f"PRAGMA index_info({name})")]
            self.assertEqual(columns, expected)

    def test_initialization_is_idempotent_and_preserves_events(self):
        event_id = self.insert_event()
        self.connection.commit()
        database.create_tables()
        database.create_tables()
        self.assertEqual(
            self.connection.execute("SELECT id FROM click_events").fetchall(), [(event_id,)]
        )

    def test_existing_database_upgrade_preserves_tables_rows_and_counters(self):
        # Reproduce the existing database before the analytics table was introduced.
        self.connection.execute("DROP TABLE click_events")
        self.connection.commit()
        # Seed historical counters without relying on the new event writer.
        self.connection.execute(
            "UPDATE links SET click_count = 7, last_click_at = ? WHERE id = ?",
            ("2026-11-14T12:00:00+00:00", self.link_id),
        )
        self.connection.commit()
        before_schema = self.connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE name IN ('users', 'links') ORDER BY name"
        ).fetchall()
        before_users = self.connection.execute("SELECT * FROM users").fetchall()
        before_links = self.connection.execute("SELECT * FROM links").fetchall()

        database.create_tables()

        self.assertEqual(self.connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE name IN ('users', 'links') ORDER BY name"
        ).fetchall(), before_schema)
        self.assertEqual(self.connection.execute("SELECT * FROM users").fetchall(), before_users)
        self.assertEqual(self.connection.execute("SELECT * FROM links").fetchall(), before_links)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM click_events").fetchone()[0], 0)

    def test_counter_updates_insert_matching_events(self):
        database.record_link_click(self.link_id)
        database.record_link_click(self.link_id)
        self.assertEqual(database.get_link_by_short_code("abc123")[8], 2)
        events = self.connection.execute("SELECT * FROM click_events ORDER BY id").fetchall()
        self.assertEqual(len(events), 2)
        for event in events:
            self.assertEqual(event[1], self.link_id)
            self.assertEqual(event[3:11], (None,) * 8)
            self.assertEqual(event[11:], ("launch", None, None))
        self.assertEqual(events[-1][2], database.get_link_by_short_code("abc123")[9])


if __name__ == "__main__":
    unittest.main()
