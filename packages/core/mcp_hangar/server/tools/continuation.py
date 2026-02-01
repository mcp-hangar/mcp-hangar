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

        When a batch response exceeds size limits, individual results may be
        truncated and include a continuation_id. Use this tool to retrieve
        the complete content.

        Args:
            continuation_id: The continuation ID from the truncated response.
            offset: Byte offset to start reading from (for paginated retrieval).
            limit: Maximum bytes to retrieve (default: 500KB, max: 2MB).

        Returns:
            Dictionary containing:
            - found: Whether the continuation ID was found
            - data: The response data (complete or partial)
            - total_size_bytes: Total size of the full response
            - offset: Starting offset of returned data
            - has_more: Whether more data is available
            - complete: Whether this retrieval is the complete response

        Raises:
            ValueError: If continuation_id is empty or limit exceeds maximum.

        Example:
            # From a truncated batch response:
            # {"truncated": true, "continuation_id": "cont_abc123_0_f8a2b3c4"}

            # Fetch the full content:
            result = hangar_fetch_continuation("cont_abc123_0_f8a2b3c4")

            # For very large responses, paginate:
            chunk1 = hangar_fetch_continuation("cont_abc123_0_f8a2b3c4", offset=0, limit=500000)
            if chunk1["has_more"]:
                chunk2 = hangar_fetch_continuation(
                    "cont_abc123_0_f8a2b3c4",
                    offset=500000,
                    limit=500000
                )
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

        Manually delete a cached full response before it expires.
        This is optional - cached entries will automatically expire
        based on the configured TTL.

        Args:
            continuation_id: The continuation ID to delete.

        Returns:
            Dictionary containing:
            - deleted: Whether the entry was successfully deleted
            - continuation_id: The ID that was processed
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
