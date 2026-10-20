"""The upstream's TLS key reaches the span and the connection metric (#1942, ADR-030 §6)."""

from __future__ import annotations

from unittest.mock import MagicMock

from mcp_hangar import metrics as prometheus_metrics
from mcp_hangar.http_client import HttpClient, HttpClientConfig
from mcp_hangar.observability.conventions import Upstream
from tests.unit.test_an_upstream_is_pinned_by_its_key import _leaf, _Upstream


def _count(outcome: str, server: str) -> float:
    for line in prometheus_metrics.get_metrics().splitlines():
        if line.startswith("mcp_hangar_upstream_peer_key_connections_total{") and f'outcome="{outcome}"' in line:
            if f'mcp_server="{server}"' in line:
                return float(line.rsplit(" ", 1)[1])
    return 0.0


def _connect(tmp_path, pins_from_upstream: bool, server: str) -> None:
    cert, key, pin = _leaf(tmp_path, f"upstream-{server}")
    upstream = _Upstream(cert, key)
    pins = frozenset({pin}) if pins_from_upstream else frozenset({"sha256:" + "f" * 64})
    client = HttpClient(
        endpoint=f"https://127.0.0.1:{upstream.port}",
        http_config=HttpClientConfig(peer_key_pins=pins, max_retries=0),
        mcp_server_id=server,
    )
    try:
        try:
            client.call("tools/list", {})
        except Exception:  # noqa: BLE001,S110 -- the refused case raises; the count is what is asserted
            pass
    finally:
        client.close()
        upstream.stop()


def test_every_handshake_is_counted_by_outcome_and_reaches_the_exposition(tmp_path) -> None:
    before_verified = _count("verified", "metric-a")
    before_refused = _count("refused", "metric-b")

    _connect(tmp_path, pins_from_upstream=True, server="metric-a")
    _connect(tmp_path, pins_from_upstream=False, server="metric-b")

    assert _count("verified", "metric-a") == before_verified + 1
    assert _count("refused", "metric-b") == before_refused + 1


def test_the_call_span_carries_the_served_key_and_a_plain_call_does_not() -> None:
    client = HttpClient(endpoint="http://upstream:8080", http_config=HttpClientConfig())
    try:
        span = MagicMock()
        sock = MagicMock(observed_peer_spki="sha256:" + "1" * 64)
        response = MagicMock(extensions={"network_stream": MagicMock(get_extra_info=lambda _: sock)})
        client._remember_served_key(response, span)
        span.set_attribute.assert_called_once_with(Upstream.PEER_SPKI, "sha256:" + "1" * 64)

        plain = MagicMock()
        client._remember_served_key(MagicMock(extensions={}), plain)
        plain.set_attribute.assert_not_called()
    finally:
        client.close()
