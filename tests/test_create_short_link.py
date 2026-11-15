import unittest
from unittest.mock import ANY, patch

from utils import create_short_link


class TestCreateShortLink(unittest.TestCase):

    @patch("utils.create_link")
    @patch("utils.get_duplicate_link")
    def test_existing_link(
        self,
        mock_duplicate,
        mock_create,
    ):
        # Simulate an existing link
        mock_duplicate.return_value = (
            1,
            1,
            "https://example.com",
            "abc123",
        )

        link_id, short_code = create_short_link(
            user_id=1,
            original_url="https://EXAMPLE.com/",
        )

        self.assertEqual(link_id, 1)
        self.assertEqual(short_code, "abc123")

        # No new database record should be created
        mock_create.assert_not_called()

        mock_duplicate.assert_called_once_with(
            user_id=1,
            normalized_url="https://example.com/",
            connection=ANY,
        )

    @patch("utils.create_link", return_value=2)
    @patch("utils.generate_unique_short_code", return_value="xyz789")
    @patch("utils.get_duplicate_link", return_value=None)
    def test_new_link(
        self,
        mock_duplicate,
        mock_generate,
        mock_create,
    ):
        link_id, short_code = create_short_link(
            user_id=1,
            original_url="https://example.com",
        )

        self.assertEqual(link_id, 2)
        self.assertEqual(short_code, "xyz789")

        mock_create.assert_called_once_with(
            user_id=1,
            original_url="https://example.com",
            normalized_url="https://example.com/",
            short_code="xyz789",
            expires_at=None,
            connection=ANY,
        )

    @patch("utils.create_link", return_value=3)
    @patch("utils.generate_unique_short_code", return_value="utm123")
    @patch("utils.get_duplicate_link", return_value=None)
    def test_utm_preservation(
        self,
        mock_duplicate,
        mock_generate,
        mock_create,
    ):
        url = (
            "https://example.com/"
            "?utm_source=telegram&utm_campaign=goldflex"
        )

        link_id, short_code = create_short_link(
            user_id=1,
            original_url=url,
        )

        self.assertEqual(link_id, 3)
        self.assertEqual(short_code, "utm123")

        mock_create.assert_called_once_with(
            user_id=1,
            original_url=url,
            normalized_url=url,
            short_code="utm123",
            expires_at=None,
            connection=ANY,
        )


if __name__ == "__main__":
    unittest.main()
