"""Maintenance endpoint handlers for the REST API.

Implements POST /maintenance/compact for event stream compaction.
"""

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.routing import Route

from ...domain.exceptions import CompactionError, ValidationError
from ..context import get_context
from .serializers import HangarJSONResponse


async def compact_stream(request: Request) -> HangarJSONResponse:
    """Compact an event stream by deleting events preceding the latest snapshot.

    Reads stream_id from JSON request body. Delegates to the event store's
    compact_stream() method, which raises CompactionError if no snapshot exists.

    Request body:
        {"stream_id": "<stream-id>"}

    Returns:
        JSON with {"compacted": {"stream_id": ..., "events_deleted": ...}}

    Raises:
        ValidationError: When stream_id is missing or empty.
        CompactionError: When no snapshot exists for the stream (mapped to 500).
    """
    body = await request.json()
    stream_id: str = body.get("stream_id", "").strip()

    if not stream_id:
        raise ValidationError(
            message="stream_id is required",
            field="stream_id",
        )

    ctx = get_context()
    event_store = ctx.runtime.event_bus.event_store

    try:
        deleted = await run_in_threadpool(event_store.compact_stream, stream_id)
    except CompactionError:
        raise

    return HangarJSONResponse(
        {"compacted": {"stream_id": stream_id, "events_deleted": deleted}},
        status_code=200,
    )


# Route definitions for mounting in the API router
maintenance_routes = [
    Route("/compact", compact_stream, methods=["POST"]),
]
