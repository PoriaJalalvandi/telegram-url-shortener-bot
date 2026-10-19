import sqlite3
from datetime import datetime, timezone

DATABASE = "url_shortener.db"


def create_tables():
    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            first_name TEXT,
            created_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            original_url TEXT NOT NULL,
            short_code TEXT UNIQUE NOT NULL,
            is_custom INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            click_count INTEGER NOT NULL DEFAULT 0,
            last_click_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    connection.commit()
    connection.close()


def create_user(telegram_user_id, username, first_name):
    connection = sqlite3.connect(DATABASE)
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
        (telegram_user_id, username, first_name, created_at),
    )

    connection.commit()
    connection.close()


def get_user_by_telegram_id(telegram_user_id):
    connection = sqlite3.connect(DATABASE)
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT id, telegram_user_id, username, first_name, created_at
        FROM users
        WHERE telegram_user_id = ?
        """,
        (telegram_user_id,)
    )

    user = cursor.fetchone()

    connection.close()

    return user


if __name__ == "__main__":
    create_tables()

    create_user(
        123456789,
        "ali_test",
        "Ali",
    )

    user = get_user_by_telegram_id(123456789)
    print(f"Retrieved user: {user}")

    user = get_user_by_telegram_id(999999999)
    print(f"Retrieved user: {user}")
    
    print("test user created successfully.")
    