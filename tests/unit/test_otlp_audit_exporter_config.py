"""Standard OTLP exporter configuration for Hangar's audit log exporter (#1326).

The audit log exporter resolves its protocol, endpoint and TLS as the trace
exporter does (#1282), with ``resolve_otlp_exporter_settings("logs", ...)``.
Before, it always built the gRPC exporter with ``insecure=True``: a scheme-less
endpoint sent identity-bearing audit records in plaintext, and the
``OTEL_EXPORTER_OTLP_LOGS_*`` variables had no effect.

The exporters here are the SDK's own, built for real; nothing replaces their
classes. From each the tests read, as ``test_tracing_otlp_exporter_config.py``
does for spans:

* the class: the gRPC or the HTTP ``OTLPLogExporter``;
* ``_endpoint``: host:port for gRPC, the full URL for HTTP;
* for gRPC, which of the SDK's channel factories the exporter called,
  ``secure_channel`` or ``insecure_channel``, recorded by a pass-through spy.
  SDK 1.44 also keeps it as ``_insecure``; 1.35 does not, so that is asserted
  where it exists;
* for HTTP, TLS is the scheme of ``_endpoint``.

The bootstrap cases run in a subprocess: a registered logger provider is
one-shot per process, and there the logs are Hangar's real stderr.
"""

import json
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from mcp_hangar.infrastructure.observability import otlp_audit_exporter as audit
from mcp_hangar.observability.tracing import resolve_otlp_exporter_settings

LOGS = "http://logs-collector:4317"
GENERIC = "http://generic-collector:4317"
HANGAR = "http://hangar-collector:4317"
SECRET = "s3cr3t-credential-value"
GRPC_LOGS = "opentelemetry.exporter.otlp.proto.grpc._log_exporter"
HTTP_LOGS = "opentelemetry.exporter.otlp.proto.http._log_exporter"


@pytest.fixture(autouse=True)
def _no_otel_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith(("OTEL_", "MCP_TRACING_")):
            monkeypatch.delenv(key)


@pytest.mark.otel_sdk
class TestRealLogExporters:
    """The SDK log exporter Hangar builds, and what it resolved."""

    @pytest.fixture
    def channels(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        import opentelemetry.exporter.otlp.proto.grpc.exporter as grpc_exporter_module

        called: list[str] = []
        for name in ("insecure_channel", "secure_channel"):
            real = getattr(grpc_exporter_module, name)

            def spy(*args, _real=real, _name=name, **kwargs):  # noqa: ANN002, ANN003, ANN202
                called.append(_name)
                return _real(*args, **kwargs)

            monkeypatch.setattr(grpc_exporter_module, name, spy)
        return called

    @pytest.fixture
    def build(self, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
        built = []

        def _build(endpoint: str | None = HANGAR, **env: str):  # noqa: ANN202
            for key, value in env.items():
                monkeypatch.setenv(key, value)
            exporter = audit._build_otlp_log_exporter(resolve_otlp_exporter_settings("logs", endpoint))
            if exporter is not None:
                built.append(exporter)
            return exporter

        yield _build
        for exporter in built:
            exporter.shutdown()

    @staticmethod
    def _assert_grpc(exporter, channels: list[str], endpoint: str, insecure: bool) -> None:  # noqa: ANN001
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter

        assert type(exporter) is OTLPLogExporter
        assert exporter._endpoint == endpoint
        assert channels == ["insecure_channel" if insecure else "secure_channel"]
        if hasattr(exporter, "_insecure"):  # SDK >= 1.37 keeps it; 1.35 only picks the channel
            assert exporter._insecure is insecure

    @staticmethod
    def _assert_http(exporter, endpoint: str) -> None:  # noqa: ANN001
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

        assert type(exporter) is OTLPLogExporter
        assert exporter._endpoint == endpoint

    # -- TLS --------------------------------------------------------------

    @pytest.mark.parametrize("where", ["hangar", "env"])
    def test_a_scheme_less_endpoint_uses_tls(self, build, channels, where) -> None:  # noqa: ANN001
        if where == "hangar":
            exporter = build("collector:4317")
        else:
            exporter = build(HANGAR, OTEL_EXPORTER_OTLP_ENDPOINT="collector:4317")
        self._assert_grpc(exporter, channels, "collector:4317", insecure=False)

    def test_http_is_plaintext(self, build, channels) -> None:  # noqa: ANN001
        self._assert_grpc(build("http://collector:4317"), channels, "collector:4317", insecure=True)

    @pytest.mark.parametrize("env", [{}, {"OTEL_EXPORTER_OTLP_INSECURE": "true"}])
    def test_https_uses_tls_even_when_insecure_is_asked_for(self, build, channels, env) -> None:  # noqa: ANN001
        self._assert_grpc(build("https://collector:4317", **env), channels, "collector:4317", insecure=False)

    @pytest.mark.parametrize("var", ["OTEL_EXPORTER_OTLP_LOGS_INSECURE", "OTEL_EXPORTER_OTLP_INSECURE"])
    def test_an_insecure_variable_is_honoured_both_ways(self, build, channels, var) -> None:  # noqa: ANN001
        self._assert_grpc(build("collector:4317", **{var: "true"}), channels, "collector:4317", insecure=True)
        channels.clear()
        self._assert_grpc(build("http://collector:4317", **{var: "false"}), channels, "collector:4317", False)

    def test_the_logs_insecure_flag_beats_the_generic_one(self, build, channels) -> None:  # noqa: ANN001
        exporter = build(
            "http://collector:4317", OTEL_EXPORTER_OTLP_LOGS_INSECURE="false", OTEL_EXPORTER_OTLP_INSECURE="true"
        )
        self._assert_grpc(exporter, channels, "collector:4317", insecure=False)

    # -- endpoint ---------------------------------------------------------

    def test_hangars_endpoint_is_used_while_no_variable_is_set(self, build, channels) -> None:  # noqa: ANN001
        self._assert_grpc(build(HANGAR), channels, "hangar-collector:4317", insecure=True)

    def test_the_logs_endpoint_beats_the_generic_one_and_hangars(self, build, channels) -> None:  # noqa: ANN001
        exporter = build(HANGAR, OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=LOGS, OTEL_EXPORTER_OTLP_ENDPOINT=GENERIC)
        self._assert_grpc(exporter, channels, "logs-collector:4317", insecure=True)

    def test_the_traces_endpoint_is_not_the_logs_one(self, build, channels) -> None:  # noqa: ANN001
        exporter = build(HANGAR, OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="http://traces-collector:4317")
        self._assert_grpc(exporter, channels, "hangar-collector:4317", insecure=True)

    # -- protocol ---------------------------------------------------------

    def test_the_logs_protocol_selects_the_http_exporter_and_the_generic_endpoint_gets_its_path(
        self,
        build,  # noqa: ANN001
        channels,  # noqa: ANN001
    ) -> None:
        exporter = build(
            HANGAR,
            OTEL_EXPORTER_OTLP_LOGS_PROTOCOL="http/protobuf",
            OTEL_EXPORTER_OTLP_PROTOCOL="grpc",
            OTEL_EXPORTER_OTLP_ENDPOINT="http://generic-collector:4318",
        )
        self._assert_http(exporter, "http://generic-collector:4318/v1/logs")
        assert channels == []

    def test_hangars_endpoint_is_used_verbatim_over_http(self, build) -> None:  # noqa: ANN001
        exporter = build("https://hangar-collector:4318/v1/logs", OTEL_EXPORTER_OTLP_PROTOCOL="http/protobuf")
        self._assert_http(exporter, "https://hangar-collector:4318/v1/logs")

    # -- no usable exporter -----------------------------------------------

    @staticmethod
    def _assert_unavailable(logger, reason: str, protocol: str, package: str | None) -> None:  # noqa: ANN001
        logger.warning.assert_called_once_with(
            "audit_log_otlp_exporter_unavailable",
            reason=reason,
            protocol=protocol,
            package=package,
            supported=["grpc", "http/protobuf"],
            fallback="structlog",
        )
        logger.info.assert_not_called()

    def test_an_unsupported_protocol_builds_nothing_and_says_why(self, build, channels) -> None:  # noqa: ANN001
        with patch.object(audit, "logger") as logger:
            assert build(OTEL_EXPORTER_OTLP_LOGS_PROTOCOL="http/json") is None
        self._assert_unavailable(logger, "unsupported_protocol", "http/json", None)
        assert channels == []

    @pytest.mark.parametrize(
        ("protocol", "module", "package"),
        [
            ("grpc", GRPC_LOGS, "opentelemetry-exporter-otlp-proto-grpc"),
            ("http/protobuf", HTTP_LOGS, "opentelemetry-exporter-otlp-proto-http"),
        ],
    )
    def test_a_missing_package_builds_nothing_and_names_it(self, build, monkeypatch, protocol, module, package) -> None:  # noqa: ANN001
        monkeypatch.setitem(sys.modules, module, None)  # as on an install without it
        if protocol == "grpc":  # imported with the module; the HTTP one only when selected
            monkeypatch.setattr(audit, "OTLPLogExporter", None)
        with patch.object(audit, "logger") as logger:
            assert build(OTEL_EXPORTER_OTLP_PROTOCOL=protocol) is None
        self._assert_unavailable(logger, "package_missing", protocol, package)

    def test_an_empty_generic_endpoint_builds_nothing(self, build, channels) -> None:  # noqa: ANN001
        with patch.object(audit, "logger") as logger:
            assert build(OTEL_EXPORTER_OTLP_ENDPOINT="") is None
        self._assert_unavailable(logger, "empty_endpoint", "grpc", "opentelemetry-exporter-otlp-proto-grpc")
        assert channels == []


_CHILD = """
import json, os, sys
if BLOCK:
    sys.modules[BLOCK] = None  # as on an install without that exporter package
import opentelemetry.exporter.otlp.proto.grpc.exporter as grpc_exporter_module
channels = []
for name in ("insecure_channel", "secure_channel"):
    real = getattr(grpc_exporter_module, name)
    setattr(grpc_exporter_module, name, lambda *a, _r=real, _n=name, **k: channels.append(_n) or _r(*a, **k))

from opentelemetry._logs import get_logger_provider
from mcp_hangar.infrastructure.observability import otlp_audit_exporter as m

inner = []
class Spy(m._MeteredLogExporter):  # Hangar's wrapper, observed; the SDK exporter inside is real
    def __init__(self, exporter):
        inner.append(exporter)
        super().__init__(exporter)
m._MeteredLogExporter = Spy

from mcp_hangar.server.bootstrap.observability import init_observability
init_observability({"observability": {"tracing": {"otlp_endpoint": ENDPOINT}}})
m.OTLPAuditExporter().export_tool_invocation("math", "add", "success", 1.0)
e = inner[0] if inner else None
print(json.dumps({
    "owned": m._audit_provider is not None,
    "configured": m.audit_log_export_configured(),
    "provider": type(get_logger_provider()).__name__,
    "exporter": e and type(e).__module__,
    "endpoint": e and e._endpoint,
    "channels": channels,
}))
sys.stdout.flush()
sys.stderr.flush()
os._exit(0)  # the collector does not exist: no exit-time flush to it
"""


def _run(endpoint: str, block: str = "", **env: str) -> tuple[dict, str]:
    """Bootstrap audit export in a fresh interpreter; its observations and its output.

    The output is everything the child wrote except the observation line, which
    this test prints itself (it carries the exporter's resolved endpoint).
    """
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "MCP_TRACING_"))}
    code = f"BLOCK = {block!r}\nENDPOINT = {endpoint!r}\n" + _CHILD
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env={**clean, "MCP_TRACING_ENABLED": "false", **env},
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    *output, observed = proc.stdout.strip().splitlines()
    return json.loads(observed), "\n".join(output) + proc.stderr


@pytest.mark.otel_sdk
class TestBootstrap:
    """Through the bootstrap, the way the server starts."""

    def test_a_scheme_less_endpoint_in_the_file_gets_a_tls_channel(self) -> None:
        seen, logs = _run("collector.invalid:4317")

        assert seen["owned"] is True and seen["exporter"] == GRPC_LOGS
        assert seen["endpoint"] == "collector.invalid:4317"
        assert seen["channels"] == ["secure_channel"]
        assert "audit_log_export_initialized" in logs

    def test_the_logs_variables_reach_the_registered_exporter(self) -> None:
        seen, _ = _run(
            HANGAR,
            OTEL_EXPORTER_OTLP_LOGS_PROTOCOL="http/protobuf",
            OTEL_EXPORTER_OTLP_LOGS_ENDPOINT="https://logs-collector.invalid:4318/v1/logs",
        )

        assert seen["owned"] is True and seen["exporter"] == HTTP_LOGS
        assert seen["endpoint"] == "https://logs-collector.invalid:4318/v1/logs"

    @pytest.mark.parametrize(
        ("block", "env", "reason"),
        [
            ("", {"OTEL_EXPORTER_OTLP_LOGS_PROTOCOL": "http/json"}, "unsupported_protocol"),
            (GRPC_LOGS, {}, "package_missing"),
            (HTTP_LOGS, {"OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf"}, "package_missing"),
        ],
    )
    def test_no_usable_exporter_keeps_audit_on_and_records_reach_the_structured_log(
        self, block: str, env: dict[str, str], reason: str
    ) -> None:
        seen, logs = _run(HANGAR, block, **env)

        assert seen == {
            "owned": False,
            "configured": True,
            "provider": "ProxyLoggerProvider",
            "exporter": None,
            "endpoint": None,
            "channels": [],
        }
        assert "audit_log_otlp_exporter_unavailable" in logs and reason in logs
        assert "audit_event" in logs and "math" in logs
        assert "audit_log_export_initialized" not in logs

    @pytest.mark.parametrize("protocol", ["grpc", "http/protobuf", "http/json"])
    @pytest.mark.parametrize("where", ["hangar_config", "logs_endpoint"])
    def test_headers_and_endpoint_credentials_are_never_logged(self, protocol: str, where: str) -> None:
        secret_url = f"https://user:{SECRET}@collector.invalid:4318/v1/logs?token={SECRET}"
        env = {
            "OTEL_EXPORTER_OTLP_PROTOCOL": protocol,
            "OTEL_EXPORTER_OTLP_HEADERS": f"authorization=Bearer%20{SECRET},x-api-key={SECRET}",
        }
        if where == "logs_endpoint":
            env["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"] = secret_url
        seen, logs = _run(secret_url if where == "hangar_config" else HANGAR, **env)

        assert seen["owned"] is (protocol != "http/json")
        assert "audit_log_" in logs  # the lines that would carry them were written
        assert SECRET not in logs
