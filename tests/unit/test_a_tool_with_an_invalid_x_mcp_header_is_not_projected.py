"""A tool a conforming client must drop is not projected either (#1056).

SEP-2243 makes it a client-side MUST: a tool whose ``x-mcp-header``
annotations fail ``find_invalid_x_mcp_header`` is dropped by the client that
receives it. Hangar forwards the upstream definition verbatim -- stripping the
annotation would move the JCS digest over
``{name, description, inputSchema, outputSchema}`` and read as upstream drift
-- so the tool goes away at the projection instead, with a reason.

Driven through the registered front-door handlers against a populated
registry, not by unit-testing the SDK validator.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anyio
import pytest

# See test_tool_metadata_carried.py: importing flat_tool_projection first trips
# a pre-existing import cycle when this file runs alone.
import mcp_hangar.server  # noqa: F401

from mcp_hangar import metrics as prometheus_metrics
from mcp_hangar._sdk_compat import McpError
from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.digest_computation import compute_tool_digest
from mcp_hangar.fastmcp_server import flat_tool_projection

SERVER = "everything"

#: Region mirrored into `Mcp-Param-Region`: the shape the SEP is for.
GOOD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"region": {"type": "string", "x-mcp-header": "Region"}},
}

#: The same annotation on an object property. The SEP permits it only on
#: integer/string/boolean, because anything else has no header serialization.
BAD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"region": {"type": "object", "x-mcp-header": "Region"}},
}


def _tools() -> list[ToolSchema]:
    return [
        ToolSchema(name="routes_by_region", description="fine", input_schema=GOOD_SCHEMA),
        ToolSchema(name="unusable_by_a_client", description="broken", input_schema=BAD_SCHEMA),
    ]


@pytest.fixture()
def registry():
    reg = get_tool_projection_registry()
    reg.build_from_tools(SERVER, _tools())
    # The verdict cache is keyed on (server, tool, digest) and outlives one
    # test; a stale entry would hide a regression in the check itself.
    flat_tool_projection._ANNOTATION_VERDICTS.clear()
    try:
        yield reg
    finally:
        reg.invalidate()
        flat_tool_projection._ANNOTATION_VERDICTS.clear()


@pytest.fixture()
def handlers(monkeypatch):
    """The registered front-door tools/list + tools/call, with policy open."""
    monkeypatch.setattr(
        flat_tool_projection,
        "is_governed_allowed",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "mcp_hangar.server.tools.tool_permissions.management_tools_for",
        lambda _ctx: set(),
    )
    registered: dict[str, Any] = {}

    class _Low:
        def add_request_handler(self, method, params_type, handler):
            registered[method] = handler

    flat_tool_projection.register_flat_tool_handlers(SimpleNamespace(_mcp_server=_Low()))
    return registered


def _ctx(envelope: bytes) -> SimpleNamespace:
    request = SimpleNamespace(state=SimpleNamespace(), _body=envelope, headers={})
    return SimpleNamespace(request=request)


def _list(handlers) -> Any:
    ctx = _ctx(b'{"jsonrpc":"2.0","method":"tools/list","params":{}}')
    return anyio.run(lambda: handlers["tools/list"](ctx, SimpleNamespace()))


def _governed_observed() -> float:
    """The VALUE observed, not the number of observations: how many tools we handed out."""
    _, sums, _ = prometheus_metrics.PROJECTED_TOOLS.collect()
    return next((s.value for s in sums if s.labels.get("kind") == "governed"), 0.0)


def _withdrawals() -> float:
    return sum(
        s.value
        for s in prometheus_metrics.PROJECTION_WITHDRAWALS_TOTAL.collect()
        if s.labels.get("reason") == "invalid_x_mcp_header"
    )


class TestTheProjection:
    def test_the_valid_annotation_is_served_and_the_invalid_one_is_not(self, registry, handlers) -> None:
        names = {tool.name for tool in _list(handlers).tools}

        assert "routes_by_region" in names
        assert "unusable_by_a_client" not in names

    def test_the_withheld_tool_is_not_counted_as_surface_we_delivered(self, registry, handlers) -> None:
        before = _governed_observed()

        result = _list(handlers)

        # One governed tool, not two: the withheld one never entered the map.
        assert _governed_observed() == before + 1
        assert len(result.tools) == 1

    def test_the_served_definition_still_digests_to_the_upstreams(self, registry, handlers) -> None:
        """Nothing is stripped, so a pin taken upstream still matches."""
        served = next(tool for tool in _list(handlers).tools if tool.name == "routes_by_region")

        payload = served.model_dump(mode="json", by_alias=True, exclude_none=True)
        upstream = {"name": "routes_by_region", "description": "fine", "inputSchema": GOOD_SCHEMA}
        assert payload["inputSchema"] == GOOD_SCHEMA
        assert compute_tool_digest(payload) == compute_tool_digest(upstream)


class TestTheWithdrawalIsObservable:
    def test_a_metric_counts_it(self, registry, handlers) -> None:
        before = _withdrawals()

        _list(handlers)

        assert _withdrawals() == before + 1

    def test_a_second_listing_does_not_count_it_again(self, registry, handlers) -> None:
        """The verdict depends on the schema, not on traffic (#1049)."""
        _list(handlers)
        after_first = _withdrawals()

        _list(handlers)

        assert _withdrawals() == after_first

    def test_a_log_line_names_the_tool_and_the_reason(self, registry, handlers, caplog) -> None:
        with caplog.at_level("WARNING"):
            _list(handlers)

        line = next(r.getMessage() for r in caplog.records if "tool_withheld_invalid_x_mcp_header" in r.getMessage())
        assert "unusable_by_a_client" in line
        assert "integer/string/boolean" in line


class TestShownEqualsCallable:
    def test_calling_the_withheld_tool_is_method_not_found(self, registry, handlers) -> None:
        ctx = _ctx(b'{"jsonrpc":"2.0","method":"tools/call","params":{"name":"unusable_by_a_client"}}')
        params = SimpleNamespace(name="unusable_by_a_client", arguments={})

        with pytest.raises(McpError) as excinfo:
            anyio.run(lambda: handlers["tools/call"](ctx, params))

        assert "not found" in str(excinfo.value).lower()
