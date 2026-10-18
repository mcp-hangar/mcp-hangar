"""A projected list says how long it may be cached, where the spec puts it (#1897, SEP-2549).

``cacheScope`` and ``ttlMs`` are top-level fields of a cacheable list result. Hangar
used to put them in ``_meta``, where no client or cache looks, so the client saw the
SDK defaults (``ttlMs`` 0) instead of Hangar's.
"""

from __future__ import annotations

from mcp.types import (
    ListPromptsResult,
    ListResourcesResult,
    ListResourceTemplatesResult,
    ListToolsResult,
)

from mcp_hangar.fastmcp_server.flat_tool_projection import (
    PROJECTED_LIST_CACHE_SCOPE,
    PROJECTED_LIST_CACHE_TTL_MS,
    projected_list_cache_hints,
)


def test_a_projected_list_is_private_and_has_a_positive_ttl() -> None:
    hints = projected_list_cache_hints()

    assert hints == {"cache_scope": "private", "ttl_ms": PROJECTED_LIST_CACHE_TTL_MS}
    assert PROJECTED_LIST_CACHE_SCOPE == "private"
    assert PROJECTED_LIST_CACHE_TTL_MS > 0


def test_the_hints_reach_the_wire_at_the_top_level_of_every_list_result() -> None:
    for result in (
        ListToolsResult(tools=[], **projected_list_cache_hints()),
        ListPromptsResult(prompts=[], **projected_list_cache_hints()),
        ListResourcesResult(resources=[], **projected_list_cache_hints()),
        ListResourceTemplatesResult(resource_templates=[], **projected_list_cache_hints()),
    ):
        wire = result.model_dump(by_alias=True, exclude_none=True)

        assert wire["cacheScope"] == "private"
        assert wire["ttlMs"] == PROJECTED_LIST_CACHE_TTL_MS
        assert "_meta" not in wire
