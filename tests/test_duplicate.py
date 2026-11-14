import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from database import get_duplicate_link, is_alias_available


class TestDuplicateDetection(unittest.TestCase):

    def setUp(self):
        # Create a temporary SQLite database
        self.temp_db = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(
            self.temp_db.name,
            "test.db"
        )

        # Create the links table
        connection = sqlite3.connect(self.db_path)

        connection.execute("""
            CREATE TABLE links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                original_url TEXT NOT NULL,
                short_code TEXT UNIQUE NOT NULL,
                is_custom INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                click_count INTEGER NOT NULL DEFAULT 0,
                last_click_at TEXT,
                normalized_url TEXT
            )
        """)

        connection.execute("""
            INSERT INTO links (
                user_id,
                original_url,
                normalized_url,
                short_code,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            1,
            "https://EXAMPLE.com",
            "https://example.com/",
            "abc123",
            "2026-11-01T10:00:00+00:00"
        ))

        connection.commit()
        connection.close()

        # Redirect database connections to the temporary DB
        self.patcher = patch(
            "database.get_connection",
            side_effect=lambda: sqlite3.connect(self.db_path)
        )
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.temp_db.cleanup()

    def test_same_user_same_url(self):
        link = get_duplicate_link(
            user_id=1,
            normalized_url="https://example.com/"
        )

        self.assertIsNotNone(link)
        self.assertEqual(link[3], "abc123")

    def test_different_user(self):
        link = get_duplicate_link(
            user_id=2,
            normalized_url="https://example.com/"
        )

        self.assertIsNone(link)

    def test_different_url(self):
        link = get_duplicate_link(
            user_id=1,
            normalized_url="https://google.com/"
        )

        self.assertIsNone(link)

    def test_inactive_link(self):

        connection = sqlite3.connect(self.db_path)

        connection.execute(
            """
            UPDATE links
            SET is_active = 0
            WHERE short_code = ?
            """,
            ("abc123",)
        )

        connection.commit()
        connection.close()

        link = get_duplicate_link(
            user_id=1,
            normalized_url="https://example.com/"
        )

        self.assertIsNone(link)

    def test_expired_link(self):

        connection = sqlite3.connect(self.db_path)

        connection.execute(
            """
            UPDATE links
            SET expires_at = ?
            WHERE short_code = ?
            """,
            (
                "2020-01-01T00:00:00+00:00",
                "abc123"
            )
        )

        connection.commit()
        connection.close()

        link = get_duplicate_link(
            user_id=1,
            normalized_url="https://example.com/"
        )

        self.assertIsNone(link)

    def test_alias_available(self):

        available = is_alias_available("newalias")

        self.assertTrue(available)

    def test_alias_taken(self):

        available = is_alias_available("abc123")

        self.assertFalse(available)

    def test_alias_case_insensitive(self):

        available = is_alias_available("ABC123")

        self.assertFalse(available)
        


if __name__ == "__main__":
    unittest.main()