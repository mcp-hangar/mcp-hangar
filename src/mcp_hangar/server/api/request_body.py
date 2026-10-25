"""Shared request-body validation for the REST surface.

Five mutating endpoints indexed the parsed JSON body directly --
``body["mcp_server_id"]``, ``body["group_id"]``, ``body["source_type"]`` and so
on. A caller who omitted a field got a `KeyError`, which is not a `ValueError`
and so escaped the routes' handlers into a generic 500 with "an internal server
error occurred".

Two costs. The caller is told the server broke when in fact their request was
incomplete, so they have nothing to act on. And every such request lands in the
log as an unhandled exception, which is the same signal a real fault produces --
noise in exactly the channel that is supposed to be quiet.

A security audit found one of the five. The other four were the same line of
code in a different file.
"""

from __future__ import annotations

import json
from typing import Any

from starlette.requests import Request

from .serializers import HangarJSONResponse


def missing_fields(body: Any, *required: str) -> HangarJSONResponse | None:
    """A 400 naming the absent fields, or ``None`` when the body carries them all.

    Args:
        body: The parsed request body, which is not necessarily a dict.
        *required: Field names the handler will index.

    Returns:
        A response to return immediately, or None to proceed.
    """
    if not isinstance(body, dict):
        return HangarJSONResponse(
            {"error": "invalid_body", "detail": "request body must be a JSON object"},
            status_code=400,
        )
    absent = [field for field in required if field not in body]
    if not absent:
        return None
    return HangarJSONResponse(
        {"error": "missing_fields", "detail": f"required field(s) absent: {', '.join(absent)}"},
        status_code=400,
    )


async def json_body(request: Request) -> Any:
    """The parsed request body, or None when it is not JSON at all (#2066).

    Pair it with :func:`missing_fields`, which answers 400 ``invalid_body`` for
    anything that is not a JSON object -- None included -- and 400
    ``missing_fields`` for an absent field. `await request.json()` raised on a
    body that is not JSON, which answered a generic 500 and was logged as an
    unhandled fault for what was the caller's mistake.
    """
    try:
        return await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
