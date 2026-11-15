"""Reduce referring URLs to origins without retaining URL-specific details."""

import ipaddress
import re
from urllib.parse import urlsplit


MAX_REFERRER_LENGTH = 2048


def normalize_referrer(value):
    """Return an HTTP(S) origin, or None for absent/invalid input."""
    if not isinstance(value, str) or not value or len(value) > MAX_REFERRER_LENGTH:
        return None
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    if "\\" in value or re.search(r"%(?![0-9a-fA-F]{2})", value):
        return None

    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        # Credentials are unusual in referrers; discard rather than retain them.
        if parsed.username is not None or parsed.password is not None:
            return None
        port = parsed.port
        if port == 0 or parsed.netloc.endswith(":"):
            return None

        hostname = parsed.hostname.lower()
        if ":" in hostname:
            hostname = f"[{ipaddress.IPv6Address(hostname).compressed}]"
        else:
            hostname = hostname.encode("idna").decode("ascii").lower()
            if len(hostname) > 253 or not all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in hostname.rstrip(".").split(".")
            ):
                return None
            if re.fullmatch(r"[0-9.]+", hostname):
                ipaddress.IPv4Address(hostname)

        if (parsed.scheme, port) in (("http", 80), ("https", 443)):
            port = None
        authority = hostname if port is None else f"{hostname}:{port}"
        return f"{parsed.scheme}://{authority}"
    except (ValueError, UnicodeError):
        return None
