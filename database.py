import sqlite3
from datetime import datetime, timezone


DATABASE = "url_shortener.db"


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
        WHERE short_code = ?
        """,
        (short_code,),
    )

    link = cursor.fetchone()

    connection.close()

    return link


# -------------------------
# Run database setup
# -------------------------

if __name__ == "__main__":
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