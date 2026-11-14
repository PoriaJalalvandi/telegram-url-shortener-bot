import ipaddress
from urllib.parse import urlsplit, urlunsplit


def validate_url(url):

    if not isinstance(url, str):
        return False

    if not url or len(url) > 2048:
        return False

    if url != url.strip():
        return False

    if any(char.isspace() or ord(char) < 32 for char in url):
        return False

    try:
        parsed = urlsplit(url)

        if parsed.scheme not in ("http", "https"):
            return False

        hostname = parsed.hostname

        if not hostname:
            return False

        if parsed.username is not None or parsed.password is not None:
            return False

        # Reject invalid port numbers.
        port = parsed.port

        if port is not None and port == 0:
            return False

        # Reject IP addresses for the initial version.
        try:
            ipaddress.ip_address(hostname)
            return False
        except ValueError:
            pass

        hostname = hostname.lower()

        if hostname == "localhost":
            return False

        if not hostname.endswith(".") and "." not in hostname:
            return False

        hostname = hostname.rstrip(".")

        labels = hostname.split(".")

        if len(labels) < 2:
            return False

        for label in labels:
            if not label or len(label) > 63:
                return False

            if label.startswith("-") or label.endswith("-"):
                return False

            if not all(
                char.isascii() and (char.isalnum() or char == "-")
                for char in label
            ):
                return False

        if labels[-1].isdigit():
            return False

        return True

    except ValueError:
        return False


def normalize_url(url):

    if not validate_url(url):
        raise ValueError("Invalid URL")

    parsed = urlsplit(url)

    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower()

    port = parsed.port

    if (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    ):
        port = None

    netloc = hostname

    if port is not None:
        netloc = f"{hostname}:{port}"

    path = parsed.path or "/"

    normalized_url = urlunsplit(
        (
            scheme,
            netloc,
            path,
            parsed.query,
            parsed.fragment,
        )
    )

    return normalized_url