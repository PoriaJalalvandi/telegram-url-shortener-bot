from datetime import datetime, timezone
import sqlite3

from flask import Flask, redirect, request

from database import get_link_by_short_code, record_link_click
from user_agent import classify_user_agent
from referrer import normalize_referrer
from configuration import validate_runtime
from operational_errors import configure_logging, log_error
from werkzeug.exceptions import HTTPException


validate_runtime()
configure_logging()
app = Flask(__name__)
app.config["DEBUG"] = False


@app.errorhandler(Exception)
def unexpected_error(error):
    if isinstance(error, HTTPException):
        return error
    log_error("web_request_failed", error)
    return "Service temporarily unavailable", 500


@app.route("/")
def home():
    return "Telegram URL Shortener Bot is running"


@app.route("/<short_code>")
def open_short_link(short_code):
    try:
        link = get_link_by_short_code(short_code)
    except sqlite3.Error as error:
        log_error("link_lookup_failed", error)
        return "Service temporarily unavailable", 503

    if link is None:
        return "Link not found", 404

    if link[7] != 1:
        return "Link is no longer available", 410

    expires_at = link[6]
    if expires_at is not None:
        try:
            expiration = datetime.fromisoformat(expires_at)
            # A missing offset is ambiguous; reject instead of assuming local time.
            if expiration.tzinfo is None:
                return "Link is no longer available", 410
            expiration = expiration.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return "Link is no longer available", 410

        if expiration <= datetime.now(timezone.utc):
            return "Link is no longer available", 410

    original_url = link[2]

    try:
        record_link_click(
            link[0],
            referrer=normalize_referrer(request.headers.get("Referer")),
            **classify_user_agent(request.headers.get("User-Agent")),
        )
    except sqlite3.Error as error:
        log_error("click_recording_failed", error)
        return "Unable to record this visit. Please try again.", 503

    return redirect(original_url)


if __name__ == "__main__":
    app.run(debug=False)
