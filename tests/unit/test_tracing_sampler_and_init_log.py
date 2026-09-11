"""The sampler argument and the tracing init log, one interpreter per case (#1301).

An out-of-range or non-numeric ``OTEL_TRACES_SAMPLER_ARG`` used to make
``TraceIdRatioBased`` raise inside ``init_tracing``, whose fault barrier then
left tracing off for the whole process. And the init log said nothing about
which exporters were attached.

The global tracer provider is registered once per process, so each case runs
in a fresh subprocess with the real SDK: the provider, its sampler and the
exporters are the SDK's own. The child logs as JSON to stderr and prints one
JSON line of observations to stdout; the parent asserts on both. These live
apart from ``test_observability_tracing.py``, which other open changes edit.
"""

import json
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.otel_sdk

SECRET = "s3cr3t-credential-value"
# Hangar's own endpoint, from config.yaml, carrying userinfo: it must never reach a log.
ENDPOINT = f"http://collector-user:{SECRET}@traces-collector.invalid:4317"

_PRELUDE = """
import json
from mcp_hangar.logging_config import setup_logging

setup_logging(json_format=True)

from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from mcp_hangar.observability import tracing as t
from mcp_hangar.server.bootstrap.observability import _parse_observability_config, init_tracing

def emit(**observed):
    print(json.dumps(observed))
"""


def _run(body: str, **env: str) -> tuple[dict, list[dict]]:
    """Run ``body`` in a fresh interpreter; return its observations and its log events."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "MCP_TRACING_"))}
    proc = subprocess.run(
        [sys.executable, "-c", _PRELUDE + body],
        capture_output=True,
        text=True,
        timeout=60,
        env={**clean, **env},
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    events = []
    for line in proc.stderr.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and "event" in event:
            events.append(event)
    assert SECRET not in proc.stderr and SECRET not in proc.stdout
    return json.loads(proc.stdout.strip().splitlines()[-1]), events


def _named(events: list[dict], name: str) -> list[dict]:
    return [e for e in events if e["event"] == name]


# Through the bootstrap, the way the server starts. Hangar's OTLP exporter is the
# SDK's in-memory one, exported synchronously, so a sampled span is observable
# the moment it ends and nothing leaves the process.
_SAMPLER_CASE = """
spans = InMemorySpanExporter()
t.OTLP_AVAILABLE = True
t.OTLPSpanExporter = lambda **kwargs: spans
t.BatchSpanProcessor = SimpleSpanProcessor

initialized = init_tracing(_parse_observability_config({}).tracing)
provider = trace.get_tracer_provider()
with t.get_tracer("case").start_as_current_span("case-span"):
    pass
emit(
    initialized=initialized,
    provider=type(provider).__module__,
    sampler=provider.sampler.get_description(),
    spans=[s.name for s in spans.get_finished_spans()],
)
"""


def _parentbased(root: str) -> str:
    return (
        f"ParentBased{{root:{root},remoteParentSampled:AlwaysOnSampler,"
        "remoteParentNotSampled:AlwaysOffSampler,localParentSampled:AlwaysOnSampler,"
        "localParentNotSampled:AlwaysOffSampler}"
    )


@pytest.mark.parametrize(
    ("sampler", "arg", "description"),
    [
        ("traceidratio", "5", "TraceIdRatioBased{1.0}"),
        ("traceidratio", "-1", "TraceIdRatioBased{1.0}"),
        ("traceidratio", "abc", "TraceIdRatioBased{1.0}"),
        # The SDK's own range check lets NaN through; it then fails computing the bound.
        ("traceidratio", "nan", "TraceIdRatioBased{1.0}"),
        ("parentbased_traceidratio", "5", _parentbased("TraceIdRatioBased{1.0}")),
    ],
)
def test_an_invalid_sampler_argument_warns_once_and_samples_everything(
    sampler: str, arg: str, description: str
) -> None:
    seen, events = _run(_SAMPLER_CASE, OTEL_TRACES_SAMPLER=sampler, OTEL_TRACES_SAMPLER_ARG=arg)

    assert seen["initialized"] is True
    assert seen["provider"] == "opentelemetry.sdk.trace"
    assert seen["sampler"] == description
    assert seen["spans"] == ["case-span"]  # tracing is on, and ratio 1.0 sampled the span
    warnings = [e for e in events if e["level"] == "warning"]
    assert len(warnings) == 1, warnings
    assert warnings[0]["event"] == "tracing_sampler_arg_invalid"
    assert warnings[0]["variable"] == "OTEL_TRACES_SAMPLER_ARG"
    assert warnings[0]["value"] == arg
    assert warnings[0]["fallback"] == 1.0
    assert not _named(events, "tracing_initialization_failed")
    assert len(_named(events, "tracing_initialized")) == 1


@pytest.mark.parametrize(
    ("sampler", "arg", "description"),
    [
        ("traceidratio", "0.25", "TraceIdRatioBased{0.25}"),
        ("traceidratio", "0", "TraceIdRatioBased{0.0}"),
        ("traceidratio", "1", "TraceIdRatioBased{1.0}"),
        ("traceidratio", None, "TraceIdRatioBased{1.0}"),  # unset: the SDK's default
        ("traceidratio", "", "TraceIdRatioBased{1.0}"),  # empty counts as unset
        ("parentbased_traceidratio", "0.25", _parentbased("TraceIdRatioBased{0.25}")),
    ],
)
def test_a_valid_sampler_argument_is_used_without_a_warning(sampler: str, arg: str | None, description: str) -> None:
    env = {"OTEL_TRACES_SAMPLER": sampler}
    if arg is not None:
        env["OTEL_TRACES_SAMPLER_ARG"] = arg
    seen, events = _run(_SAMPLER_CASE, **env)

    assert seen["initialized"] is True
    assert seen["sampler"] == description
    assert not [e for e in events if e["level"] in ("warning", "error")]


@pytest.mark.parametrize(
    ("env", "exporters"),
    [
        # An endpoint is configured, but the protocol has no exporter: console only.
        ({"OTEL_EXPORTER_OTLP_PROTOCOL": "http/json", "MCP_TRACING_CONSOLE": "true"}, ["console"]),
        ({}, ["otlp_grpc"]),
        ({"OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf"}, ["otlp_http"]),
        ({"MCP_TRACING_CONSOLE": "true"}, ["otlp_grpc", "console"]),
    ],
)
def test_the_init_log_lists_the_attached_exporters_and_no_endpoint(env: dict[str, str], exporters: list[str]) -> None:
    """Real SDK exporters, built from a config-file endpoint; no span, so nothing is sent."""
    seen, events = _run(
        f"ENDPOINT = {ENDPOINT!r}\n"
        + """
config = _parse_observability_config({"observability": {"tracing": {"otlp_endpoint": ENDPOINT}}})
initialized = init_tracing(config.tracing)
# One span processor per exporter attached: what the registered provider really holds.
emit(initialized=initialized, processors=len(trace.get_tracer_provider()._active_span_processor._span_processors))
""",
        **env,
    )

    assert seen["initialized"] is True
    assert seen["processors"] == len(exporters)
    [initialized] = _named(events, "tracing_initialized")
    assert initialized["exporters"] == exporters
    assert not [key for key in initialized if "endpoint" in key]
    # No line names the endpoint, its host or its credentials (checked in _run).
    assert not [e for e in events if "traces-collector" in json.dumps(e)]
