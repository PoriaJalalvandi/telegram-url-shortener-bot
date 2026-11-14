import unittest

from validators import normalize_url


class TestURLNormalization(unittest.TestCase):

    def test_hostname_normalization(self):

        self.assertEqual(
            normalize_url("https://EXAMPLE.com"),
            "https://example.com/",
        )

    def test_default_port(self):

        self.assertEqual(
            normalize_url("https://example.com:443"),
            "https://example.com/",
        )

    def test_non_default_port(self):

        self.assertEqual(
            normalize_url("https://example.com:8080"),
            "https://example.com:8080/",
        )

    def test_utm_preservation(self):

        self.assertEqual(
            normalize_url(
                "https://example.com/?utm_source=telegram"
            ),
            "https://example.com/?utm_source=telegram",
        )

    def test_path_case_preservation(self):

        self.assertEqual(
            normalize_url("https://example.com/Product"),
            "https://example.com/Product",
        )

    def test_invalid_url(self):

        with self.assertRaises(ValueError):
            normalize_url("not-a-url")


if __name__ == "__main__":
    unittest.main()