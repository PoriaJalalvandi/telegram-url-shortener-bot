"""Explicit local/production configuration; no network or DNS lookups."""

import ipaddress
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from referrer import normalize_referrer


def production_mode():
    mode = os.environ.get("APP_ENV", "development").strip().lower()
    if mode not in ("development", "production"):
        raise ValueError("APP_ENV must be development or production")
    return mode == "production"


def public_http_url(value):
    origin = normalize_referrer(value)
    if origin is None:
        return False
    host = urlsplit(origin).hostname.rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".invalid", ".test", ".example", ".home.arpa", ".lan")):
        return False
    try:
        address = ipaddress.ip_address(host)
        return address.is_global and not address.is_multicast and not address.is_reserved
    except ValueError:
        # Reject ambiguous legacy/hex IP spellings browsers may interpret locally.
        labels = host.split(".")
        numeric = all(label.isdigit() or re.fullmatch(r"0x[0-9a-f]+", label) for label in labels)
        return "." in host and not labels[-1].isdigit() and not numeric


def get_base_url():
    configured = os.environ.get("BASE_URL")
    if configured is None and production_mode():
        raise ValueError("Production requires an explicit public HTTPS BASE_URL")
    value = configured.strip() if configured is not None else "http://127.0.0.1:5000"
    origin = normalize_referrer(value)
    if origin is None or urlsplit(value).path not in ("", "/") or "?" in value or "#" in value:
        raise ValueError("BASE_URL must be an HTTP/HTTPS origin without credentials, path, query, or fragment")
    if production_mode() and (not origin.startswith("https://") or not public_http_url(origin)):
        raise ValueError("Production requires a public HTTPS BASE_URL")
    return origin


def validate_runtime():
    """Both processes must explicitly select production and shared persistence."""
    if "DATABASE_PATH" in os.environ and not os.environ["DATABASE_PATH"].strip():
        raise ValueError("DATABASE_PATH must not be empty")
    if production_mode():
        if os.environ.get("FLASK_DEBUG", "0").lower() not in ("0", "false", ""):
            raise ValueError("Flask debug mode must be disabled in production")
        get_base_url()
        database_path = os.environ.get("DATABASE_PATH", "")
        if not database_path or not Path(database_path).is_absolute() or database_path == ":memory:":
            raise ValueError("Production requires an absolute persistent DATABASE_PATH")
