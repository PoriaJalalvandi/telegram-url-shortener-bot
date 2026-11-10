import secrets
import sqlite3
import string

from database import create_link, get_link_by_short_code


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

    for attempt in range(max_attempts):

        short_code = generate_unique_short_code()

        try:
            link_id = create_link(
                user_id=user_id,
                original_url=original_url,
                short_code=short_code,
            )

            return link_id, short_code

        except sqlite3.IntegrityError as error:

            if "links.short_code" not in str(error):
                raise

    raise RuntimeError("Could not create a unique short link")


if __name__ == "__main__":

    link_id, short_code = create_short_link(
        user_id=1,
        original_url="https://www.python.org",
    )

    print(f"Link ID: {link_id}")
    print(f"Short Code: {short_code}")