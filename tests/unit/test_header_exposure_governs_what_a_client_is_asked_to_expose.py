"""`header_exposure`: which parameters an upstream may oblige a client to expose (#1057).

SEP-2243's only defence against annotating a secret is a SHOULD NOT. An upstream
that annotates `api_key` with `x-mcp-header` obliges every conforming client to
send the key as an HTTP header, where every intermediary on the path can read
it. #1056 validates the syntax of those annotations; this is the semantics.

The schema is never edited -- the digest is JCS over
`{name, description, inputSchema, outputSchema}`, so a strip would move every
pin -- so the tool is withheld, or merely reported, instead.
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
from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry
from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.policies.header_exposure import (
    clear_header_exposure_policies,
    HeaderExposurePolicy,
    set_header_exposure_policy,
)
from mcp_hangar.domain.services.digest_computation import compute_tool_digest
from mcp_hangar.fastmcp_server import flat_tool_projection

SERVER = "everything"

#: The parameter name is innocent; the header it is sent as is not.
TOKEN_IN_HEADER: dict[str, Any] = {
    "type": "object",
    "properties": {"credential": {"type": "string", "x-mcp-header": "X-Auth-Token"}},
}

#: The other way round: the header is innocent, the property is not.
API_KEY_PROPERTY: dict[str, Any] = {
    "type": "object",
    "properties": {"api_key": {"type": "string", "x-mcp-header": "X-Key"}},
}

INNOCENT: dict[str, Any] = {
    "type": "object",
    "properties": {"region": {"type": "string", "x-mcp-header": "Region"}},
}


def _tools() -> list[ToolSchema]:
    return [
        ToolSchema(name="routes_by_region", description="fine", input_schema=INNOCENT),
        ToolSchema(name="leaks_a_token", description="asks for a secret", input_schema=TOKEN_IN_HEADER),
    ]


@pytest.fixture()
def registry():
    reg = get_tool_projection_registry()
    reg.build_from_tools(SERVER, _tools())
    flat_tool_projection._EXPOSURE_VERDICTS.clear()
    flat_tool_projection._ANNOTATION_VERDICTS.clear()
    try:
        yield reg
    finally:
        reg.invalidate()
        clear_header_exposure_policies()
        flat_tool_projection._EXPOSURE_VERDICTS.clear()
        flat_tool_projection._ANNOTATION_VERDICTS.clear()


@pytest.fixture()
def handlers(monkeypatch):
    monkeypatch.setattr(flat_tool_projection, "is_governed_allowed", lambda *a, **k: True)
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


def _list(handlers) -> Any:
    ctx = SimpleNamespace(
        request=SimpleNamespace(
            state=SimpleNamespace(),
            _body=b'{"jsonrpc":"2.0","method":"tools/list","params":{}}',
            headers={},
        )
    )
    return anyio.run(lambda: handlers["tools/list"](ctx, SimpleNamespace()))


def _withdrawals(reason: str) -> float:
    return sum(
        s.value for s in prometheus_metrics.PROJECTION_WITHDRAWALS_TOTAL.collect() if s.labels.get("reason") == reason
    )


SECRETY = ("*token*", "*secret*", "*password*", "api_key", "*_key")


class TestTheBlockIsParsedStrictly:
    def test_the_default_action_is_warn(self) -> None:
        policy = HeaderExposurePolicy.from_config({"deny_annotated": ["*token*"]})

        assert policy is not None
        assert policy.on_violation == "warn"

    def test_an_unknown_action_is_refused_at_parse(self) -> None:
        """A typo would otherwise resolve to the default and report a control
        as on while the action the author asked for never happens."""
        with pytest.raises(ValueError, match="invalid header_exposure.on_violation"):
            HeaderExposurePolicy.from_config({"deny_annotated": ["*"], "on_violation": "withdrawn"})

    def test_a_non_list_pattern_set_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must be a list of strings"):
            HeaderExposurePolicy.from_config({"deny_annotated": "*token*"})

    def test_a_non_mapping_block_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must be a mapping"):
            HeaderExposurePolicy.from_config(["*token*"])

    def test_an_absent_block_is_no_policy(self) -> None:
        assert HeaderExposurePolicy.from_config(None) is None


class TestWhatCounts:
    def test_the_annotation_token_is_matched(self) -> None:
        policy = HeaderExposurePolicy(deny_annotated=SECRETY)

        assert policy.violation(TOKEN_IN_HEADER) is not None

    def test_the_property_path_is_matched(self) -> None:
        """`api_key` sent as `X-Key` is the same exposure under another name."""
        policy = HeaderExposurePolicy(deny_annotated=SECRETY)

        assert policy.violation(API_KEY_PROPERTY) is not None

    def test_matching_is_case_insensitive(self) -> None:
        policy = HeaderExposurePolicy(deny_annotated=("*TOKEN*",))

        assert policy.violation(TOKEN_IN_HEADER) is not None

    def test_a_nested_property_is_reached_by_its_path(self) -> None:
        schema = {
            "type": "object",
            "properties": {"auth": {"type": "object", "properties": dict(API_KEY_PROPERTY["properties"])}},
        }

        assert HeaderExposurePolicy(deny_annotated=("auth.api_key",)).violation(schema) is not None

    def test_an_unannotated_secret_is_not_an_exposure(self) -> None:
        """This block governs what leaves in a HEADER, not what a tool accepts."""
        schema = {"type": "object", "properties": {"api_key": {"type": "string"}}}

        assert HeaderExposurePolicy(deny_annotated=SECRETY).violation(schema) is None

    def test_an_innocent_annotation_is_left_alone(self) -> None:
        assert HeaderExposurePolicy(deny_annotated=SECRETY).violation(INNOCENT) is None

    def test_an_empty_pattern_set_denies_nothing(self) -> None:
        assert HeaderExposurePolicy().violation(TOKEN_IN_HEADER) is None


class TestTheProjection:
    def test_withdraw_removes_the_tool(self, registry, handlers) -> None:
        set_header_exposure_policy(SERVER, HeaderExposurePolicy(SECRETY, "withdraw"))
        before = _withdrawals("header_exposure_withdraw")

        names = {tool.name for tool in _list(handlers).tools}

        assert names == {"routes_by_region"}
        assert _withdrawals("header_exposure_withdraw") == before + 1

    def test_warn_keeps_the_tool_and_still_says_so(self, registry, handlers, caplog) -> None:
        """The default. Adopting the block changes nobody's surface."""
        set_header_exposure_policy(SERVER, HeaderExposurePolicy(SECRETY, "warn"))
        before = _withdrawals("header_exposure_warn")

        with caplog.at_level("WARNING"):
            names = {tool.name for tool in _list(handlers).tools}

        assert "leaks_a_token" in names
        assert _withdrawals("header_exposure_warn") == before + 1
        line = next(r.getMessage() for r in caplog.records if "tool_header_exposure_denied" in r.getMessage())
        assert "leaks_a_token" in line and "X-Auth-Token" in line

    def test_refuse_boot_refuses_to_serve_the_catalogue(self, registry, handlers) -> None:
        set_header_exposure_policy(SERVER, HeaderExposurePolicy(SECRETY, "refuse_boot"))

        with pytest.raises(ConfigurationError, match="refuses to serve"):
            _list(handlers)

    def test_no_block_means_no_change(self, registry, handlers) -> None:
        names = {tool.name for tool in _list(handlers).tools}

        assert names == {"routes_by_region", "leaks_a_token"}

    def test_a_member_inherits_its_group_block(self, registry, handlers, monkeypatch) -> None:
        """A group is a governed scope; a block declared there must not be dead
        config just because the projection iterates members (#1038)."""
        monkeypatch.setattr(flat_tool_projection, "_member_to_group", lambda: {SERVER: "the-group"})
        set_header_exposure_policy("the-group", HeaderExposurePolicy(SECRETY, "withdraw"))

        names = {tool.name for tool in _list(handlers).tools}

        assert names == {"routes_by_region"}


class TestTheSchemaIsNeverEdited:
    def test_a_warned_tool_is_served_byte_identical(self, registry, handlers) -> None:
        """Stripping the annotation would move the JCS digest and read as
        upstream drift to every pin."""
        set_header_exposure_policy(SERVER, HeaderExposurePolicy(SECRETY, "warn"))

        served = next(tool for tool in _list(handlers).tools if tool.name == "leaks_a_token")

        payload = served.model_dump(mode="json", by_alias=True, exclude_none=True)
        assert payload["inputSchema"] == TOKEN_IN_HEADER
        upstream = {"name": "leaks_a_token", "description": "asks for a secret", "inputSchema": TOKEN_IN_HEADER}
        assert compute_tool_digest(payload) == compute_tool_digest(upstream)


class TestTheOverlayIsClearedOnReload:
    def test_clearing_restores_the_tool(self, registry, handlers) -> None:
        set_header_exposure_policy(SERVER, HeaderExposurePolicy(SECRETY, "withdraw"))
        assert "leaks_a_token" not in {t.name for t in _list(handlers).tools}

        clear_header_exposure_policies()

        assert "leaks_a_token" in {t.name for t in _list(handlers).tools}
