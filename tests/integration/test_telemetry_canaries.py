"""Synthetic canaries reach a sink only where the telemetry data contract allows (#1535).

The contract is #1276's: telemetry carries bounded classifications, the caller
and the event store carry text. This suite proves it against real exporters
rather than restating it: ``_canary_harness.py`` bootstraps Hangar, serves the
app ``serve --http`` serves, and drives one synthetic value per kind of input
through it -- tool arguments, result text, ``isError`` text, an upstream's
JSON-RPC error, inbound baggage and headers, an approver's reason -- over a
stdio and an HTTP upstream, on both tool surfaces (``hangar_call`` and the front
door's flat tools). Each run reads back the sinks ``SINKS`` lists.

The upstream's error text and ``isError`` text also stand for exception
messages: Hangar raises both as a ``ToolInvocationError`` carrying the text, so
every span that records the exception is handed a message with a canary in it.

``CONTRACT`` is the matrix: for each sink, the form each kind of canary may take
there. Anything it does not name is forbidden, which is the contract's own
allowlist rule. A sink or a kind added later is a row or a column here; the
parametrisation picks it up.

Before any cell is believed, ``test_the_canary_went_in`` proves the value
entered the system on that surface and transport, and ``test_the_sink_was_read``
that the sink is not trivially empty: an absence proves nothing about a value
never sent or a sink never captured.

Not covered here yet, and tracked on #1535: the live-gateway tier with a real
OTLP receiver (#1293, ``tests/live``), exceptions raised on other paths (a
transport failure, a validation error), the Langfuse route, metric labels and
the rest of the identifier table. Of that table, the calling principal is a
canary here too: ``PRINCIPAL_CONTRACT`` keeps it off every span by default
(#1580).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from mcp_hangar.domain.events.base import EVENT_TEXT_LENGTH_LIMIT, FREE_TEXT_FIELDS
from mcp_hangar.logging_config import LOG_FIELD_LENGTH_LIMIT
from mcp_hangar.observability.tracing import SPAN_ATTRIBUTE_LENGTH_LIMIT

from ._canary_upstream import KINDS, PRINCIPAL, TRANSPORTS, canary, head, tail

pytestmark = [pytest.mark.otel_sdk, pytest.mark.security]

HARNESS = Path(__file__).with_name("_canary_harness.py")
SURFACES = ("hangar_call", "front_door")

#: The audit exporter's bound on a record attribute (#1343).
AUDIT_ATTRIBUTE_LENGTH_LIMIT = 256

#: kind -> the harness scenario that injects it.
SCENARIO = {
    "argument": "argument",
    "secret_argument": "argument",
    "result": "result",
    "is_error": "is_error",
    "rpc_error": "rpc_error",
    "baggage": "argument",
    "header": "argument",
    "approver_reason": "approval",
}


def _unreachable_endpoint() -> str:
    """A loopback port nothing listens on: exports fail fast and stay local."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{probe.getsockname()[1]}"


def _run(surface: str, tmp: Path) -> dict[str, Any]:
    directory = tmp / surface
    directory.mkdir()
    out = directory / "run.json"
    # The limits are read from the environment; the suite checks the defaults.
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("OTEL_", "MCP_TRACING_", "MCP_COMPLIANCE_")) and not k.endswith("_LENGTH_LIMIT")
    }
    env.update({"MCP_COMPLIANCE_FORMAT": "cef", "MCP_COMPLIANCE_OUTPUT": str(directory / "cef.log")})
    # Under the 60s pytest-timeout the integration job applies.
    result = subprocess.run(
        [sys.executable, str(HARNESS), surface, str(out), _unreachable_endpoint()],
        capture_output=True,
        text=True,
        timeout=45,
        env=env,
    )
    assert result.returncode == 0 and out.exists(), f"harness exited {result.returncode}:\n{result.stderr[-4000:]}"
    return json.loads(out.read_text())


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    tmp = tmp_path_factory.mktemp("canaries")
    with ThreadPoolExecutor(max_workers=len(SURFACES)) as pool:
        pending = {surface: pool.submit(_run, surface, tmp) for surface in SURFACES}
        return {surface: future.result() for surface, future in pending.items()}


# --- the sinks --------------------------------------------------------------


def _logs(run: dict[str, Any]) -> list[Any]:
    """The structured log, one record per line; a line that is not JSON is kept as text."""
    records: list[Any] = []
    for line in run["logs"]:
        try:
            records.append(json.loads(line))
        except ValueError:
            records.append(line)
    return records


def _carriers(run: dict[str, Any]) -> list[Any]:
    """What crossed to the upstreams besides the call itself: ``_meta``, and the HTTP headers."""
    seen = run["upstream_seen"]
    return [r["meta"] for r in seen["stdio"]] + [{"meta": r["meta"], "headers": r["headers"]} for r in seen["http"]]


#: sink -> its records in one run. Route numbers are #1276's.
SINKS: dict[str, Callable[[dict[str, Any]], list[Any]]] = {
    # R1: every exported span's name, attributes and links.
    "span_attributes": lambda run: [{k: s[k] for k in ("name", "attributes", "links")} for s in run["spans"]],
    # R2.
    "span_status_descriptions": lambda run: [s["status_description"] for s in run["spans"]],
    # R3, and the governance events on the same spans.
    "span_events": lambda run: [e for s in run["spans"] for e in s["events"]],
    # R4: outbound to both upstreams.
    "upstream_carriers": _carriers,
    # R6.
    "otlp_audit_records": lambda run: run["audit"],
    "compliance_cef": lambda run: run["compliance"]["cef"],
    "compliance_leef": lambda run: run["compliance"]["leef"],
    "compliance_jsonlines": lambda run: run["compliance"]["jsonlines"],
    "compliance_syslog": lambda run: run["compliance"]["syslog"],
    # R7, at INFO: the level `serve` defaults to.
    "structured_logs": _logs,
    # R8.
    "ws_events": lambda run: run["ws_events"],
    "event_store": lambda run: run["event_store"],
}


def _strings(value: Any) -> Iterator[str]:
    """Every string in a JSON value, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _mappings(value: Any) -> Iterator[dict[str, Any]]:
    """Every mapping in a JSON value."""
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _mappings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _mappings(item)


# --- the forms a canary may take ---------------------------------------------


@dataclass(frozen=True)
class Forbidden:
    """The contract does not name this input for this sink: no trace of it, cut or whole."""

    def check(self, records: list[Any], kind: str, transport: str) -> None:
        found = [s[:120] for s in _strings(records) if head(kind, transport) in s]
        assert found == [], f"{len(found)} value(s) carry the canary: {found[:3]}"


@dataclass(frozen=True)
class Bounded:
    """Kept here by design, but only cut to ``limit`` characters: never the whole long value."""

    limit: int

    def check(self, records: list[Any], kind: str, transport: str) -> None:
        carried = [s for s in _strings(records) if head(kind, transport) in s]
        assert carried, "the sink is meant to keep this input, and holds no trace of it"
        uncut = tail(kind, transport)
        for value in carried:
            assert len(value) <= self.limit, f"{len(value)} characters, over the bound of {self.limit}"
            assert uncut is None or uncut not in value, "the whole value, not a bounded copy"


@dataclass(frozen=True)
class Kept:
    """Kept here as sent: a caller's own argument, persisted after redaction by key and shape."""

    def check(self, records: list[Any], kind: str, transport: str) -> None:
        assert canary(kind, transport) in set(_strings(records)), "the sink is meant to keep this input"


@dataclass(frozen=True)
class Redacted:
    """Kept here only as the redaction marker, under ``key``: present as redacted, not merely absent."""

    key: str
    marker: str = "[REDACTED]"

    def check(self, records: list[Any], kind: str, transport: str) -> None:
        Forbidden().check(records, kind, transport)
        # The records of this call: the ones holding the ordinary argument sent beside it.
        beside = canary("argument", transport)
        ours = [m for m in _mappings(records) if self.key in m and beside in m.values()]
        assert ours, f"no record of the call keeps its arguments, so nothing shows {self.key!r} redacted"
        assert {m[self.key] for m in ours} == {self.marker}, [m[self.key] for m in ours]


Rule = Forbidden | Bounded | Kept | Redacted

#: What #1276 allows each sink to carry; every cell it leaves out is ``Forbidden``.
#: The event store and ``/ws/events`` are the one different trust boundary: they
#: stay in the deployment behind ``audit:read``, keep the caller's (redacted)
#: arguments, and keep upstream and approver text bounded at event creation.
_RETAINED: dict[str, Rule] = {
    "argument": Kept(),
    "secret_argument": Redacted("api_token"),
    "is_error": Bounded(EVENT_TEXT_LENGTH_LIMIT),
    "rpc_error": Bounded(EVENT_TEXT_LENGTH_LIMIT),
    "approver_reason": Bounded(EVENT_TEXT_LENGTH_LIMIT),
}
CONTRACT: dict[str, dict[str, Rule]] = {sink: {} for sink in SINKS}
CONTRACT["event_store"] = dict(_RETAINED)
CONTRACT["ws_events"] = dict(_RETAINED)

#: Cells tracked outside this suite. Each is a strict xfail, so the change that
#: resolves one has to delete its entry here.
TRACKED_SEPARATELY: frozenset[tuple[str, str]] = frozenset()


def _cells() -> Iterator[Any]:
    for surface in SURFACES:
        for sink in SINKS:
            for kind in KINDS:
                marks = []
                if (sink, kind) in TRACKED_SEPARATELY:
                    marks.append(pytest.mark.xfail(strict=True, reason="tracked separately"))
                for transport in TRANSPORTS:
                    yield pytest.param(
                        surface, sink, kind, transport, marks=marks, id=f"{surface}-{sink}-{kind}-{transport}"
                    )


@pytest.mark.parametrize(("surface", "sink", "kind", "transport"), list(_cells()))
def test_a_canary_takes_only_the_form_the_contract_allows(runs, surface, sink, kind, transport):
    rule = CONTRACT[sink].get(kind, Forbidden())

    rule.check(SINKS[sink](runs[surface]), kind, transport)


#: The identifier table of #1276, for the calling principal (the harness's API
#: key is issued to ``PRINCIPAL``): for each sink, whether it must carry the
#: principal (True), must not (False), or may (None). Spans carry caller ids
#: only on the operator's opt-in (#1580), which this suite leaves off.
PRINCIPAL_CONTRACT: dict[str, bool | None] = dict.fromkeys(SINKS, False) | {
    # Caller identity in audit, whatever the span setting (#1342). Also the
    # proof the principal went in, so an absence from the spans is a result.
    "otlp_audit_records": True,
    # Allowed by the table, and not what this suite pins.
    "compliance_cef": None,
    "compliance_leef": None,
    "compliance_jsonlines": None,
    "compliance_syslog": None,
    "structured_logs": None,
    "ws_events": None,
    "event_store": None,
}


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("sink", SINKS)
def test_the_principal_reaches_only_the_sinks_that_may_name_it(runs, surface, sink):
    rule = PRINCIPAL_CONTRACT[sink]
    carried = [s[:120] for s in _strings(SINKS[sink](runs[surface])) if PRINCIPAL in s]

    if rule is True:
        assert carried, "the sink is meant to name the caller, and holds no trace of the principal"
    elif rule is False:
        assert carried == [], f"{len(carried)} value(s) name the caller: {carried[:3]}"


# --- that the matrix means something ----------------------------------------


def _upstream_calls(run: dict[str, Any], transport: str) -> list[dict[str, Any]]:
    return [r for r in run["upstream_seen"][transport] if r["method"] == "tools/call"]


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("kind", KINDS)
def test_the_canary_went_in(runs, surface, kind, transport):
    """The value entered on this surface and transport: an absence elsewhere is then a result."""
    run = runs[surface]
    response = run["calls"][f"{transport}:{SCENARIO[kind]}"]
    assert "result" in response, response

    if kind in ("argument", "secret_argument"):
        key = "note" if kind == "argument" else "api_token"
        sent = [(r.get("arguments") or {}).get(key) for r in _upstream_calls(run, transport)]
        assert canary(kind, transport) in sent, sent
    elif kind in ("result", "is_error", "rpc_error", "approver_reason"):
        # The caller is owed the whole text; only telemetry is bounded.
        value = canary(kind, transport)
        assert any(value in s for s in _strings(response)), json.dumps(response)[:500]


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("sink", SINKS)
def test_the_sink_was_read(runs, surface, sink):
    assert SINKS[sink](runs[surface]), f"{sink} holds nothing, so no canary could be found in it"


@pytest.mark.parametrize("surface", SURFACES)
def test_the_event_stream_carries_what_the_store_holds(runs, surface):
    run = runs[surface]
    streamed = {e["event_type"] for e in run["ws_events"]}
    stored = sorted(e["event_id"] for e in run["event_store"] if e["event_type"] in streamed)

    assert sorted(e["event_id"] for e in run["ws_events"]) == stored
    assert {"ToolInvocationRequested", "ToolInvocationFailed", "ToolApprovalDenied"} <= streamed, streamed


@pytest.mark.parametrize("surface", SURFACES)
def test_both_upstreams_were_called_with_every_tool(runs, surface):
    for transport in TRANSPORTS:
        tools = sorted(r["tool"] for r in _upstream_calls(runs[surface], transport))
        # `guarded` never reaches its upstream: the approver denies it.
        assert tools == sorted(f"{transport}-{t}" for t in ("note", "result_text", "is_error", "rpc_error")), tools


# --- the bounds, whatever carries them ----------------------------------------


def _long_strings(values: Iterator[str], limit: int) -> list[str]:
    return [f"{len(v)}: {v[:80]}" for v in values if len(v) > limit]


@pytest.mark.parametrize("surface", SURFACES)
def test_no_span_attribute_exceeds_its_bound(runs, surface):
    values = (v for s in runs[surface]["spans"] for v in _strings(list(s["attributes"].values())))

    assert _long_strings(values, SPAN_ATTRIBUTE_LENGTH_LIMIT) == []


@pytest.mark.parametrize("surface", SURFACES)
def test_a_span_status_description_is_empty_or_its_error_type(runs, surface):
    described = [s for s in runs[surface]["spans"] if s["status_description"]]

    assert [s for s in described if s["status_description"] != s["attributes"].get("error.type")] == []


@pytest.mark.parametrize("surface", SURFACES)
def test_an_exception_event_carries_its_type_only(runs, surface):
    events = [e["attributes"] for s in runs[surface]["spans"] for e in s["events"] if e["name"] == "exception"]

    assert events, "no call failed, so R3 was not exercised"
    assert [a for a in events if set(a) != {"exception.type"}] == []


@pytest.mark.parametrize("surface", SURFACES)
def test_outbound_meta_carries_trace_context_only(runs, surface):
    metas = [set((r["meta"] or {}).keys()) for t in TRANSPORTS for r in _upstream_calls(runs[surface], t)]

    assert metas and all(keys <= {"traceparent", "tracestate"} for keys in metas), metas


@pytest.mark.parametrize("surface", SURFACES)
def test_no_audit_attribute_exceeds_its_bound(runs, surface):
    values = (v for r in runs[surface]["audit"] for v in _strings(list(r["attributes"].values())))

    assert _long_strings(values, AUDIT_ATTRIBUTE_LENGTH_LIMIT) == []


@pytest.mark.parametrize("surface", SURFACES)
def test_no_log_field_exceeds_its_bound(runs, surface):
    records = [r for r in _logs(runs[surface]) if isinstance(r, dict)]

    assert _long_strings((v for r in records for v in _strings(list(r.values()))), LOG_FIELD_LENGTH_LIMIT) == []


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("sink", ["event_store", "ws_events"])
def test_no_free_text_event_field_exceeds_its_bound(runs, surface, sink):
    values = (e[k] for e in runs[surface][sink] for k in FREE_TEXT_FIELDS if isinstance(e.get(k), str))

    assert _long_strings(values, EVENT_TEXT_LENGTH_LIMIT) == []
