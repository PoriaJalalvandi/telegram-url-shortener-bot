import unittest

from referrer import MAX_REFERRER_LENGTH, normalize_referrer


class TestReferrerNormalization(unittest.TestCase):
    def test_valid_origins(self):
        cases = (
            ("http://example.com/page", "http://example.com"),
            ("https://example.com/page?secret=value#private", "https://example.com"),
            ("HTTPS://ExAmPlE.COM/", "https://example.com"),
            ("http://example.com:80/", "http://example.com"),
            ("https://example.com:443/", "https://example.com"),
            ("https://example.com:8443/path", "https://example.com:8443"),
            ("http://example.com:8080/path", "http://example.com:8080"),
            ("http://example.com:443/", "http://example.com:443"),
            ("https://[2001:db8::1]:8443/private", "https://[2001:db8::1]:8443"),
            ("https://bücher.example/path", "https://xn--bcher-kva.example"),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(normalize_referrer(value), expected)

    def test_absent_malformed_or_unsupported_values(self):
        for value in (
            None, "", "not-a-url", "/relative", "//example.com/path", "https://",
            "ftp://example.com", "javascript:alert(1)", "file:///private",
            "https://example.com:99999/", "https://example.com:0/",
            "https://example.com:/", "https://[invalid]/", "https://bad_host/path",
            "https://user:secret@example.com/private", " https://example.com",
            "https://example.com/path with spaces", "https://example.com/\nprivate",
            "https://example.com\\@other.example", "https://example.com/%ZZ",
        ):
            with self.subTest(value=value):
                self.assertIsNone(normalize_referrer(value))

    def test_length_limit(self):
        prefix = "https://example.com/"
        at_limit = prefix + "a" * (MAX_REFERRER_LENGTH - len(prefix))
        self.assertEqual(normalize_referrer(at_limit), "https://example.com")
        self.assertIsNone(normalize_referrer(at_limit + "a"))


if __name__ == "__main__":
    unittest.main()
