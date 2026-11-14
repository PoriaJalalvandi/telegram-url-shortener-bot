import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from database import create_tables
from utils import create_short_link, create_custom_link
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

    def test_case_insensitive_short_code_uniqueness(self):

        connection = self.get_test_connection()

        try:
            connection.execute(
                """
                INSERT INTO links (
                    user_id,
                    original_url,
                    normalized_url,
                    short_code,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    1,
                    "https://example.com/first",
                    "https://example.com/first",
                    "GoldFlex",
                    "2026-11-01T10:00:00+00:00"
                )
            )

            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO links (
                        user_id,
                        original_url,
                        normalized_url,
                        short_code,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        1,
                        "https://example.com/second",
                        "https://example.com/second",
                        "goldflex",
                        "2026-11-01T10:00:00+00:00"
                    )
                )

        finally:
            connection.rollback()
            connection.close()


    def test_create_custom_alias(self):

        link_id, short_code = create_custom_link(
            user_id=1,
            original_url="https://example.com/gold",
            alias="GoldFlex"
        )

        self.assertEqual(short_code, "goldflex")

        connection = self.get_test_connection()

        try:
            link = connection.execute(
                """
                SELECT short_code, is_custom, normalized_url
                FROM links
                WHERE id = ?
                """,
                (link_id,)
            ).fetchone()

        finally:
            connection.close()

        self.assertEqual(link[0], "goldflex")
        self.assertEqual(link[1], 1)
        self.assertEqual(
            link[2],
            "https://example.com/gold"
        )


    def test_duplicate_custom_alias(self):

        create_custom_link(
            user_id=1,
            original_url="https://example.com/first",
            alias="GoldFlex"
        )

        with self.assertRaisesRegex(
            ValueError,
            "Alias already taken"
        ):
            create_custom_link(
                user_id=1,
                original_url="https://example.com/second",
                alias="goldflex"
            )

        connection = self.get_test_connection()

        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM links"
            ).fetchone()[0]

        finally:
            connection.close()

        self.assertEqual(count, 1)


    def test_invalid_custom_alias(self):

        with self.assertRaisesRegex(
            ValueError,
            "Invalid alias"
        ):
            create_custom_link(
                user_id=1,
                original_url="https://example.com",
                alias="my alias"
            )

        connection = self.get_test_connection()

        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM links"
            ).fetchone()[0]

        finally:
            connection.close()

        self.assertEqual(count, 0)


    def test_custom_alias_foreign_key_error(self):

        with self.assertRaises(sqlite3.IntegrityError) as context:
            create_custom_link(
                user_id=999999,
                original_url="https://example.com",
                alias="newalias"
            )

        error_name = getattr(
            context.exception,
            "sqlite_errorname",
            None
        )

        self.assertEqual(
            error_name,
            "SQLITE_CONSTRAINT_FOREIGNKEY"
        )


    def test_concurrent_custom_alias(self):

        barrier = Barrier(2)

        def create_alias_request(user_id, alias):
            barrier.wait()

            try:
                return create_custom_link(
                    user_id=user_id,
                    original_url="https://example.com/gold",
                    alias=alias,
                )

            except ValueError as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=2) as executor:

            futures = [
                executor.submit(
                    create_alias_request, 1, "GoldFlex"
                ),
                executor.submit(
                    create_alias_request, 1, "goldflex"
                ),
            ]

            results = [
                future.result()
                for future in futures
            ]

        # Exactly one request must succeed
        successful = [
            result
            for result in results
            if isinstance(result, tuple)
        ]

        # Exactly one request must fail
        failed = [
            result
            for result in results
            if result == "Alias already taken"
        ]

        self.assertEqual(len(successful), 1)
        self.assertEqual(len(failed), 1)

        connection = self.get_test_connection()

        try:
            count = connection.execute(
                """
                SELECT COUNT(*)
                FROM links
                WHERE LOWER(short_code) = ?
                """,
                ("goldflex",)
            ).fetchone()[0]

        finally:
            connection.close()

        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()