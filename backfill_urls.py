from database import get_connection
from validators import normalize_url


def backfill_normalized_urls():

    connection = get_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id, original_url
            FROM links
            WHERE normalized_url IS NULL
            """
        )

        links = cursor.fetchall()

        for link_id, original_url in links:

            normalized_url = normalize_url(original_url)

            cursor.execute(
                """
                UPDATE links
                SET normalized_url = ?
                WHERE id = ?
                """,
                (normalized_url, link_id),
            )

            print(f"Updated link {link_id}: {normalized_url}")

        connection.commit()

        print(f"Backfill completed: {len(links)} links updated.")

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


if __name__ == "__main__":
    backfill_normalized_urls()