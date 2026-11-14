import sqlite3
import unittest
from unittest.mock import patch

from utils import create_short_link


class TestCollision(unittest.TestCase):

    # Test 1: Collision followed by success

    @patch("utils.create_link")
    @patch("utils.generate_unique_short_code")
    def test_collision_retry(self, mock_generate, mock_create):

        mock_generate.side_effect = [
            "abc123",
            "xyz789",
        ]

        mock_create.side_effect = [
            sqlite3.IntegrityError(
                "UNIQUE constraint failed: links.short_code"
            ),
            2,
        ]

        link_id, short_code = create_short_link(
            user_id=1,
            original_url="https://www.python.org",
        )

        self.assertEqual(link_id, 2)
        self.assertEqual(short_code, "xyz789")
        self.assertEqual(mock_create.call_count, 2)

    # Test 2: All attempts fail

    @patch("utils.create_link")
    @patch("utils.generate_unique_short_code")
    def test_max_attempts(self, mock_generate, mock_create):

        mock_generate.return_value = "abc123"

        mock_create.side_effect = sqlite3.IntegrityError(
            "UNIQUE constraint failed: links.short_code"
        )

        with self.assertRaises(RuntimeError):

            create_short_link(
                user_id=1,
                original_url="https://www.python.org",
                max_attempts=5,
            )

        self.assertEqual(mock_create.call_count, 5)

    # Test 3: Unrelated database error

    @patch("utils.create_link")
    @patch("utils.generate_unique_short_code")
    def test_non_collision_error(self, mock_generate, mock_create):

        mock_generate.return_value = "abc123"

        mock_create.side_effect = sqlite3.IntegrityError(
            "FOREIGN KEY constraint failed"
        )

        with self.assertRaises(sqlite3.IntegrityError):

            create_short_link(
                user_id=999,
                original_url="https://www.python.org",
            )

        self.assertEqual(mock_create.call_count, 1)


if __name__ == "__main__":
    unittest.main()