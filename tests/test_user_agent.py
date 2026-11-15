import unittest

from user_agent import classify_user_agent


class TestUserAgentClassification(unittest.TestCase):
    def test_browser_devices_and_operating_systems(self):
        cases = (
            ("Linux; Android 14; Pixel", "Chrome/120.0 Mobile Safari/537.36", "Mobile", "Android"),
            ("iPhone; CPU iPhone OS 17_0 like Mac OS X", "Version/17.0 Mobile/15E148 Safari/604.1", "Mobile", "iOS"),
            ("Windows NT 10.0; Win64; x64", "Chrome/120.0 Safari/537.36", "Desktop", "Windows"),
            ("Macintosh; Intel Mac OS X 14_0", "Version/17.0 Safari/605.1", "Desktop", "macOS"),
            ("X11; Linux x86_64", "Firefox/120.0", "Desktop", "Linux"),
            ("iPad; CPU OS 17_0 like Mac OS X", "Version/17.0 Mobile/15E148 Safari/604.1", "Tablet", "iOS"),
            ("Linux; Android 13; SM-X200", "Chrome/120.0 Safari/537.36", "Tablet", "Android"),
            ("Macintosh; Intel Mac OS X 10_15", "Version/17.0 Mobile/15E148 Safari/604.1", "Tablet", "iOS"),
        )
        for platform, browser, device, os_name in cases:
            with self.subTest(platform=platform):
                ua = f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 {browser}"
                self.assertEqual(classify_user_agent(ua), {
                    "device_type": device, "operating_system": os_name, "is_bot": 0,
                })

    def test_recognized_bots_and_automated_clients(self):
        for ua in (
            "TelegramBot (like TwitterBot)", "Googlebot/2.1", "bingbot/2.0",
            "facebookexternalhit/1.1", "Slackbot-LinkExpanding 1.0", "Discordbot/2.0",
            "Twitterbot/1.0", "WhatsApp/2.0", "curl/8.0", "python-requests/2.32",
            "Mozilla/5.0 (Windows NT 10.0) HeadlessChrome/120.0 Safari/537.36",
        ):
            with self.subTest(ua=ua):
                self.assertEqual(classify_user_agent(ua)["is_bot"], 1)

    def test_missing_or_unrecognized_remain_uncertain(self):
        for ua in (None, "", "   ", "custom-client/1.0", "Mozilla/5.0", "Windows NT 10.0"):
            with self.subTest(ua=ua):
                result = classify_user_agent(ua)
                self.assertIsNone(result["is_bot"])
                self.assertEqual(result["device_type"], "Unknown")

    def test_unknown_has_only_normalized_fields(self):
        self.assertEqual(classify_user_agent("unrecognized-client"), {
            "device_type": "Unknown", "operating_system": "Unknown", "is_bot": None,
        })


if __name__ == "__main__":
    unittest.main()
