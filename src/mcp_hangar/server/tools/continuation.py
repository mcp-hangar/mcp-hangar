"""Continuation tool for retrieving full responses from truncated results.

Provides the hangar_fetch_continuation MCP tool that allows clients
to retrieve the complete content when a batch response was truncated.
"""

from mcp.server.fastmcp import FastMCP

from ...logging_config import get_logger
from ..bootstrap.truncation import get_response_cache

logger = get_logger(__name__)

# Default and maximum limits for retrieval
DEFAULT_LIMIT = 500_000  # 500KB default
MAX_LIMIT = 2_000_000  # 2MB max per retrieval


def register_continuation_tools(mcp: FastMCP) -> None:
    """Register continuation-related MCP tools.

    Args:
        mcp: The FastMCP server instance.
    """

    @mcp.tool(name="hangar_fetch_continuation")
    def hangar_fetch_continuation(
        continuation_id: str,
        offset: int = 0,
        limit: int = DEFAULT_LIMIT,
    ) -> dict:
        """Fetch full or remaining content from a truncated batch response.

        CHOOSE THIS when: hangar_call returned truncated result with continuation_id.
        CHOOSE hangar_delete_continuation when: done with data and want to free memory.
        SKIP THIS when: result was not truncated (no continuation_id in response).

        Side effects: None (read-only cache access).

        Args:
            continuation_id: str - ID from truncated response (starts with "cont_")
            offset: int - Byte offset to start reading (default: 0)
            limit: int - Max bytes to retrieve (default: 500000, max: 2000000)

        Returns:
            Success: {
                found: true,
                data: any,
                total_size_bytes: int,
                offset: int,
                has_more: bool,
                complete: bool
            }
            Not found: {found: false, error: str}
            Cache unavailable: {found: false, error: str}

        Example:
            hangar_fetch_continuation("cont_abc123_0_f8a2b3c4")
            # {"found": true, "data": {"result": "full data here"}, "total_size_bytes": 1024,
            #  "offset": 0, "has_more": false, "complete": true}

            hangar_fetch_continuation("cont_abc123_0_f8a2b3c4", offset=500000, limit=500000)
            # {"found": true, "data": ..., "has_more": true, "complete": false, ...}

            hangar_fetch_continuation("cont_expired")
            # {"found": false, "error": "Continuation not found (may have expired)"}

            hangar_fetch_continuation("cont_x")  # when truncation disabled
            # {"found": false, "error": "Truncation cache not available (truncation may be disabled)"}
        """
        if not continuation_id:
            raise ValueError("continuation_id is required")

        if not continuation_id.startswith("cont_"):
            raise ValueError("Invalid continuation_id format (must start with 'cont_')")

        if offset < 0:
            raise ValueError("offset must be non-negative")

        if limit <= 0:
            limit = DEFAULT_LIMIT
        elif limit > MAX_LIMIT:
            logger.warning(
                "continuation_limit_exceeded",
                continuation_id=continuation_id,
                requested_limit=limit,
                max_limit=MAX_LIMIT,
            )
            limit = MAX_LIMIT

        cache = get_response_cache()
        if cache is None:
            logger.warning(
                "continuation_cache_not_available",
                continuation_id=continuation_id,
            )
            return {
                "found": False,
                "error": "Truncation cache not available (truncation may be disabled)",
            }

        result = cache.retrieve(continuation_id, offset=offset, limit=limit)

        if not result.found:
            logger.debug(
                "continuation_not_found",
                continuation_id=continuation_id,
            )
            return {
                "found": False,
                "error": "Continuation not found (may have expired)",
            }

        logger.debug(
            "continuation_retrieved",
            continuation_id=continuation_id,
            offset=offset,
            limit=limit,
            total_size=result.total_size_bytes,
            complete=result.complete,
        )

        return {
            "found": True,
            "data": result.data,
            "total_size_bytes": result.total_size_bytes,
            "offset": result.offset,
            "has_more": result.has_more,
            "complete": result.complete,
        }

    @mcp.tool(name="hangar_delete_continuation")
    def hangar_delete_continuation(continuation_id: str) -> dict:
        """Delete a cached continuation to free resources.

        CHOOSE THIS when: done with continuation data and want to free memory now.
        SKIP THIS for normal use - cached entries auto-expire based on TTL.

        Side effects: Removes the cached response from memory.

        Args:
            continuation_id: str - ID of cached continuation (starts with "cont_")

        Returns:
            Success: {deleted: true, continuation_id: str}
            Not found: {deleted: false, continuation_id: str}
            Cache unavailable: {deleted: false, continuation_id: str, error: str}

        Example:
            hangar_delete_continuation("cont_abc123_0_f8a2b3c4")
            # {"deleted": true, "continuation_id": "cont_abc123_0_f8a2b3c4"}

            hangar_delete_continuation("cont_nonexistent")
            # {"deleted": false, "continuation_id": "cont_nonexistent"}

            hangar_delete_continuation("cont_x")  # when truncation disabled
            # {"deleted": false, "continuation_id": "cont_x", "error": "Truncation cache not available"}
        """
        if not continuation_id:
            raise ValueError("continuation_id is required")

        cache = get_response_cache()
        if cache is None:
            return {
                "deleted": False,
                "continuation_id": continuation_id,
                "error": "Truncation cache not available",
            }

        deleted = cache.delete(continuation_id)

        logger.debug(
            "continuation_delete_requested",
            continuation_id=continuation_id,
            deleted=deleted,
        )

        return {
            "deleted": deleted,
            "continuation_id": continuation_id,
        }
