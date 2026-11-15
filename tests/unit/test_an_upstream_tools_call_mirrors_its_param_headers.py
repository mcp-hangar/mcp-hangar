"""Hangar, as a client, mirrors a tool's ``x-mcp-header`` arguments as ``Mcp-Param-*`` headers (#2011).

The schema they are built from is the one the upstream's ``tools/list`` served
this client, and it is judged by the rule the batch gates apply (#2253) before
anything is sent, so a path that skipped the gates cannot mirror a tool they
withhold. The headers are read off the request that reached the transport, not
off a dict the client built.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from mcp_hangar import http_client as http_client_module
from mcp_hangar.domain.exceptions import ParamHeaderRefusedError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.policies import header_exposure
from mcp_hangar.domain.policies.header_exposure import HeaderExposurePolicy, adopt_header_exposure_policies
from mcp_hangar.domain.value_objects import McpServerState
from mcp_hangar.http_client import AuthConfig, AuthType, HttpClient, HttpClientConfig
from mcp_hangar.server.tools.batch import executor

#: What importing the batch executor registered, read before any fixture runs.
REGISTERED_OWNERS = header_exposure._OWNERS

SERVER = "upstream"

LOOKUP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "region": {"type": "string", "x-mcp-header": "Region"},
        "priority": {"type": "integer", "x-mcp-header": "Priority"},
        "verbose": {"type": "boolean", "x-mcp-header": "Verbose"},
        "debug": {"type": "boolean", "x-mcp-header": "Debug"},
        "empty": {"type": "string", "x-mcp-header": "EmptyVal"},
        "method": {"type": "string", "x-mcp-header": "Method"},
        "text": {"type": "string", "x-mcp-header": "Text"},
        "ratio": {"type": "number"},
        "query": {"type": "string"},
        "nested": {"type": "object", "properties": {"zone": {"type": "string", "x-mcp-header": "Zone"}}},
    },
}


def _tool(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "inputSchema": schema}


class _Upstream:
    """Records each request and answers like an upstream; `tools` is what `tools/list` serves."""

    def __init__(self, tools: list[dict[str, Any]]) -> None:
        self.tools = tools
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        body = json.loads(request.content)
        result: dict[str, Any] = {"tools": self.tools} if body["method"] == "tools/list" else {}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    def calls(self) -> list[httpx.Request]:
        return [r for r in self.seen if json.loads(r.content)["method"] == "tools/call"]


def _client(upstream: _Upstream, mcp_server_id: str | None = SERVER, **config: Any) -> HttpClient:
    client = HttpClient(
        "http://upstream.test/mcp",
        auth_config=config.pop("auth_config", None),
        http_config=HttpClientConfig(enforce_ssrf=False, max_retries=1, **config),
        mcp_server_id=mcp_server_id,
    )
    defaults = client._client.headers
    client._client.close()
    client._client = httpx.Client(headers=defaults, transport=httpx.MockTransport(upstream))
    return client


def _sentinel(value: str) -> str:
    return f"=?base64?{base64.b64encode(value.encode('utf-8')).decode('ascii')}?="


def _param_headers(request: httpx.Request) -> dict[str, str]:
    return {k.lower(): v for k, v in request.headers.items() if k.lower().startswith("mcp-param-")}


@pytest.fixture(autouse=True)
def no_blocks(monkeypatch: pytest.MonkeyPatch):
    """No `header_exposure` block and no group owners unless a test sets them."""
    saved = dict(header_exposure._POLICIES)
    adopt_header_exposure_policies({}, replace=True)
    monkeypatch.setattr(header_exposure, "_OWNERS", None)
    yield
    adopt_header_exposure_policies(saved, replace=True)


@pytest.fixture
def upstream() -> _Upstream:
    return _Upstream([_tool("lookup", LOOKUP_SCHEMA), _tool("plain", {"type": "object", "properties": {}})])


def _call(client: HttpClient, tool: str, arguments: dict[str, Any]) -> None:
    client.call("tools/call", {"name": tool, "arguments": arguments})


def test_annotated_arguments_are_mirrored_with_the_sdk_encoding(upstream: _Upstream) -> None:
    arguments = {
        "region": "us-west1",
        "priority": 42,
        "verbose": False,
        "debug": True,
        "empty": "",
        "method": "test-method",
        "text": "internal spaces ok",
        "ratio": 3.14,
        "query": "SELECT 1",
        "nested": {"zone": "a"},
    }
    with _client(upstream) as client:
        client.call("tools/list", {})
        _call(client, "lookup", arguments)

    (sent,) = upstream.calls()
    assert _param_headers(sent) == {
        "mcp-param-region": "us-west1",
        "mcp-param-priority": "42",
        "mcp-param-verbose": "false",
        "mcp-param-debug": "true",
        "mcp-param-emptyval": "",
        "mcp-param-method": "test-method",
        "mcp-param-text": "internal spaces ok",
        "mcp-param-zone": "a",
    }
    # `Method` is a parameter, not the standard header; the body is untouched.
    assert sent.headers["Mcp-Method"] == "tools/call"
    assert json.loads(sent.content)["params"]["arguments"] == arguments


@pytest.mark.parametrize(
    "value",
    ["Hello, 世界", " padded ", " lead", "trail ", "line1\nline2", "line1\r\nline2", "\tindented", "=?base64?eA==?="],
)
def test_an_unsafe_value_is_base64_wrapped_and_no_raw_line_holds_cr_or_lf(upstream: _Upstream, value: str) -> None:
    with _client(upstream) as client:
        client.call("tools/list", {})
        _call(client, "lookup", {"region": value})

    (sent,) = upstream.calls()
    assert sent.headers["Mcp-Param-Region"] == _sentinel(value)
    for name, raw in sent.headers.raw:
        assert b"\r" not in name + raw and b"\n" not in name + raw


@pytest.mark.parametrize("arguments", [{"region": None}, {}, {"nested": None}, {"nested": "flat"}])
def test_a_null_or_absent_value_is_omitted(upstream: _Upstream, arguments: dict[str, Any]) -> None:
    with _client(upstream) as client:
        client.call("tools/list", {})
        _call(client, "lookup", {"priority": 1, **arguments})

    (sent,) = upstream.calls()
    assert _param_headers(sent) == {"mcp-param-priority": "1"}
    assert json.loads(sent.content)["params"]["arguments"] == {"priority": 1, **arguments}


def test_a_tool_never_listed_or_unannotated_has_no_param_headers(upstream: _Upstream) -> None:
    with _client(upstream) as client:
        _call(client, "lookup", {"region": "x"})
        client.call("tools/list", {})
        _call(client, "plain", {"region": "x"})
        _call(client, "missing", {"region": "x"})

    assert [_param_headers(r) for r in upstream.calls()] == [{}, {}, {}]


def test_the_schema_is_a_copy_the_caller_cannot_change(upstream: _Upstream) -> None:
    with _client(upstream) as client:
        answer = client.call("tools/list", {})
        answer["result"]["tools"][0]["inputSchema"]["properties"]["query"]["x-mcp-header"] = "Query"
        _call(client, "lookup", {"query": "secret"})

    assert "mcp-param-query" not in _param_headers(upstream.calls()[0])


def test_a_first_page_replaces_the_catalogue_and_a_later_page_adds_to_it(upstream: _Upstream) -> None:
    with _client(upstream) as client:
        client.call("tools/list", {})
        upstream.tools = [
            _tool("other", {"type": "object", "properties": {"a": {"type": "string", "x-mcp-header": "A"}}})
        ]
        client.call("tools/list", {"cursor": "2"})
        _call(client, "lookup", {"region": "r"})
        _call(client, "other", {"a": "1"})
        client.call("tools/list", {})
        _call(client, "lookup", {"region": "r"})

    assert [_param_headers(r) for r in upstream.calls()] == [
        {"mcp-param-region": "r"},
        {"mcp-param-a": "1"},
        {},
    ]


def test_an_answer_without_a_tool_list_leaves_the_catalogue_as_it_was(upstream: _Upstream) -> None:
    with _client(upstream) as client:
        client.call("tools/list", {})
        upstream.tools = "not a list"  # type: ignore[assignment]
        client.call("tools/list", {})
        _call(client, "lookup", {"region": "r"})

    assert _param_headers(upstream.calls()[0]) == {"mcp-param-region": "r"}


# --- What the batch gates refuse, the client refuses too ---------------------


def _assert_refused(upstream: _Upstream, client: HttpClient, reason: str, tool: str = "lookup") -> None:
    with pytest.raises(ParamHeaderRefusedError) as refused:
        _call(client, tool, {"region": "us-west1"})
    assert refused.value.reason == reason
    assert upstream.calls() == []


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {"r": {"type": "number", "x-mcp-header": "R"}}},
        {"type": "object", "properties": {"r": {"type": "string", "x-mcp-header": "Not A Token"}}},
        {
            "type": "object",
            "properties": {
                "a": {"type": "string", "x-mcp-header": "Dup"},
                "b": {"type": "string", "x-mcp-header": "dup"},
            },
        },
        {"type": "object", "anyOf": [{"properties": {"r": {"type": "string", "x-mcp-header": "R"}}}]},
    ],
)
def test_an_invalid_annotation_is_refused_before_anything_is_sent(schema: dict[str, Any]) -> None:
    upstream = _Upstream([_tool("lookup", schema)])
    with _client(upstream) as client:
        client.call("tools/list", {})
        _assert_refused(upstream, client, "invalid_x_mcp_header")


@pytest.mark.parametrize("action", ["withdraw", "refuse_boot"])
def test_a_servers_own_block_that_withholds_refuses_the_call(upstream: _Upstream, action: str) -> None:
    adopt_header_exposure_policies({SERVER: HeaderExposurePolicy(("region",), action)}, replace=True)
    with _client(upstream) as client:
        client.call("tools/list", {})
        _assert_refused(upstream, client, f"header_exposure_{action}")


def test_a_group_block_reaches_its_member(upstream: _Upstream, monkeypatch: pytest.MonkeyPatch) -> None:
    adopt_header_exposure_policies({"pool": HeaderExposurePolicy(("Region",), "withdraw")}, replace=True)
    monkeypatch.setattr(header_exposure, "_OWNERS", lambda server: ("pool",) if server == SERVER else ())
    with _client(upstream) as client:
        client.call("tools/list", {})
        _assert_refused(upstream, client, "header_exposure_withdraw")


def test_the_schema_judged_is_the_one_mirrored_after_a_refresh(upstream: _Upstream) -> None:
    adopt_header_exposure_policies({SERVER: HeaderExposurePolicy(("api_key",), "withdraw")}, replace=True)
    with _client(upstream) as client:
        client.call("tools/list", {})
        _call(client, "lookup", {"region": "r"})
        drifted = {"type": "object", "properties": {"api_key": {"type": "string", "x-mcp-header": "Key"}}}
        upstream.tools = [_tool("lookup", drifted)]
        client.call("tools/list", {})
        with pytest.raises(ParamHeaderRefusedError):
            _call(client, "lookup", {"api_key": "secret"})

    assert len(upstream.calls()) == 1


@pytest.mark.parametrize(
    "policies",
    [
        {SERVER: HeaderExposurePolicy(("region",), "warn")},
        {SERVER: HeaderExposurePolicy(("other",), "withdraw")},
        {"elsewhere": HeaderExposurePolicy(("region",), "warn")},
    ],
)
def test_a_block_that_warns_or_does_not_match_lets_the_headers_through(
    upstream: _Upstream, policies: dict[str, HeaderExposurePolicy], monkeypatch: pytest.MonkeyPatch
) -> None:
    adopt_header_exposure_policies(policies, replace=True)
    monkeypatch.setattr(header_exposure, "_OWNERS", lambda server: ())
    with _client(upstream) as client:
        client.call("tools/list", {})
        _call(client, "lookup", {"region": "r"})

    assert _param_headers(upstream.calls()[0]) == {"mcp-param-region": "r"}


def test_without_group_owners_a_withholding_block_fails_closed(upstream: _Upstream) -> None:
    adopt_header_exposure_policies({"pool": HeaderExposurePolicy(("nothing",), "withdraw")}, replace=True)
    with _client(upstream) as client:
        client.call("tools/list", {})
        _assert_refused(upstream, client, "owners_unknown")


def test_without_a_server_id_a_withholding_block_fails_closed(upstream: _Upstream) -> None:
    adopt_header_exposure_policies({"pool": HeaderExposurePolicy(("nothing",), "withdraw")}, replace=True)
    with _client(upstream, mcp_server_id=None) as client:
        client.call("tools/list", {})
        _assert_refused(upstream, client, "server_unknown")


def test_the_batch_executor_registers_the_group_owners_it_reads() -> None:
    assert REGISTERED_OWNERS is executor._groups_owning


# --- Collisions ---------------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    [
        "Authorization",
        "Host",
        "Content-Length",
        "Content-Type",
        "Mcp-Session-Id",
        "Session-Id",
        "Method",
        "Name",
        "Protocol-Version",
        "Connection",
        "Transfer-Encoding",
        "X-API-Key",
    ],
)
def test_an_annotation_named_like_a_reserved_header_only_ever_adds_a_prefixed_one(token: str) -> None:
    schema = {"type": "object", "properties": {"v": {"type": "string", "x-mcp-header": token}}}
    upstream = _Upstream([_tool("lookup", schema)])
    auth = AuthConfig(auth_type=AuthType.BEARER, bearer_token="real-token")
    with _client(upstream, auth_config=auth) as client:
        client.call("tools/list", {})
        _call(client, "lookup", {"v": "attacker"})

    (sent,) = upstream.calls()
    assert _param_headers(sent) == {f"mcp-param-{token.lower()}": "attacker"}
    assert sent.headers["Authorization"] == "Bearer real-token"
    assert sent.headers["Host"] == "upstream.test"
    assert sent.headers["Mcp-Method"] == "tools/call"
    assert sent.headers["Mcp-Name"] == "lookup"
    assert "attacker" not in [v for k, v in sent.headers.items() if not k.lower().startswith("mcp-param-")]


@pytest.mark.parametrize(
    "config",
    [
        {"auth_config": AuthConfig(auth_type=AuthType.API_KEY, api_key="k", api_key_header="Mcp-Param-Region")},
        {"extra_headers": {"mcp-param-region": "static"}},
    ],
)
def test_a_mirrored_header_that_would_replace_one_the_client_sends_is_refused(
    upstream: _Upstream, config: dict[str, Any]
) -> None:
    with _client(upstream, **config) as client:
        client.call("tools/list", {})
        _assert_refused(upstream, client, "header_collision")


@pytest.mark.parametrize("built", [{"Authorization": "x"}, {"Mcp-Param-Host": "x", "host": "x"}])
def test_the_client_checks_the_names_the_codec_returns(
    upstream: _Upstream, monkeypatch: pytest.MonkeyPatch, built: dict[str, str]
) -> None:
    monkeypatch.setattr(http_client_module, "mcp_param_headers", lambda header_map, arguments: built)
    with _client(upstream) as client:
        client.call("tools/list", {})
        _assert_refused(upstream, client, "header_collision")


# --- The refusal through the aggregate ---------------------------------------


def test_a_refusal_keeps_its_type_and_does_not_count_against_the_server() -> None:
    server = McpServer(mcp_server_id=SERVER, mode="remote", endpoint="http://127.0.0.1:9/mcp")
    server._state = McpServerState.READY
    server._tools.update_from_list([_tool("lookup", LOOKUP_SCHEMA)])
    server._client = MagicMock()
    server._client.is_alive.return_value = True
    server._client.call.side_effect = ParamHeaderRefusedError("lookup", "invalid_x_mcp_header", SERVER)

    with pytest.raises(ParamHeaderRefusedError):
        server.invoke_tool("lookup", {"region": "r"})
    assert server.health.consecutive_failures == 0
