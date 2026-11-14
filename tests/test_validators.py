import unittest

from validators import validate_url, validate_alias


class TestURLValidation(unittest.TestCase):

    def test_valid_urls(self):

        valid_urls = [
            "https://google.com",
            "https://example.com/page",
            "http://example.com",
            "https://example.com:8080/path",
            "https://example.com/?utm_source=telegram",
        ]

        for url in valid_urls:
            with self.subTest(url=url):
                self.assertTrue(validate_url(url))

    def test_invalid_urls(self):

        invalid_urls = [
            "",
            "not-a-url",
            "https://",
            "javascript:alert(1)",
            "file:///etc/passwd",
            "https://localhost",
            "http://127.0.0.1",
            "http://192.168.1.1",
            "https://example.com:99999",
            "https://user:pass@example.com",
            "https://example.com/path with spaces",
        ]

        for url in invalid_urls:
            with self.subTest(url=url):
                self.assertFalse(validate_url(url))

    def test_valid_aliases(self):

        valid_aliases = [
            "goldflex",
            "campaign2026",
            "gold-flex",
            "telegram_ads",
            "GoldFlex",
            "abc",
        ]

        for alias in valid_aliases:
            with self.subTest(alias=alias):
                self.assertTrue(validate_alias(alias))

    def test_invalid_aliases(self):

        invalid_aliases = [
            "",
            "ab",
            "my campaign",
            "gold/flex",
            "gold.flex",
            "کمپین",
            "admin",
            "ADMIN",
            "api",
            "a" * 31,
            12345,
            None,
        ]

        for alias in invalid_aliases:
            with self.subTest(alias=alias):
                self.assertFalse(validate_alias(alias))


if __name__ == "__main__":
    unittest.main()