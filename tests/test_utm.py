import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import database
from app import app
from utm import UTM_FIELDS, extract_utm


class TestUTMExtraction(unittest.TestCase):
    def test_all_fields_and_decoded_case_sensitive_values(self):
        url = ("https://example.com/?utm_source=TeLeGram&utm_medium=Paid+Social"
               "&utm_campaign=Spring%202026&utm_content=%D8%B3%D9%84%D8%A7%D9%85"
               "&utm_term=A%2BB%2520")
        self.assertEqual(extract_utm(url), dict(zip(
            UTM_FIELDS, ("TeLeGram", "Paid Social", "Spring 2026", "سلام", "A+B%20")
        )))

    def test_missing_and_empty(self):
        for url in ("https://example.com", "https://example.com/?utm_source=", "https://example.com/#utm_source=ignored"):
            self.assertEqual(extract_utm(url), dict.fromkeys(UTM_FIELDS))

    def test_duplicates_and_malformed_fields(self):
        self.assertEqual(extract_utm(
            "https://example.com/?utm_source=&utm_source=ignored&utm_medium=First"
            "&utm_medium=Second&utm_campaign=%ZZ&utm_campaign=Valid"
            "&utm_content=%FF&utm_content=Good&utm_term&utm_term=+X+&UTM_SOURCE=ignored"
        ), dict(zip(UTM_FIELDS, (None, "First", "Valid", "Good", " X "))))


class TestUTMIntegration(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        patcher = patch.object(database, "DATABASE", os.path.join(temporary.name, "test.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        database.create_tables()
        database.create_user(123, "tester", "Test")
        self.user_id = database.get_user_by_telegram_id(123)[0]
        self.client = app.test_client()
        self.connection = database.get_connection()
        self.addCleanup(self.connection.close)

    def link(self, code="abc123", query="utm_source=Source&utm_medium=Email&utm_campaign=Launch&utm_content=Hero&utm_term=Term"):
        return database.create_link(self.user_id, "https://example.com/?" + query, code)

    def events(self):
        return self.connection.execute(
            "SELECT utm_source, utm_medium, utm_campaign, utm_content, utm_term FROM click_events"
        ).fetchall()

    def test_destination_attribution_and_unmodified_redirect(self):
        self.link()
        response = self.client.get("/abc123?utm_source=Incoming", headers={"Referer": "https://other.example/?utm_source=Referrer"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], database.get_link_by_short_code("abc123")[2])
        self.assertEqual(self.events(), [("Source", "Email", "Launch", "Hero", "Term")])

    def test_missing_tags_are_null(self):
        self.link(query="other=value")
        self.client.get("/abc123?utm_source=Incoming")
        self.assertEqual(self.events(), [(None,) * 5])

    def test_reports_individual_dimensions_and_combinations(self):
        self.link()
        self.link("other", "utm_source=Source&utm_medium=Social&utm_campaign=Launch")
        self.link("untagged", "")
        for code in ("abc123", "abc123", "other", "untagged"):
            self.assertEqual(self.client.get("/" + code).status_code, 302)
        expected = {
            "utm_source": [(None, 1), ("Source", 3)],
            "utm_medium": [(None, 1), ("Email", 2), ("Social", 1)],
            "utm_campaign": [(None, 1), ("Launch", 3)],
            "utm_content": [(None, 2), ("Hero", 2)],
            "utm_term": [(None, 2), ("Term", 2)],
        }
        for field in UTM_FIELDS:
            self.assertEqual(database.get_utm_counts(field), expected[field])
        self.assertEqual(database.get_campaign_counts(), [
            (None, None, None, 1), ("Source", "Email", "Launch", 2),
            ("Source", "Social", "Launch", 1),
        ])
        self.assertEqual(database.get_utm_counts("utm_source", link_id=database.get_link_by_short_code("other")[0]), [("Source", 1)])

    def test_utc_date_bounds_are_inclusive_start_exclusive_end(self):
        link_id = self.link()
        start = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
        for hours in (-1, 0, 1, 2):
            with patch("database.datetime", wraps=datetime) as clock:
                clock.now.return_value = start + timedelta(hours=hours)
                database.record_link_click(link_id)
        offset_start = start.astimezone(timezone(timedelta(hours=3, minutes=30)))
        self.assertEqual(database.get_utm_counts("utm_source", start=offset_start, end=start + timedelta(hours=2)), [("Source", 2)])
        self.assertEqual(database.get_campaign_counts(start=start, end=start + timedelta(hours=2)), [("Source", "Email", "Launch", 2)])
        self.assertEqual(database.get_utm_counts("utm_source", start=start), [("Source", 3)])
        self.assertEqual(database.get_utm_counts("utm_source", end=start), [("Source", 1)])

    def test_invalid_report_arguments(self):
        with self.assertRaises(ValueError):
            database.get_utm_counts("utm_source; DROP TABLE links")
        with self.assertRaises(ValueError):
            database.get_campaign_counts(start=datetime(2026, 1, 1))
        with self.assertRaises(ValueError):
            database.get_campaign_counts(start=datetime(2026, 1, 2, tzinfo=timezone.utc), end=datetime(2026, 1, 1, tzinfo=timezone.utc))

    def test_historical_counters_do_not_create_report_data(self):
        self.link()
        self.connection.execute("UPDATE links SET click_count = 50")
        self.connection.commit()
        self.assertEqual(database.get_campaign_counts(), [])
        self.client.get("/abc123")
        self.assertEqual(database.get_utm_counts("utm_source"), [("Source", 1)])
        self.assertEqual(database.get_link_by_short_code("abc123")[8], 51)

    def test_insert_failure_rolls_back_utm_event_and_counters(self):
        self.link()
        self.connection.execute("""
            CREATE TRIGGER reject_utm AFTER INSERT ON click_events
            BEGIN SELECT RAISE(ABORT, 'test'); END
        """)
        self.connection.commit()
        self.assertEqual(self.client.get("/abc123").status_code, 503)
        self.assertEqual(self.events(), [])
        self.assertEqual(database.get_link_by_short_code("abc123")[8:10], (0, None))

    def test_no_events_for_missing_or_unavailable_links(self):
        self.link()
        self.assertEqual(self.client.get("/missing?utm_source=Incoming").status_code, 404)
        for active, expiration in ((0, None), (1, "2000-01-01T00:00:00+00:00"), (1, "invalid")):
            self.connection.execute("UPDATE links SET is_active = ?, expires_at = ?", (active, expiration))
            self.connection.commit()
            self.assertEqual(self.client.get("/abc123").status_code, 410)
        self.assertEqual(self.events(), [])


if __name__ == "__main__":
    unittest.main()
