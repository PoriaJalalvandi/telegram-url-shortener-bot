import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from database import create_tables
from utils import create_short_link
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier


class TestShortLinkIntegration(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

        self.db_path = os.path.join(
            self.temp_dir.name,
            "test.db"
        )

        # Redirect database connections to temporary SQLite
        self.db_patcher = patch(
            "database.get_connection",
            side_effect=self.get_test_connection
        )

        self.utils_patcher = patch(
            "utils.get_connection",
            side_effect=self.get_test_connection
        )

        self.db_patcher.start()
        self.utils_patcher.start()

        # Create tables using the real application schema
        create_tables()

        # Create a test user
        connection = self.get_test_connection()

        connection.execute(
            """
            INSERT INTO users (
                telegram_user_id,
                username,
                first_name,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                123456789,
                "test_user",
                "Test",
                "2026-11-01T10:00:00+00:00"
            )
        )

        connection.commit()
        connection.close()

    def get_test_connection(self):
        connection = sqlite3.connect(self.db_path)

        connection.execute("PRAGMA foreign_keys = ON")

        return connection

    def tearDown(self):
        self.utils_patcher.stop()
        self.db_patcher.stop()
        self.temp_dir.cleanup()

    def test_duplicate_creates_only_one_record(self):

        # First request
        first_id, first_code = create_short_link(
            user_id=1,
            original_url="https://EXAMPLE.com"
        )

        # Second request: same URL in normalized form
        second_id, second_code = create_short_link(
            user_id=1,
            original_url="https://example.com/"
        )

        self.assertEqual(first_id, second_id)
        self.assertEqual(first_code, second_code)

        connection = self.get_test_connection()

        count = connection.execute(
            "SELECT COUNT(*) FROM links"
        ).fetchone()[0]

        connection.close()

        self.assertEqual(count, 1)

    def test_concurrent_duplicate_requests(self):

        url = "https://example.com/concurrent-test"

        barrier = Barrier(2)

        def create_link_request():
            barrier.wait()

            return create_short_link(
                user_id=1,
                original_url=url,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:

            futures = [
                executor.submit(create_link_request)
                for _ in range(2)
            ]

            results = [
                future.result()
                for future in futures
            ]

        # Both requests must return the same link
        self.assertEqual(results[0], results[1])

        # Only one record must exist in SQLite
        connection = self.get_test_connection()

        try:
            count = connection.execute(
                """
                SELECT COUNT(*)
                FROM links
                WHERE normalized_url = ?
                """,
                (url,)
            ).fetchone()[0]

        finally:
            connection.close()

        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()