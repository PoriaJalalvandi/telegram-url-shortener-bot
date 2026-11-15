import secrets
import sqlite3
import string

from database import (
    get_connection,
    create_link,
    get_link_by_short_code,
    get_duplicate_link,
    is_alias_available,
)

from validators import (
    validate_url,
    normalize_url,
    validate_alias,
    normalize_expires_at,
)


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


def create_short_link(user_id, original_url, max_attempts=5, *, expires_at=None, return_reused=False):
    """Return (id, code), optionally appending reuse status from the transaction."""

    # Step 1: Validate URL
    if not validate_url(original_url):
        raise ValueError("Invalid URL")

    # Step 2: Normalize URL
    normalized_url = normalize_url(original_url)
    expires_at = normalize_expires_at(expires_at)

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
            result = (existing_link[0], existing_link[3])
            return (*result, True) if return_reused else result

        # Step 6: Create a new short link
        for attempt in range(max_attempts):

            short_code = generate_unique_short_code()

            try:
                link_id = create_link(
                    user_id=user_id,
                    original_url=original_url,
                    normalized_url=normalized_url,
                    short_code=short_code,
                    expires_at=expires_at,
                    connection=connection,
                )

                connection.commit()

                result = (link_id, short_code)
                return (*result, False) if return_reused else result

            except sqlite3.IntegrityError as error:

                if not any(marker in str(error) for marker in ("links.short_code", "idx_links_short_code_nocase")):
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


def create_custom_link(user_id, original_url, alias, *, expires_at=None):

    # Step 1: Validate URL
    if not validate_url(original_url):
        raise ValueError("Invalid URL")

    # Step 2: Validate alias
    if not validate_alias(alias):
        raise ValueError("Invalid alias")

    # Step 3: Normalize inputs
    normalized_url = normalize_url(original_url)
    normalized_alias = alias.lower()
    expires_at = normalize_expires_at(expires_at)

    # Step 4: Open database connection
    connection = get_connection()

    try:
        # Step 5: Start transaction
        connection.execute("BEGIN IMMEDIATE")

        # Step 6: Check alias availability
        if not is_alias_available(
            normalized_alias,
            connection=connection,
        ):
            raise ValueError("Alias already taken")

        # Step 7: Create custom link
        link_id = create_link(
            user_id=user_id,
            original_url=original_url,
            normalized_url=normalized_url,
            short_code=normalized_alias,
            is_custom=True,
            expires_at=expires_at,
            connection=connection,
        )

        # Step 8: Commit transaction
        connection.commit()

        return link_id, normalized_alias

    except sqlite3.IntegrityError as error:
        connection.rollback()

        error_name = getattr(
            error,
            "sqlite_errorname",
            None
        )

        if error_name == "SQLITE_CONSTRAINT_UNIQUE":
            raise ValueError("Alias already taken") from error

        raise

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()
