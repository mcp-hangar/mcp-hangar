"""A projected tool's ``Mcp-Param-*`` headers are compared with its body on the served app (#1791).

Over the real streamable-HTTP transport (``_front_door_harness``), a front-door
call to a tool whose ``region`` declares ``x-mcp-header: Region`` sends a
header that disagrees with the body. The SDK must refuse it before dispatch,
with a plain ``400`` and no call to the upstream.

mcp 2.0.0 got the tool's schema by running the ``tools/list`` handler. 2.3.0
asks ``Server.get_tool_input_schema`` instead, which answers only for the tools
its own tool manager holds -- never a projected upstream tool -- so it skipped
the check, the header an L7 rule matches on was never compared with the body,
and a call the rule denies reached its upstream. The hook installed for it makes
the same test pass on both SDKs.

Naming: neutral placeholders only (read_item, tenant:a).
"""

from __future__ import annotations

from typing import Any, ClassVar

from tests.integration._front_door_harness import MODERN, TENANT_A, Upstream, front_door

TOOL = "read_item"


class _RegionUpstream(Upstream):
    properties: ClassVar[dict[str, Any]] = {"region": {"type": "string", "x-mcp-header": "Region"}}


def test_a_header_that_disagrees_with_the_body_is_refused_before_the_upstream() -> None:
    with front_door((TOOL,), topology="front_door", upstream_class=_RegionUpstream) as door:
        response = door.call(
            TENANT_A, TOOL, {"region": "eu-west-1"}, era=MODERN, extra_headers={"Mcp-Param-Region": "us-east-1"}
        )

        assert response.status_code == 400, response.text[:300]
        assert door.upstream.called == [], "the call reached its upstream with a header nobody compared"


def test_a_header_that_agrees_is_served() -> None:
    with front_door((TOOL,), topology="front_door", upstream_class=_RegionUpstream) as door:
        response = door.call(
            TENANT_A, TOOL, {"region": "eu-west-1"}, era=MODERN, extra_headers={"Mcp-Param-Region": "eu-west-1"}
        )

        assert response.status_code == 200, response.text[:300]
        assert door.upstream.called == [TOOL]


def test_the_lookup_answers_for_a_projected_tool_and_not_for_a_stranger() -> None:
    from mcp_hangar.fastmcp_server.flat_tool_projection import projected_input_schema

    with front_door((TOOL,), topology="front_door", upstream_class=_RegionUpstream):
        schema = projected_input_schema(TOOL)
        assert schema is not None
        assert schema["properties"]["region"]["x-mcp-header"] == "Region"
        assert projected_input_schema("no_such_tool") is None
