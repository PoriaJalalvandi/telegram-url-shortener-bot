import unittest

from validators import validate_url


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


if __name__ == "__main__":
    unittest.main()