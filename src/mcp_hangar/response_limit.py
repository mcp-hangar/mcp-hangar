"""The most bytes of one upstream response Hangar reads (#1613).

Both transports enforce it while they read, before a response is held whole:
the stdio reader stops reading a line past it, and the HTTP transport stops
reading a body past it. A response over it fails its call with
`ResponseTooLarge`, the same on every surface.

Configured once, at config time, and applied to every server that does not
set its own:

    execution:
      max_response_bytes: 33554432

``MCP_MAX_RESPONSE_BYTES`` wins over the file, as ``MCP_TRACING_CALLER_IDS``
does over ``observability.tracing.caller_ids``. A server's own
``mcp_servers.<id>.max_response_bytes`` wins over both.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

#: 32 MiB. Up from the 10 MiB cap it replaces, which was checked only after a
#: result had been read whole, so no result served before is refused now.
DEFAULT_MAX_RESPONSE_BYTES = 32 * 1024 * 1024
ENV_VAR = "MCP_MAX_RESPONSE_BYTES"

_default = DEFAULT_MAX_RESPONSE_BYTES


def default_max_response_bytes() -> int:
    """The process-wide limit a server without its own reads with."""
    return _default


def set_default_max_response_bytes(limit: int) -> None:
    """Set the process-wide limit. Startup and a reload call it with the resolved value."""
    global _default
    _default = limit


def parse_max_response_bytes(raw: object, where: str) -> int | None:
    """*raw* as a limit, None when absent.

    ``True`` is an ``int`` to ``isinstance`` and is refused, as the other limits
    refuse it: ``yes`` is not a limit of one byte.

    Raises:
        ValueError: *raw* is present and not a positive whole number of bytes.
    """
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip().isdigit():
        raw = int(raw.strip())
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise ValueError(
            f"Invalid {where} {raw!r}. It must be a positive whole number of bytes; "
            f"omit it to keep the default of {DEFAULT_MAX_RESPONSE_BYTES}."
        )
    return raw


def resolve_max_response_bytes(full_config: Mapping[str, Any], env: Mapping[str, str] | None = None) -> int:
    """The process-wide limit: ``MCP_MAX_RESPONSE_BYTES``, else ``execution.max_response_bytes``, else the default.

    Raises:
        ValueError: The value that applies is not a positive whole number.
    """
    env = os.environ if env is None else env
    from_env = parse_max_response_bytes(env.get(ENV_VAR) or None, ENV_VAR)
    if from_env is not None:
        return from_env
    section = full_config.get("execution")
    raw = section.get("max_response_bytes") if isinstance(section, dict) else None
    from_file = parse_max_response_bytes(raw, "execution.max_response_bytes")
    return from_file if from_file is not None else DEFAULT_MAX_RESPONSE_BYTES
