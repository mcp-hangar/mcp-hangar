"""An in-process OTLP/gRPC receiver for the T3 export check.

A real receiver, not an exporter double: a ``grpc.server`` serving the OTLP
collector ``TraceService`` and ``LogsService``, so the gateway's own OTLP gRPC
exporters dial it over loopback and every record here arrived on the wire, as
protobuf, the way it would reach a Collector. It keeps the decoded requests and
answers queries by ``service.instance.id``, so each run reads only its own data.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import threading
import time
from typing import Any, TypeVar

import pytest

T = TypeVar("T")


@dataclass(frozen=True)
class Received:
    """One span or log record, as decoded at the receiver."""

    scope: str
    name: str  # span name; the body for a log record
    kind: int  # proto SpanKind; 0 for a log record
    trace_id: str  # hex; "" when the record carries none
    attributes: dict[str, Any]
    status_code: int = 0  # proto Status.StatusCode; 2 is ERROR. 0 for a log record
    status_message: str = ""  # the span status description; "" for a log record
    events: tuple[tuple[str, dict[str, Any]], ...] = ()  # (name, attributes) per span event


def _value(any_value: Any) -> Any:
    field = any_value.WhichOneof("value")
    return getattr(any_value, field) if field in ("string_value", "bool_value", "int_value", "double_value") else None


def _attributes(key_values: Any) -> dict[str, Any]:
    return {kv.key: _value(kv.value) for kv in key_values}


class OtlpReceiver:
    """Accepts OTLP traces and logs on a loopback port; see the module docstring."""

    def __init__(self) -> None:
        # Skip, never fail, when the receiver cannot be built.
        grpc = pytest.importorskip("grpc", reason="grpcio is needed for the in-process OTLP receiver")
        logs_pb = pytest.importorskip("opentelemetry.proto.collector.logs.v1.logs_service_pb2")
        logs_rpc = pytest.importorskip("opentelemetry.proto.collector.logs.v1.logs_service_pb2_grpc")
        trace_pb = pytest.importorskip("opentelemetry.proto.collector.trace.v1.trace_service_pb2")
        trace_rpc = pytest.importorskip("opentelemetry.proto.collector.trace.v1.trace_service_pb2_grpc")

        self._lock = threading.Lock()
        self._spans: list[tuple[dict[str, Any], Received]] = []
        self._logs: list[tuple[dict[str, Any], Received]] = []
        receiver = self

        class _Traces(trace_rpc.TraceServiceServicer):
            def Export(self, request, context):  # noqa: ANN001, ANN201, N802 -- generated gRPC signature
                receiver._keep_spans(request)
                return trace_pb.ExportTraceServiceResponse()

        class _Logs(logs_rpc.LogsServiceServicer):
            def Export(self, request, context):  # noqa: ANN001, ANN201, N802 -- generated gRPC signature
                receiver._keep_logs(request)
                return logs_pb.ExportLogsServiceResponse()

        self._server = grpc.server(ThreadPoolExecutor(max_workers=4))
        trace_rpc.add_TraceServiceServicer_to_server(_Traces(), self._server)
        logs_rpc.add_LogsServiceServicer_to_server(_Logs(), self._server)
        self.port = self._server.add_insecure_port("127.0.0.1:0")
        if not self.port:
            pytest.skip("the OTLP receiver could not bind a loopback port")
        self._server.start()

    @property
    def endpoint(self) -> str:
        """``http://`` means plaintext gRPC to the gateway's exporters."""
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self._server.stop(grace=None)

    def _keep_spans(self, request: Any) -> None:
        with self._lock:
            for resource_spans in request.resource_spans:
                resource = _attributes(resource_spans.resource.attributes)
                for scope_spans in resource_spans.scope_spans:
                    for span in scope_spans.spans:
                        record = Received(
                            scope=scope_spans.scope.name,
                            name=span.name,
                            kind=span.kind,
                            trace_id=span.trace_id.hex(),
                            attributes=_attributes(span.attributes),
                            status_code=span.status.code,
                            status_message=span.status.message,
                            events=tuple((e.name, _attributes(e.attributes)) for e in span.events),
                        )
                        self._spans.append((resource, record))

    def _keep_logs(self, request: Any) -> None:
        with self._lock:
            for resource_logs in request.resource_logs:
                resource = _attributes(resource_logs.resource.attributes)
                for scope_logs in resource_logs.scope_logs:
                    for log in scope_logs.log_records:
                        record = Received(
                            scope=scope_logs.scope.name,
                            name=str(_value(log.body)),
                            kind=0,
                            trace_id=log.trace_id.hex() if any(log.trace_id) else "",
                            attributes=_attributes(log.attributes),
                        )
                        self._logs.append((resource, record))

    def spans(self, instance_id: str) -> list[Received]:
        """Every span received from the process whose ``service.instance.id`` is ``instance_id``."""
        with self._lock:
            return [r for res, r in self._spans if res.get("service.instance.id") == instance_id]

    def logs(self, instance_id: str) -> list[Received]:
        """Every log record received from the process whose ``service.instance.id`` is ``instance_id``."""
        with self._lock:
            return [r for res, r in self._logs if res.get("service.instance.id") == instance_id]


def poll(probe: Callable[[], T | None], timeout: float, interval: float = 0.1) -> T | None:
    """Return the first truthy ``probe()`` within ``timeout`` seconds, else None.

    Arrival is asynchronous (a batch processor exports on its own schedule), so
    every check at the receiver waits on a deadline rather than a fixed sleep.
    """
    deadline = time.monotonic() + timeout
    while True:
        found = probe()
        if found or time.monotonic() >= deadline:
            return found or None
        time.sleep(interval)
