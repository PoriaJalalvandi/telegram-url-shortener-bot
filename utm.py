"""Destination campaign tags; these do not identify actual acquisition sources."""

import re
from urllib.parse import parse_qsl, urlsplit


UTM_FIELDS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")


def extract_utm(url):
    """Use the first well-formed occurrence of each exact, case-sensitive key.

    Missing/empty values become None. Invalid percent escapes, invalid UTF-8,
    and fields without '=' are ignored. Decode once, including '+' as a space;
    otherwise preserve values verbatim. Fragments are not query parameters.
    """
    result = dict.fromkeys(UTM_FIELDS)
    try:
        query = urlsplit(url).query
    except (TypeError, ValueError):
        return result
    seen = set()
    for field in query.split("&"):
        if re.search(r"%(?![0-9a-fA-F]{2})", field):
            continue
        try:
            pairs = parse_qsl(field, keep_blank_values=True, strict_parsing=True, errors="strict")
        except (ValueError, UnicodeError):
            continue
        for key, value in pairs:
            if key in result and key not in seen:
                seen.add(key)
                result[key] = value if value else None
    return result
