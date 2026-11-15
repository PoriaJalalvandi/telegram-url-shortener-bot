"""Bounded, formula-safe CSV documents. No Telegram calls or public files."""

import csv
import io
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from tempfile import SpooledTemporaryFile

import database
from bot_ui import status


MAX_ROWS = 5000
MAX_BYTES = 8 * 1024 * 1024
HEADERS = {
    "overview": ("all_time_registered_users", "all_time_total_links", "all_time_historical_click_counters", "recorded_click_events", "bot_events", "likely_non_bot_events", "unknown_events"),
    "links": ("short_code", "short_url", "original_url", "created_at", "expires_at", "status", "historical_click_count"),
    "daily": ("utc_date", "total_click_events", "bot_events", "likely_non_bot_events", "unknown_events"),
    "utm": ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "click_event_count"),
    "devices": ("device_type", "operating_system", "click_event_count"),
    "events": ("clicked_at", "short_code", "original_url", "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "device_type", "operating_system", "is_bot", "referrer"),
}


def safe_cell(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    # CSV quoting alone does not prevent spreadsheet formula execution.
    leading = re.sub(r"^[\s\ufeff\x00-\x1f]*", "", value)
    if (leading.startswith(("=", "+", "-", "@"))
            or value.startswith(("\t", "\r", "\n"))):
        return "'" + value
    return value


def utc_timestamp(value):
    if value is None:
        return ""
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            return "Invalid timestamp"
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return "Invalid timestamp"


def encode_row(values):
    row = io.StringIO(newline="")
    csv.writer(row).writerow([safe_cell(value) for value in values])
    return row.getvalue().encode("utf-8")


@contextmanager
def generate_csv(report_type, *, start, end, base_url):
    """Yield (file, row_count, truncated). Always close file and DB generator."""
    headers = HEADERS[report_type]
    rows = database.iter_export_rows(report_type, start=start, end=end, row_limit=MAX_ROWS)
    with SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as document:
        try:
            document.write(b"\xef\xbb\xbf" + encode_row(headers))
            count, truncated = 0, False
            for row in rows:
                if count == MAX_ROWS:
                    truncated = True
                    break
                if report_type == "links":
                    row["short_url"] = base_url + "/" + row["short_code"]
                    row["status"] = status(row.pop("is_active"), row["expires_at"])
                for field in ("clicked_at", "created_at", "expires_at"):
                    if field in row:
                        row[field] = utc_timestamp(row[field])
                encoded = encode_row(row.get(field) for field in headers)
                if document.tell() + len(encoded) > MAX_BYTES:
                    truncated = True
                    break
                document.write(encoded)
                count += 1
            document.seek(0)
            # Release the SQLite read connection before waiting on Telegram IO.
            rows.close()
            yield document, count, truncated
        finally:
            rows.close()
