"""Tier 3: the telemetry canaries, on the wire from a real gateway (#1535).

``tests/integration/test_telemetry_canaries.py`` proves the #1276 contract
against the providers the bootstrap builds, read through in-memory exporters
attached beside the real ones. This tier proves the part only the wire can: a
real ``mcp-hangar serve --http`` process, driven over the shipped ``/mcp``
surface, exports through its own OTLP gRPC exporters to the receiver in
``_otlp_receiver``, and every record asserted here crossed the wire as OTLP
protobuf. The receiver keeps each record whole (``raw_spans``, ``raw_logs``), so
a canary in an array attribute, a map or a link's attributes is found too.

The upstreams are the integration suite's: ``_canary_upstream.py`` over stdio,
and the same ``answer()`` served over HTTP from this process. Each sends one
canary per kind of input; every request also carries the baggage and header
canaries, as an HTTP header and in ``_meta``.

One contract, not two: the rules are ``CONTRACT`` and ``Forbidden`` from the
integration suite, read for the sinks this tier captures. Every record of the
run is scanned -- every span and log record the gateway's ``service.instance.id``
exported, not only the ones of a call -- so a canary on a sibling, background or
health span is found as well.

Left to the integration suite, which already reads them from the real
bootstrap: the event store, ``/ws/events``, the compliance exporters, the
structured log, the calling principal (the gateway here runs without auth), the
front door, and the approver and batch-truncation canaries. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live -m "live and t3"
"""

from __future__ import annotations

import json
import sys
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest

from mcp_hangar.observability.tracing import SPAN_ATTRIBUTE_LENGTH_LIMIT
from tests.integration import _canary_upstream as upstream
from tests.integration.test_telemetry_canaries import (
    AUDIT_ATTRIBUTE_LENGTH_LIMIT,
    CONTRACT,
    ENCODED_KINDS,
    Forbidden,
    _strings,
)
from tests.live._otlp_receiver import OtlpReceiver, poll
from tests.live.conftest import running_hangar
from tests.live.test_t3_export import _gateway_env

pytestmark = [pytest.mark.live, pytest.mark.t3, pytest.mark.security]

_ARRIVAL_TIMEOUT_S = 30.0
_MODERN = "2026-07-28"
_ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": _MODERN,
    "io.modelcontextprotocol/clientInfo": {"name": "t3-canaries", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}
_SERVERS = {"stdio": "canary-stdio", "http": "canary-http"}

#: scenario -> the upstream tool. Each runs once per transport.
_SCENARIOS = {"argument": "note", "result": "result_text", "is_error": "is_error", "rpc_error": "rpc_error"}

#: The kinds this tier sends, and the scenario each goes in with. The rest of
#: ``upstream.KINDS`` needs an approver or batch truncation, which the
#: integration suite drives.
_SENT = {
    "argument": "argument",
    "secret_argument": "argument",
    "l7_argument": "argument",
    "l7_header": "argument",
    "l7_header_encoded": "argument",
    "baggage": "argument",
    "header": "argument",
    "result": "result",
    "is_error": "is_error",
    "rpc_error": "rpc_error",
}

#: kind -> the argument key ``note`` sends it under.
_ARGUMENT_KEYS = {
    "argument": "note",
    "secret_argument": "api_token",
    "l7_argument": "ref",
    "l7_header": "region",
    "l7_header_encoded": "zone",
}

_CONFIG = """\
logging:
  level: WARNING
mcp_servers:
  {stdio}:
    mode: subprocess
    command: ["{python}", "{upstream}"]
    env:
      CANARY_UPSTREAM_RECORD: "{record}"
    idle_ttl_s: 120
  {http}:
    mode: remote
    endpoint: "{endpoint}"
"""


class _HttpUpstream(BaseHTTPRequestHandler):
    """``_canary_upstream.answer`` over HTTP, recording what each request carried."""

    seen: ClassVar[list[dict[str, Any]]] = []

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802 -- http.server's handler name
        # No server-to-client stream: 405 is how an upstream says so.
        self.send_response(405)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802 -- http.server's handler name
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        self.seen.append({**upstream.seen(request), "headers": dict(self.headers.items())})
        response = upstream.answer(request, "http")
        body = b"" if response is None else json.dumps(response).encode()
        self.send_response(202 if response is None else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _arguments(tool: str, transport: str) -> dict[str, Any]:
    if tool != "note":
        return {}
    return {key: upstream.canary(kind, transport) for kind, key in _ARGUMENT_KEYS.items()}


def _hangar_call(base_url: str, transport: str, tool: str) -> dict[str, Any]:
    """One stateless ``hangar_call`` carrying the transport's baggage and header canaries; its first result."""
    name = upstream.prefix(transport) + tool
    calls = [{"mcp_server": _SERVERS[transport], "tool": name, "arguments": _arguments(tool, transport)}]
    meta = {**_ENVELOPE, "baggage": f"canary.meta={upstream.canary('baggage', transport)}"}
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "hangar_call", "arguments": {"calls": calls}, "_meta": meta},
    }
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": _MODERN,
        "Mcp-Method": "tools/call",
        "Mcp-Name": "hangar_call",
        "baggage": f"canary.probe={upstream.canary('baggage', transport)}",
        "X-Canary-Probe": upstream.canary("header", transport),
        "Mcp-Param-Region": upstream.canary("l7_header", transport),
        "Mcp-Param-Zone": upstream.encoded("l7_header_encoded", transport),
    }
    response = httpx.post(f"{base_url}/mcp", headers=headers, content=json.dumps(body), timeout=60)
    response.raise_for_status()
    text = response.text.lstrip()
    if not text.startswith("{"):  # SSE framing: take the data line
        text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
    result = json.loads(text)["result"]
    batch = result.get("structuredContent") or json.loads(result["content"][0]["text"])
    first: dict[str, Any] = batch["results"][0]
    return first


@dataclass
class _Run:
    spans: list[dict[str, Any]]
    logs: list[dict[str, Any]]
    calls: dict[str, dict[str, Any]]
    seen: dict[str, list[dict[str, Any]]]


@pytest.fixture(scope="module")
def receiver() -> Iterator[OtlpReceiver]:
    otlp = OtlpReceiver()
    yield otlp
    otlp.stop()


@pytest.fixture(scope="module")
def run(receiver: OtlpReceiver, tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Run]:
    """One gateway, every scenario on both transports, then the records its exporters sent."""
    workdir = tmp_path_factory.mktemp("t3_canaries")
    record = workdir / "stdio-upstream.jsonl"
    _HttpUpstream.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HttpUpstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config = _CONFIG.format(
        stdio=_SERVERS["stdio"],
        http=_SERVERS["http"],
        python=sys.executable,
        upstream=Path(upstream.__file__).resolve(),
        record=record,
        endpoint=f"http://127.0.0.1:{server.server_address[1]}/mcp",
    )
    run_id = f"t3-canaries-{uuid.uuid4()}"
    env = _gateway_env(receiver, run_id, OTEL_BSP_SCHEDULE_DELAY="200", OTEL_BLRP_SCHEDULE_DELAY="200")
    calls: dict[str, dict[str, Any]] = {}
    try:
        with running_hangar(workdir, config, env) as hangar:
            for transport in upstream.TRANSPORTS:
                for scenario, tool in _SCENARIOS.items():
                    calls[f"{transport}:{scenario}"] = _hangar_call(hangar.base_url, transport, tool)
        # The gateway has stopped and flushed (T3 proves the shutdown flush).
        # Wait until a batch.call span of every call is in.
        wanted = {c["call_id"] for c in calls.values()}

        def every_call() -> bool:
            spans = receiver.spans(run_id)
            return wanted <= {s.attributes.get("batch.call.id") for s in spans}

        assert poll(every_call, _ARRIVAL_TIMEOUT_S), "the spans of some calls never arrived"
        stdio_seen = [json.loads(line) for line in record.read_text().splitlines()] if record.exists() else []
        yield _Run(
            spans=receiver.raw_spans(run_id),
            logs=receiver.raw_logs(run_id),
            calls=calls,
            seen={"stdio": stdio_seen, "http": list(_HttpUpstream.seen)},
        )
    finally:
        server.shutdown()


# --- the sinks --------------------------------------------------------------


def _status(span: dict[str, Any]) -> str:
    return str((span.get("status") or {}).get("message", ""))


def _attributes(entries: list[dict[str, Any]] | None) -> dict[str, Any]:
    """OTLP ``KeyValue`` entries as a mapping of key to its JSON value."""
    return {e["key"]: e.get("value") for e in entries or []}


#: sink -> its records in the run, named as the integration suite names them.
SINKS: dict[str, Callable[[_Run], list[Any]]] = {
    "span_attributes": lambda r: [{k: s.get(k) for k in ("name", "attributes", "links")} for s in r.spans],
    "span_status_descriptions": lambda r: [_status(s) for s in r.spans],
    "span_events": lambda r: [e for s in r.spans for e in s.get("events", [])],
    # Every exported log record: the audit records, and anything else on the pipeline.
    "otlp_audit_records": lambda r: r.logs,
}


@pytest.mark.parametrize("transport", upstream.TRANSPORTS)
@pytest.mark.parametrize("kind", sorted(_SENT))
@pytest.mark.parametrize("sink", SINKS)
def test_a_canary_takes_only_the_form_the_contract_allows_on_the_wire(run, sink, kind, transport):
    rule = CONTRACT[sink].get(kind, Forbidden())

    rule.check(SINKS[sink](run), kind, transport)


@pytest.mark.parametrize("sink", SINKS)
@pytest.mark.parametrize("kind", ENCODED_KINDS)
def test_a_sentinel_canary_crosses_the_wire_in_no_form(run, sink, kind):
    for transport in upstream.TRANSPORTS:
        payload = upstream.encoded(kind, transport).removeprefix("=?base64?").removesuffix("?=")
        found = [s[:120] for s in _strings(SINKS[sink](run)) if payload in s]
        assert found == [], f"{len(found)} value(s) carry the sentinel canary: {found[:3]}"


# --- that the matrix means something ----------------------------------------


@pytest.mark.parametrize("sink", SINKS)
def test_the_sink_was_read(run, sink):
    assert SINKS[sink](run), f"{sink} holds nothing, so no canary could be found in it"


def test_every_call_left_an_audit_record(run):
    """The audit pipeline carried every call, so an absence from it is a result."""
    audited = set()
    for record in run.logs:
        attributes = _attributes(record.get("attributes"))
        if record.get("scope") == "mcp_hangar.audit" and _text(attributes, "mcp.event.name") == "tool_invocation":
            audited.add(_text(attributes, "gen_ai.tool.name"))
    expected = {upstream.prefix(t) + tool for t in upstream.TRANSPORTS for tool in _SCENARIOS.values()}

    assert expected <= audited, sorted(expected - audited)


def _text(attributes: dict[str, Any], key: str) -> str | None:
    value = attributes.get(key) or {}
    return value.get("stringValue")


@pytest.mark.parametrize("transport", upstream.TRANSPORTS)
@pytest.mark.parametrize("kind", sorted(_SENT))
def test_the_canary_went_in(run, kind, transport):
    """The value entered on this transport: an absence from the wire is then a result."""
    response = run.calls[f"{transport}:{_SENT[kind]}"]
    if kind in _ARGUMENT_KEYS:
        key = _ARGUMENT_KEYS[kind]
        sent = [(r.get("arguments") or {}).get(key) for r in run.seen[transport] if r["method"] == "tools/call"]
        assert upstream.canary(kind, transport) in sent, sent
    elif kind in ("baggage", "header"):
        # Sent on every request; the argument call reached the upstream.
        assert response.get("success") is True, response
    else:
        # The caller is owed the whole text; only telemetry is bounded.
        value = upstream.canary(kind, transport)
        assert any(value in s for s in _strings(response)), json.dumps(response)[:500]


# --- the bounds, on the wire -------------------------------------------------


def _long(values: Iterator[str], limit: int) -> list[str]:
    return [f"{len(v)}: {v[:80]}" for v in values if len(v) > limit]


def test_no_exported_span_attribute_exceeds_its_bound(run):
    values = (v for s in run.spans for v in _strings(list(_attributes(s.get("attributes")).values())))

    assert _long(values, SPAN_ATTRIBUTE_LENGTH_LIMIT) == []


def test_an_exported_status_description_is_empty_or_its_error_type(run):
    described = [s for s in run.spans if _status(s)]
    error_type = [_attributes(s.get("attributes")).get("error.type", {}).get("stringValue") for s in described]

    assert [(_status(s), t) for s, t in zip(described, error_type, strict=True) if _status(s) != t] == []


def test_an_exported_exception_event_carries_its_type_only(run):
    events = [
        _attributes(e.get("attributes")) for s in run.spans for e in s.get("events", []) if e["name"] == "exception"
    ]

    assert events, "no call failed on the wire, so exception events were not exercised"
    assert [sorted(a) for a in events if set(a) != {"exception.type"}] == []


def test_no_exported_log_attribute_exceeds_the_audit_bound(run):
    values = (v for r in run.logs for v in _strings(list(_attributes(r.get("attributes")).values())))

    assert _long(values, AUDIT_ATTRIBUTE_LENGTH_LIMIT) == []
