import sqlite3
import os
from datetime import datetime, timezone

from utm import UTM_FIELDS, extract_utm


DATABASE = os.environ.get("DATABASE_PATH", "url_shortener.db")


# -------------------------
# Database connection
# -------------------------

def get_connection():
    connection = sqlite3.connect(DATABASE)

    # SQLite does not enforce foreign keys by default.
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


# -------------------------
# Database setup
# -------------------------

def create_tables():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            first_name TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            original_url TEXT NOT NULL,
            normalized_url TEXT,
            short_code TEXT UNIQUE NOT NULL,
            is_custom INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            click_count INTEGER NOT NULL DEFAULT 0,
            last_click_at TEXT,

            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_links_short_code_nocase
        ON links(short_code COLLATE NOCASE)
    """)

    # Future writers should supply UTC ISO 8601 timestamps and normalized metadata.
    # UTM fields are optional snapshots of the destination's campaign parameters.
    # Store no raw IP addresses or complete User-Agent strings.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS click_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link_id INTEGER NOT NULL,
            clicked_at TEXT NOT NULL,
            visitor_id TEXT,
            is_bot INTEGER CHECK (is_bot IN (0, 1)),
            country_code TEXT,
            device_type TEXT,
            operating_system TEXT,
            referrer TEXT,
            utm_source TEXT,
            utm_medium TEXT,
            utm_campaign TEXT,
            utm_term TEXT,
            utm_content TEXT,

            FOREIGN KEY (link_id) REFERENCES links(id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_click_events_link_clicked_at
        ON click_events(link_id, clicked_at)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_click_events_clicked_at
        ON click_events(clicked_at)
    """)

    connection.commit()
    connection.close()


# -------------------------
# User functions
# -------------------------

def create_user(telegram_user_id, username, first_name):
    connection = get_connection()
    cursor = connection.cursor()

    created_at = datetime.now(timezone.utc).isoformat()

    cursor.execute(
        """
        INSERT OR IGNORE INTO users (
            telegram_user_id,
            username,
            first_name,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            telegram_user_id,
            username,
            first_name,
            created_at,
        ),
    )

    connection.commit()
    connection.close()


def get_user_by_telegram_id(telegram_user_id):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            telegram_user_id,
            username,
            first_name,
            created_at
        FROM users
        WHERE telegram_user_id = ?
        """,
        (telegram_user_id,),
    )

    user = cursor.fetchone()

    connection.close()

    return user


# -------------------------
# Link functions
# -------------------------

def create_link(
    user_id,
    original_url,
    short_code,
    is_custom=False,
    expires_at=None,
    normalized_url=None,
    connection=None
):
    owns_connection = connection is None

    if owns_connection:
        connection = get_connection()

    try:
        cursor = connection.cursor()

        created_at = datetime.now(timezone.utc).isoformat()

        cursor.execute(
            """
            INSERT INTO links (
                user_id,
                original_url,
                normalized_url,
                short_code,
                is_custom,
                created_at,
                expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                original_url,
                normalized_url,
                short_code,
                is_custom,
                created_at,
                expires_at
            )
        )

        if owns_connection:
            connection.commit()

        return cursor.lastrowid

    except Exception:
        if owns_connection:
            connection.rollback()
        raise

    finally:
        if owns_connection:
            connection.close()

def record_link_click(link_id, *, device_type=None, operating_system=None, is_bot=None, referrer=None):
    """Commit the counter update and one event together, or roll back both."""
    connection = get_connection()
    try:
        with connection:
            clicked_at = datetime.now(timezone.utc).isoformat()
            cursor = connection.execute(
                """
                UPDATE links
                SET click_count = click_count + 1,
                    last_click_at = ?
                WHERE id = ?
                """,
                (clicked_at, link_id),
            )
            if cursor.rowcount != 1:
                raise sqlite3.DatabaseError("Click target no longer exists")
            # The UPDATE holds the write lock: snapshot the stored destination
            # in this same transaction, never tags from the incoming request.
            original_url = connection.execute(
                "SELECT original_url FROM links WHERE id = ?", (link_id,)
            ).fetchone()[0]
            utm = extract_utm(original_url)
            cursor = connection.execute(
                """
                INSERT INTO click_events (
                    link_id, clicked_at, device_type, operating_system, is_bot, referrer,
                    utm_source, utm_medium, utm_campaign, utm_content, utm_term
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (link_id, clicked_at, device_type, operating_system, is_bot, referrer,
                 *(utm[field] for field in UTM_FIELDS)),
            )
            if cursor.rowcount != 1:
                raise sqlite3.DatabaseError("Click event was not recorded")
    finally:
        connection.close()


def get_utm_counts(dimension, *, start=None, end=None, link_id=None):
    """Return (value, event_count) rows for one whitelisted UTM column.

    NULL is an explicit unattributed group. Dates are aware datetimes, with
    inclusive start and exclusive end. Includes bot events, never legacy counters.
    """
    if dimension not in UTM_FIELDS:
        raise ValueError("Unsupported UTM dimension")
    return _get_utm_counts((dimension,), start=start, end=end, link_id=link_id)


def get_campaign_counts(*, start=None, end=None, link_id=None):
    """Return (source, medium, campaign, event_count) rows from event snapshots."""
    return _get_utm_counts(
        ("utm_source", "utm_medium", "utm_campaign"),
        start=start, end=end, link_id=link_id,
    )


def _event_report_filter(*, start, end, link_id):
    bounds = []
    for value in (start, end):
        if value is not None:
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise ValueError("Date bounds must be timezone-aware datetimes")
            value = value.astimezone(timezone.utc)
        bounds.append(value)
    start, end = bounds
    if start is not None and end is not None and start >= end:
        raise ValueError("start must be before end")

    clauses, parameters = [], []
    for bound, operator in ((start, ">="), (end, "<")):
        if bound is not None:
            # Compare instants, including stored UTC values with/without fractions.
            clauses.append(f"julianday(clicked_at) {operator} julianday(?)")
            parameters.append(bound.isoformat())
    if link_id is not None:
        clauses.append("link_id = ?")
        parameters.append(link_id)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return where, parameters


def _get_utm_counts(dimensions, *, start, end, link_id):
    where, parameters = _event_report_filter(start=start, end=end, link_id=link_id)
    columns = ", ".join(dimensions)  # Only internal constants/whitelisted identifiers.
    connection = get_connection()
    try:
        return connection.execute(
            f"SELECT {columns}, COUNT(*) FROM click_events{where} "
            f"GROUP BY {columns} ORDER BY {columns}", parameters,
        ).fetchall()
    finally:
        connection.close()


def get_daily_click_counts(*, link_id=None, start=None, end=None):
    """Return (UTC day, total, bots, likely_non_bots, unknown) event-count rows.

    Days without events are omitted. Aware date bounds use [start, end).
    Historical link counters are not consulted. Invalid stored dates are omitted.
    """
    where, parameters = _event_report_filter(start=start, end=end, link_id=link_id)
    where += " AND " if where else " WHERE "
    where += "date(clicked_at) IS NOT NULL"
    connection = get_connection()
    try:
        return connection.execute(
            f"""
            SELECT date(clicked_at) AS day,
                   COUNT(*) AS total_events,
                   SUM(CASE WHEN is_bot = 1 THEN 1 ELSE 0 END) AS bot_events,
                   SUM(CASE WHEN is_bot = 0 THEN 1 ELSE 0 END) AS likely_non_bot_events,
                   SUM(CASE WHEN is_bot IS NULL THEN 1 ELSE 0 END) AS unknown_events
            FROM click_events{where}
            GROUP BY day
            ORDER BY day
            """,
            parameters,
        ).fetchall()
    finally:
        connection.close()


def get_link_by_short_code(short_code):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            user_id,
            original_url,
            short_code,
            is_custom,
            created_at,
            expires_at,
            is_active,
            click_count,
            last_click_at
        FROM links
        WHERE short_code = ? COLLATE NOCASE
        """,
        (short_code,),
    )

    link = cursor.fetchone()

    connection.close()

    return link


def list_user_links(telegram_user_id):
    """Return only the authenticated user's latest ten links, without analytics."""
    connection = get_connection()
    try:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(
            """
            SELECT l.short_code, l.original_url, l.expires_at, l.is_active, l.created_at
            FROM links AS l JOIN users AS u ON l.user_id = u.id
            WHERE u.telegram_user_id = ?
            ORDER BY julianday(l.created_at) DESC, l.id DESC LIMIT 10
            """, (telegram_user_id,),
        )]
    finally:
        connection.close()


def list_recent_links():
    """Internal admin helper: latest ten links, without user personal information."""
    connection = get_connection()
    try:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(
            """
            SELECT short_code, original_url, expires_at, is_active, created_at
            FROM links ORDER BY julianday(created_at) DESC, id DESC LIMIT 10
            """
        )]
    finally:
        connection.close()


def get_analytics_overview():
    """Counters and events are separate totals; all values share one SQL snapshot."""
    connection = get_connection()
    try:
        connection.row_factory = sqlite3.Row
        return dict(connection.execute("""
            SELECT (SELECT COUNT(*) FROM users) AS users,
                   (SELECT COUNT(*) FROM links) AS links,
                   (SELECT COALESCE(SUM(click_count), 0) FROM links) AS recorded_clicks,
                   COUNT(*) AS events,
                   COALESCE(SUM(is_bot = 1), 0) AS bots,
                   COALESCE(SUM(is_bot = 0), 0) AS non_bots,
                   COALESCE(SUM(is_bot IS NULL), 0) AS unknown
            FROM click_events
        """).fetchone())
    finally:
        connection.close()


def get_link_event_summary(link_id):
    connection = get_connection()
    try:
        connection.row_factory = sqlite3.Row
        return dict(connection.execute("""
            SELECT COUNT(*) AS events,
                   COALESCE(SUM(is_bot = 1), 0) AS bots,
                   COALESCE(SUM(is_bot = 0), 0) AS non_bots,
                   COALESCE(SUM(is_bot IS NULL), 0) AS unknown
            FROM click_events WHERE link_id = ?
        """, (link_id,)).fetchone())
    finally:
        connection.close()


def get_client_counts(dimension, *, link_id=None):
    """Top five normalized device/OS categories by event count."""
    if dimension not in ("device_type", "operating_system"):
        raise ValueError("Unsupported client dimension")
    where = " WHERE link_id = ?" if link_id is not None else ""
    parameters = (link_id,) if link_id is not None else ()
    connection = get_connection()
    try:
        return connection.execute(
            f"SELECT COALESCE({dimension}, 'Unknown') AS category, COUNT(*) AS events "
            f"FROM click_events{where} GROUP BY category "
            "ORDER BY events DESC, category LIMIT 5", parameters,
        ).fetchall()
    finally:
        connection.close()


def iter_export_rows(report_type, *, start, end, row_limit=5000):
    """Stream at most limit+1 whitelisted report rows; caller detects truncation.

    Overview inventory/counters are all-time, events are range-filtered. Links
    are filtered by creation time; all other reports filter event timestamps.
    """
    if type(row_limit) is not int or not 1 <= row_limit <= 5000:
        raise ValueError("Invalid export row limit")
    where, parameters = _event_report_filter(start=start, end=end, link_id=None)
    queries = {
        "overview": """SELECT
            (SELECT COUNT(*) FROM users) AS all_time_registered_users,
            (SELECT COUNT(*) FROM links) AS all_time_total_links,
            (SELECT COALESCE(SUM(click_count), 0) FROM links) AS all_time_historical_click_counters,
            COUNT(*) AS recorded_click_events,
            COALESCE(SUM(is_bot=1), 0) AS bot_events,
            COALESCE(SUM(is_bot=0), 0) AS likely_non_bot_events,
            COALESCE(SUM(is_bot IS NULL), 0) AS unknown_events
            FROM click_events""" + where,
        "links": """SELECT short_code, original_url, created_at, expires_at, is_active,
            click_count AS historical_click_count FROM links"""
            + where.replace("clicked_at", "created_at") + " ORDER BY julianday(created_at), id",
        "daily": """SELECT date(clicked_at) AS utc_date, COUNT(*) AS total_click_events,
            SUM(CASE WHEN is_bot=1 THEN 1 ELSE 0 END) AS bot_events,
            SUM(CASE WHEN is_bot=0 THEN 1 ELSE 0 END) AS likely_non_bot_events,
            SUM(CASE WHEN is_bot IS NULL THEN 1 ELSE 0 END) AS unknown_events
            FROM click_events""" + (where + " AND " if where else " WHERE ")
            + "date(clicked_at) IS NOT NULL GROUP BY utc_date ORDER BY utc_date",
        "utm": """SELECT utm_source, utm_medium, utm_campaign, utm_content, utm_term,
            COUNT(*) AS click_event_count FROM click_events""" + where
            + " GROUP BY utm_source, utm_medium, utm_campaign, utm_content, utm_term "
            "ORDER BY click_event_count DESC, utm_source, utm_medium, utm_campaign, utm_content, utm_term",
        "devices": """SELECT device_type, operating_system, COUNT(*) AS click_event_count
            FROM click_events""" + where
            + " GROUP BY device_type, operating_system ORDER BY click_event_count DESC, device_type, operating_system",
        "events": """SELECT e.clicked_at, l.short_code, l.original_url,
            e.utm_source, e.utm_medium, e.utm_campaign, e.utm_content, e.utm_term,
            e.device_type, e.operating_system, e.is_bot, e.referrer
            FROM click_events e JOIN links l ON e.link_id=l.id"""
            + where.replace("clicked_at", "e.clicked_at") + " ORDER BY julianday(e.clicked_at), e.id",
    }
    if report_type not in queries:
        raise ValueError("Unsupported export type")
    connection = get_connection()
    try:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute(queries[report_type] + " LIMIT ?", [*parameters, row_limit + 1])
        while rows := cursor.fetchmany(128):
            for row in rows:
                yield dict(row)
    finally:
        connection.close()


# -------------------------
# Run database setup
# -------------------------

if __name__ == "__main__":
    from configuration import validate_runtime
    validate_runtime()
    create_tables()
    print("Database tables are ready.")

def get_duplicate_link(
    user_id,
    normalized_url,
    connection=None
):
    owns_connection = connection is None

    if owns_connection:
        connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                user_id,
                original_url,
                short_code,
                is_custom,
                created_at,
                expires_at,
                is_active,
                click_count,
                last_click_at,
                normalized_url
            FROM links
            WHERE user_id = ?
              AND normalized_url = ?
              AND is_custom = 0
              AND is_active = 1
              AND (
                    expires_at IS NULL
                    OR expires_at > ?
                  )
            ORDER BY id ASC
            LIMIT 1
            """,
            (
                user_id,
                normalized_url,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

        return cursor.fetchone()

    finally:
        if owns_connection:
            connection.close()


def is_alias_available(alias, connection=None):

    owns_connection = connection is None

    if owns_connection:
        connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT 1
            FROM links
            WHERE LOWER(short_code) = LOWER(?)
            LIMIT 1
            """,
            (alias,)
        )

        return cursor.fetchone() is None

    finally:
        if owns_connection:
            connection.close()
