"""The local names a registry server is known by.

Registry names are reverse-DNS with a path, `io.github.acme/weather`. Hangar's
own ids allow letters, digits, hyphens and underscores only, so a hot-loaded
server is registered under a sanitized form. That mapping is lossy:
`io.github.acme/x-y` and `io.github.acme-x/y` sanitize alike. Anything that
decides what a server is *allowed* -- its trust, its secrets, the directory its
secret files are read from -- keys on the exact registry name, never on that.
"""

import re

#: What one segment of a registry name may contain to be used as a directory.
SECRETS_SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9._-]+")


def sanitize_mcp_server_id(server_id: str) -> str:
    """Sanitize a registry server name to be a valid McpServerId.

    Converts dots and slashes to hyphens, collapses repeated hyphens and strips
    them from both ends. Lossy, so it names a server and decides nothing.

    Args:
        server_id: The server name from the registry.

    Returns:
        A sanitized mcp_server ID.
    """
    sanitized = server_id.replace("/", "-").replace(".", "-")
    while "--" in sanitized:
        sanitized = sanitized.replace("--", "-")
    return sanitized.strip("-")


def secrets_dir_segments(server_name: str) -> tuple[str, ...] | None:
    """The directory path, below the secrets directory, of a server's own secret files.

    The registry name split on `/`, one directory per segment:
    `io.github.acme/weather` is `io.github.acme/weather/`. Injective, unlike the
    sanitized id, so no two servers share a directory. None when a segment is
    empty, ends in `.` (which covers `.` and `..`; Windows strips trailing dots,
    so `weather.` and `weather` would be one directory) or has a character
    outside `SECRETS_SEGMENT_PATTERN`: such a server has no per-server
    directory at all.
    """
    segments = tuple(server_name.split("/"))
    for segment in segments:
        if segment.endswith(".") or not SECRETS_SEGMENT_PATTERN.fullmatch(segment):
            return None
    return segments
