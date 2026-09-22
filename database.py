import sqlite3


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


if __name__ == "__main__":
    create_tables()
    print("Database and tables created successfully.")