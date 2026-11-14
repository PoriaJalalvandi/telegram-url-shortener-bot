import secrets
import sqlite3
import string

from database import (
    get_connection,
    create_link,
    get_link_by_short_code,
    get_duplicate_link,
)

from validators import validate_url, normalize_url


def generate_short_code(length=6):
    characters = string.ascii_letters + string.digits

    short_code = "".join(
        secrets.choice(characters)
        for _ in range(length)
    )

    return short_code


def generate_unique_short_code(length=6, max_attempts=5):

    for attempt in range(max_attempts):

        short_code = generate_short_code(length)

        existing_link = get_link_by_short_code(short_code)

        if existing_link is None:
            return short_code

    raise RuntimeError("Could not generate a unique short code")


def create_short_link(user_id, original_url, max_attempts=5):

    # Step 1: Validate URL
    if not validate_url(original_url):
        raise ValueError("Invalid URL")

    # Step 2: Normalize URL
    normalized_url = normalize_url(original_url)

    # Step 3: Open a shared database connection
    connection = get_connection()

    try:
        # Step 4: Start a write transaction
        connection.execute("BEGIN IMMEDIATE")

        # Step 5: Check for an existing link
        existing_link = get_duplicate_link(
            user_id=user_id,
            normalized_url=normalized_url,
            connection=connection,
        )

        if existing_link is not None:
            connection.commit()
            return existing_link[0], existing_link[3]

        # Step 6: Create a new short link
        for attempt in range(max_attempts):

            short_code = generate_unique_short_code()

            try:
                link_id = create_link(
                    user_id=user_id,
                    original_url=original_url,
                    normalized_url=normalized_url,
                    short_code=short_code,
                    connection=connection,
                )

                connection.commit()

                return link_id, short_code

            except sqlite3.IntegrityError as error:

                if "links.short_code" not in str(error):
                    raise

        raise RuntimeError(
            "Could not create a unique short link"
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

if __name__ == "__main__":

    try:
        link_id, short_code = create_short_link(
            user_id=1,
            original_url="not-a-url",
        )

        print(f"Link created: {short_code}")

    except ValueError as error:
        print(f"Validation error: {error}")