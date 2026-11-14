"""Hangar, as a client, sends the SEP-2243 standard headers on every POST to an upstream (#2011).

``Mcp-Method`` mirrors the body's ``method``; ``Mcp-Name`` its ``params.name``
(``tools/call``, ``prompts/get``) or ``params.uri`` (``resources/read``). The
headers are read off the request that reached the transport, not off a dict the
client built, so a merge that lost or overrode one would show here.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from mcp_hangar.domain.exceptions import ClientError
from mcp_hangar.http_client import AuthConfig, AuthType, HttpClient, HttpClientConfig


def _client(seen: list[httpx.Request], **config: Any) -> HttpClient:
    """An HttpClient whose pool records each request and answers it like an upstream would."""

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        if "id" not in body:
            return httpx.Response(202)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {}})

    client = HttpClient(
        "http://upstream.test/mcp",
        auth_config=config.pop("auth_config", None),
        http_config=HttpClientConfig(enforce_ssrf=False, max_retries=1, **config),
    )
    defaults = client._client.headers
    client._client.close()
    client._client = httpx.Client(headers=defaults, transport=httpx.MockTransport(answer))
    return client


def _sentinel(value: str) -> str:
    return f"=?base64?{base64.b64encode(value.encode('utf-8')).decode('ascii')}?="


@pytest.fixture
def seen() -> list[httpx.Request]:
    return []


@pytest.mark.parametrize("method", ["initialize", "tools/list", "server/discover", "ping"])
def test_every_request_names_its_method(seen: list[httpx.Request], method: str) -> None:
    with _client(seen) as client:
        client.call(method, {})

    assert seen[0].headers["Mcp-Method"] == method
    assert "Mcp-Name" not in seen[0].headers


def test_a_notification_names_its_method(seen: list[httpx.Request]) -> None:
    with _client(seen) as client:
        client.notify("notifications/initialized")

    assert seen[0].headers["Mcp-Method"] == "notifications/initialized"


@pytest.mark.parametrize(
    ("method", "params", "name"),
    [
        ("tools/call", {"name": "get_weather", "arguments": {"city": "x"}}, "get_weather"),
        ("tools/call", {"name": "my-hyphenated-tool", "arguments": {}}, "my-hyphenated-tool"),
        ("prompts/get", {"name": "summarise"}, "summarise"),
        ("resources/read", {"uri": "file:///srv/data/config.json"}, "file:///srv/data/config.json"),
    ],
)
def test_a_named_request_mirrors_its_name(
    seen: list[httpx.Request], method: str, params: dict[str, Any], name: str
) -> None:
    with _client(seen) as client:
        client.call(method, params)

    assert seen[0].headers["Mcp-Method"] == method
    assert seen[0].headers["Mcp-Name"] == name
    assert json.loads(seen[0].content)["params"][next(k for k in ("name", "uri") if k in params)] == name


@pytest.mark.parametrize(
    "name",
    [
        "evil\r\nAuthorization: Bearer stolen",
        "line1\nline2",
        "Hello, 世界",
        " padded ",
        "\ttab",
        "=?base64?literal?=",
    ],
)
def test_an_unsafe_name_is_sent_base64_wrapped(seen: list[httpx.Request], name: str) -> None:
    """A CR or LF in a caller-supplied name never reaches a header raw: no injected header line."""
    with _client(seen, auth_config=AuthConfig(auth_type=AuthType.BEARER, bearer_token="configured")) as client:
        client.call("resources/read", {"uri": name})

    request = seen[0]
    assert request.headers["Mcp-Name"] == _sentinel(name)
    assert request.headers.get_list("Authorization") == ["Bearer configured"]
    for key, value in request.headers.raw:
        assert b"\r" not in key + value and b"\n" not in key + value


def test_a_name_that_is_not_a_string_is_left_out(seen: list[httpx.Request]) -> None:
    with _client(seen) as client:
        client.call("tools/call", {"name": 7, "arguments": {}})

    assert seen[0].headers["Mcp-Method"] == "tools/call"
    assert "Mcp-Name" not in seen[0].headers


def test_the_body_wins_over_an_operator_header_of_the_same_name(seen: list[httpx.Request]) -> None:
    """``extra_headers`` cannot make the standard headers disagree with the body."""
    extra = {"Mcp-Method": "tools/list", "mcp-name": "other_tool", "X-Tenant": "kept"}
    with _client(seen, extra_headers=extra) as client:
        client.call("tools/call", {"name": "real_tool", "arguments": {}})

    request = seen[0]
    assert request.headers.get_list("Mcp-Method") == ["tools/call"]
    assert request.headers.get_list("Mcp-Name") == ["real_tool"]
    assert request.headers["X-Tenant"] == "kept"


def test_auth_and_session_headers_are_untouched(seen: list[httpx.Request]) -> None:
    auth = AuthConfig(auth_type=AuthType.API_KEY, api_key="k-1", api_key_header="X-API-Key")
    with _client(seen, auth_config=auth) as client:
        client._mcp_session_id = "session-1"
        client.call("tools/call", {"name": "Authorization", "arguments": {}})

    request = seen[0]
    assert request.headers["X-API-Key"] == "k-1"
    assert request.headers["Mcp-Session-Id"] == "session-1"
    assert request.headers["Mcp-Name"] == "Authorization"
    assert "Authorization" not in request.headers


def test_a_method_with_no_header_form_is_refused_unsent(seen: list[httpx.Request]) -> None:
    with _client(seen) as client, pytest.raises(ClientError):
        client.call("tools/call\r\nX-Injected: 1", {})

    assert seen == []
