"""Tracing overhead harness (#1292): four modes x three workloads, raw JSON out.

    python -m tests.benchmark.tracing_overhead --out tracing-overhead.json --soak 60

Needs ``.[dev,opentelemetry]``. Not a pytest module, so no CI job runs it.

Every mode runs in its own subprocess. OTel allows one global provider per
process, and a fresh process gives each mode its own RSS. The modes are:

- ``off``: ``MCP_TRACING_ENABLED=false`` and ``init_tracing()``.
- ``sdk_memory``: an SDK provider with ``BatchSpanProcessor(InMemorySpanExporter)``,
  cleared after every iteration.
- ``sdk_otlp_local``: ``init_tracing(otlp_endpoint=...)``, the production exporter
  config, pointed at a gRPC sink in another process.
- ``sdk_otlp_unreachable``: the same config, pointed at a closed local port.

Each mode runs the workloads in ``tracing_workload`` (one call, and batches of
10 and 100) after a warmup. Latency is measured per ``hangar_call``. CPU is
``process_time`` over the timed loop and, except for the unreachable endpoint,
a ``force_flush``, so export work lands in the workload that caused it. RSS is
read with ``ps``. Spans and OTLP-encoded bytes per call come from one untimed
extra iteration. Logging is ``setup_logging("WARNING")``: processors run, but
nothing is emitted.

``--soak N`` runs batches of 10 against the unreachable endpoint for N seconds,
sampling RSS every 0.5 s. Growth counts as unbounded if the least-squares RSS
slope over the second half exceeds ``SOAK_BOUND_MIB_PER_MIN``.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import resource
import socket
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
MODES = ("off", "sdk_memory", "sdk_otlp_local", "sdk_otlp_unreachable")
ITERATIONS = {1: 1000, 10: 200, 100: 60}  # calls per hangar_call -> timed iterations
WARMUP = {1: 50, 10: 10, 100: 3}
SOAK_BOUND_MIB_PER_MIN = 1.0


def _rss(pid: int) -> int:
    return int(subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)]).split()[0]) * 1024


def _pcts(ns: list[int]) -> dict[str, float]:
    q = statistics.quantiles(ns, n=100)
    return {"p50": q[49] / 1e3, "p95": q[94] / 1e3, "p99": q[98] / 1e3}


def _install(mode: str, endpoint: str):  # noqa: ANN202 -- SDK types are imported lazily
    """Route Hangar's spans for ``mode``; return (capture processor, flush, clear)."""
    from mcp_hangar.observability import tracing

    if mode == "off":
        assert not tracing.init_tracing(), "MCP_TRACING_ENABLED=false must refuse init"
        return None, lambda: None, lambda: None, type(tracing.get_tracer(__name__)).__name__
    from opentelemetry import trace
    from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    clear = lambda: None  # noqa: E731
    if mode == "sdk_memory":
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        clear = exporter.clear
        # Before #1283 Hangar ignores a provider it did not build until its own
        # init_tracing runs; after it, get_tracer uses the registered one. Only
        # the former needs the flag, and then it is set exactly as init would.
        if isinstance(tracing.get_tracer(__name__), tracing.NoOpTracer):
            tracing._initialized = True
    elif not tracing.init_tracing(otlp_endpoint=endpoint):
        raise SystemExit(f"init_tracing refused {endpoint}")
    provider = trace.get_tracer_provider()

    class Capture(SpanProcessor):
        def __init__(self) -> None:
            self.on, self.spans = False, []

        def on_end(self, span) -> None:  # noqa: ANN001
            if self.on:
                self.spans.append(span)

    capture = Capture()
    provider.add_span_processor(capture)
    flush = (lambda: None) if mode == "sdk_otlp_unreachable" else provider.force_flush
    return capture, flush, clear, type(tracing.get_tracer(__name__)).__name__


def _per_call_spans(wl, calls: int, capture) -> tuple[float, float]:  # noqa: ANN001
    if capture is None:
        return 0.0, 0.0
    from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans

    capture.on = True
    wl.run(calls)
    capture.on = False
    spans, capture.spans = capture.spans, []
    return len(spans) / calls, len(encode_spans(spans).SerializeToString()) / calls


def _child(mode: str, endpoint: str, soak: float) -> None:
    os.environ["MCP_TRACING_ENABLED"] = "false" if mode == "off" else "true"
    from mcp_hangar.logging_config import setup_logging

    setup_logging(level="WARNING", json_format=True)
    capture, flush, clear, tracer = _install(mode, endpoint)
    import mcp_hangar.server.context as server_context
    from tests.benchmark.tracing_workload import TracingWorkload

    wl = TracingWorkload()
    server_context._context = wl.ctx
    runs = []
    for calls, iterations in ITERATIONS.items() if not soak else [(10, 0)]:
        for _ in range(WARMUP[calls]):
            wl.run(calls)
        flush(), clear()
        rss0, cpu0, lat = _rss(os.getpid()), time.process_time(), []
        if soak:
            print("start", flush=True)
        deadline = time.monotonic() + soak
        while len(lat) < iterations or time.monotonic() < deadline:
            t0 = time.perf_counter_ns()
            wl.run(calls)
            lat.append(time.perf_counter_ns() - t0)
            clear()
        flush()
        cpu, rss1 = time.process_time() - cpu0, _rss(os.getpid())
        spans, nbytes = _per_call_spans(wl, calls, capture)
        n = len(lat) * calls
        runs.append(
            {"mode": mode, "tracer": tracer, "calls_per_iteration": calls, "iterations": len(lat)}
            | {"latency_us": _pcts(lat), "cpu_us_per_call": cpu / n * 1e6, "rss_delta_bytes": rss1 - rss0}
            | {"spans_per_call": spans, "span_bytes_per_call": nbytes, "ru_maxrss": resource.getrusage(0).ru_maxrss}
        )
    print(json.dumps(runs), flush=True)


def _sink(port: int) -> None:
    """A local OTLP/gRPC collector that accepts and counts spans until stdin closes."""
    from concurrent.futures import ThreadPoolExecutor

    import grpc
    from opentelemetry.proto.collector.trace.v1 import trace_service_pb2 as pb, trace_service_pb2_grpc as rpc

    received = [0]

    class Sink(rpc.TraceServiceServicer):
        def Export(self, request, context):  # noqa: ANN001, ANN201, N802 -- generated gRPC signature
            received[0] += sum(len(s.spans) for r in request.resource_spans for s in r.scope_spans)
            return pb.ExportTraceServiceResponse()

    server = grpc.server(ThreadPoolExecutor(max_workers=4))
    rpc.add_TraceServiceServicer_to_server(Sink(), server)
    server.add_insecure_port(f"127.0.0.1:{port}")
    server.start()
    print("ready", flush=True)
    sys.stdin.readline()
    server.stop(0)
    print(received[0], flush=True)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _spawn(*args: str, **kw) -> subprocess.Popen:  # noqa: ANN003
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "MCP_TRACING"))}
    cmd = [sys.executable, "-m", "tests.benchmark.tracing_overhead", *args]
    return subprocess.Popen(cmd, cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE, **kw)


@contextmanager
def _endpoint(mode: str, out: dict):  # noqa: ANN202
    port = _free_port()
    if mode != "sdk_otlp_local":  # nothing listens: connection refused
        yield f"http://127.0.0.1:{port}"
        return
    sink = _spawn("--sink", str(port), stdin=subprocess.PIPE)
    assert sink.stdout is not None and sink.stdin is not None
    assert sink.stdout.readline().strip() == "ready"
    yield f"http://127.0.0.1:{port}"
    sink.stdin.close()
    out["sink_spans_received"] = int(sink.stdout.readline())
    sink.wait()


def _run(mode: str, endpoint: str, soak: float = 0) -> tuple[list[dict], list[tuple[float, int]]]:
    samples: list[tuple[float, int]] = []
    with tempfile.TemporaryFile("w+") as err:
        proc = _spawn("--child", mode, "--endpoint", endpoint, "--soak", str(soak), stderr=err)
        assert proc.stdout is not None
        if soak:
            assert proc.stdout.readline().strip() == "start"
            t0 = time.monotonic()
            while proc.poll() is None:
                try:
                    samples.append((time.monotonic() - t0, _rss(proc.pid)))
                except (subprocess.CalledProcessError, IndexError):
                    break  # exited between poll and ps
                time.sleep(0.5)
        stdout = proc.stdout.read()
        if proc.wait() != 0:
            err.seek(0)
            raise RuntimeError(f"{mode} child failed:\n{err.read()[-4000:]}")
    return json.loads(stdout.strip().splitlines()[-1]), samples


def _soak(seconds: float) -> dict:
    [run], samples = _run("sdk_otlp_unreachable", f"http://127.0.0.1:{_free_port()}", seconds)
    # Past the window the child is exiting, which against a dead endpoint takes
    # a while (likely the SDK's exit-time flush): reported, not counted as soak.
    exit_after = samples[-1][0] - seconds
    samples = [(t, r) for t, r in samples if t <= seconds]
    tail = [(t, r) for t, r in samples if t >= seconds / 2]
    slope = statistics.linear_regression([t for t, _ in tail], [r for _, r in tail]).slope  # bytes/s
    spans_per_s = run["iterations"] * 10 * run["spans_per_call"] / seconds
    per_min = slope * 60 / 2**20
    return run | {
        "seconds": seconds,
        "rss_first_bytes": samples[0][1],
        "rss_peak_bytes": max(r for _, r in samples),
        "rss_last_bytes": samples[-1][1],
        "second_half_slope_mib_per_min": per_min,
        "second_half_bytes_retained_per_span": slope / spans_per_s,
        "unbounded_if_slope_mib_per_min_above": SOAK_BOUND_MIB_PER_MIN,
        "bounded": per_min <= SOAK_BOUND_MIB_PER_MIN,
        "child_exit_after_soak_s": exit_after,
        "rss_samples": samples,
    }


def _host() -> dict:
    darwin = sys.platform == "darwin"
    info: dict = {"platform": platform.platform(), "machine": platform.machine(), "logical_cpus": os.cpu_count()}
    try:
        if darwin:
            sysctl = lambda key: subprocess.check_output(["sysctl", "-n", key], text=True).strip()  # noqa: E731
            info |= {"cpu": sysctl("machdep.cpu.brand_string"), "memory_bytes": int(sysctl("hw.memsize"))}
        else:
            text = Path("/proc/cpuinfo").read_text() + Path("/proc/meminfo").read_text()
            lines = dict(line.split(":", 1) for line in text.splitlines() if ":" in line)
            info |= {"cpu": lines["model name"].strip(), "memory_bytes": int(lines["MemTotal"].split()[0]) * 1024}
    except (OSError, KeyError, ValueError, subprocess.CalledProcessError):
        pass
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="tracing-overhead.json")
    parser.add_argument("--soak", type=float, default=0, help="seconds against the unreachable endpoint")
    parser.add_argument("--child")
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--sink", type=int)
    args = parser.parse_args()
    if args.sink:
        return _sink(args.sink)
    if args.child:
        return _child(args.child, args.endpoint, args.soak)

    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    out: dict = {"created_utc": datetime.now(UTC).isoformat(), "git_sha": sha, "host": _host()}
    out |= {"python": sys.version, "opentelemetry_sdk": version("opentelemetry-sdk"), "runs": []}
    out["otlp_exporter"] = version("opentelemetry-exporter-otlp-proto-grpc")
    for mode in MODES:
        with _endpoint(mode, out) as endpoint:
            out["runs"] += _run(mode, endpoint)[0]
    if args.soak:
        out["soak"] = _soak(args.soak)
    Path(args.out).write_text(json.dumps(out, indent=2) + "\n")

    off = {r["calls_per_iteration"]: r["latency_us"]["p50"] for r in out["runs"] if r["mode"] == "off"}
    cols = ["mode", "calls", "p50 us", "p95 us", "p99 us", "p50 vs off", "CPU us/call", "RSS delta KiB", "spans/call"]
    print("| " + " | ".join([*cols, "B/call"]) + " |\n" + "|---" * (len(cols) + 1) + "|")
    for r in out["runs"]:
        lat, n = r["latency_us"], r["calls_per_iteration"]
        print(
            f"| {r['mode']} | {n} | {lat['p50']:.0f} | {lat['p95']:.0f} | {lat['p99']:.0f} | {lat['p50'] / off[n]:.2f}x"
            f" | {r['cpu_us_per_call']:.0f} | {r['rss_delta_bytes'] // 1024} | {r['spans_per_call']:.1f}"
            f" | {r['span_bytes_per_call']:.0f} |"
        )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
