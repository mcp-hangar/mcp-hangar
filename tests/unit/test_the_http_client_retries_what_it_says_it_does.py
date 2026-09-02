"""`max_retries`, `retry_backoff_factor` and `retry_status_codes` now run.

They were declared on `HttpClientConfig`, validated in the domain value object,
parsed from `http:`, passed through the launcher and documented -- and the only
retry that happened was httpcore's, which retries `ConnectError` and
`ConnectTimeout` alone, on its own hardcoded backoff. A 502 from an upstream
mid-rollout came back to the caller on the first attempt, `retry_status_codes`
had no reader outside its own definition, and `mcp_hangar_http_retries_total`
-- registered, on a shipped Grafana panel, in the docs table -- could not be
incremented from inside a loop the application cannot see (#1163).
"""

from __future__ import annotations

import httpx
import pytest

from mcp_hangar import metrics as prometheus_metrics
from mcp_hangar.http_client import HttpClient, HttpClientConfig

_ENDPOINT = "http://upstream.test:9000/mcp"


def _client(**config) -> HttpClient:
    return HttpClient(
        endpoint=_ENDPOINT,
        mcp_server_id="upstream",
        http_config=HttpClientConfig(retry_backoff_factor=0.0, **config),
    )


def _responses(client: HttpClient, statuses: list[int]) -> list[httpx.Request]:
    """Answer each POST with the next status; record what was sent."""
    seen: list[httpx.Request] = []
    remaining = list(statuses)

    def _post(url, **kwargs):
        request = httpx.Request("POST", url)
        seen.append(request)
        status = remaining.pop(0) if remaining else 200
        return httpx.Response(status, json={"jsonrpc": "2.0", "id": "1", "result": {}}, request=request)

    client._client.post = _post  # type: ignore[method-assign]
    return seen


class TestARetryableStatus:
    def test_a_502_is_retried_up_to_max_retries(self):
        client = _client(max_retries=3)
        seen = _responses(client, [502, 502])

        response = client.call("tools/list", {})

        assert len(seen) == 3
        assert "error" not in response

    @pytest.mark.parametrize("status", [502, 503, 504])
    def test_every_configured_status_is_retried(self, status):
        client = _client(max_retries=2)
        seen = _responses(client, [status])

        client.call("tools/list", {})

        assert len(seen) == 2

    def test_a_status_outside_the_list_is_returned_at_once(self):
        client = _client(max_retries=3)
        seen = _responses(client, [500])

        result = client.call("tools/list", {})

        assert len(seen) == 1
        assert result["error"]["message"] == "HTTP error: 500"

    def test_the_configured_list_is_what_decides(self):
        """`retry_status_codes` had no reader at all before this."""
        client = _client(max_retries=3, retry_status_codes=(429,))
        seen = _responses(client, [429, 502])

        client.call("tools/list", {})

        assert len(seen) == 2, "429 should have been retried, and 502 should not"

    def test_the_last_attempt_is_returned_rather_than_swallowed(self):
        client = _client(max_retries=2)
        seen = _responses(client, [503, 503])

        result = client.call("tools/list", {})

        assert len(seen) == 2
        assert result["error"]["message"] == "HTTP error: 503"

    def test_one_attempt_means_no_retry(self):
        client = _client(max_retries=1)
        seen = _responses(client, [502])

        client.call("tools/list", {})

        assert len(seen) == 1


class TestAConnectFailure:
    def test_it_is_retried_here_now_that_the_transport_does_not(self):
        client = _client(max_retries=3)
        attempts: list[int] = []

        def _post(url, **kwargs):
            attempts.append(1)
            if len(attempts) < 3:
                raise httpx.ConnectError("connection refused")
            request = httpx.Request("POST", url)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": "1", "result": {}}, request=request)

        client._client.post = _post  # type: ignore[method-assign]

        client.call("tools/list", {})

        assert len(attempts) == 3

    def test_the_last_failure_still_reaches_the_caller(self):
        """Exhausting the attempts raises what it always raised."""
        from mcp_hangar.domain.exceptions import ClientError

        client = _client(max_retries=2)
        attempts: list[int] = []

        def _post(url, **kwargs):
            attempts.append(1)
            raise httpx.ConnectError("connection refused")

        client._client.post = _post  # type: ignore[method-assign]

        with pytest.raises(ClientError):
            client.call("tools/list", {})

        assert len(attempts) == 2


class TestTheMetric:
    def test_a_retry_is_counted_with_the_status_as_its_reason(self):
        client = _client(max_retries=2)
        _responses(client, [503])

        client.call("tools/list", {})

        exposition = prometheus_metrics.get_metrics()
        assert 'mcp_hangar_http_retries_total{mcp_server="upstream",retry_reason="503"}' in exposition

    def test_a_connect_failure_is_counted_under_its_own_reason(self):
        client = _client(max_retries=2)
        calls: list[int] = []

        def _post(url, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise httpx.ConnectTimeout("timed out")
            request = httpx.Request("POST", url)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": "1", "result": {}}, request=request)

        client._client.post = _post  # type: ignore[method-assign]

        client.call("tools/list", {})

        assert 'retry_reason="connection_error"' in prometheus_metrics.get_metrics()
