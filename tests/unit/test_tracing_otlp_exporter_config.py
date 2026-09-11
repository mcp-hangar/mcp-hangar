"""Standard OTLP exporter configuration for Hangar's trace exporter (#1282).

The exporters here are the SDK's own, built for real: nothing replaces their
classes. Each case sets the environment, resolves the settings as
``init_tracing`` does, builds the exporter and reads back what it resolved:

* the class: the gRPC or the HTTP ``OTLPSpanExporter``;
* ``_endpoint`` on both -- the gRPC exporter keeps the target (host:port), the
  HTTP one the full URL;
* for gRPC, the TLS decision: which of the SDK's channel factories the exporter
  called, ``secure_channel`` or ``insecure_channel`` as its module imported
  them, recorded by a pass-through spy. SDK 1.44 also keeps it as
  ``_insecure``; 1.35 does not, so that is asserted where it exists;
* for HTTP, TLS is the scheme of ``_endpoint``.

The bootstrap cases run in a subprocess: a registered tracer provider is
one-shot per process, and there the logs are Hangar's real stderr.
"""

import json
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from mcp_hangar.observability import tracing
from mcp_hangar.observability.tracing import OtlpExporterSettings, resolve_otlp_exporter_settings

TRACES = "http://traces-collector:4317"
GENERIC = "http://generic-collector:4317"
HANGAR = "http://hangar-collector:4317"
SECRET = "s3cr3t-credential-value"


@pytest.fixture(autouse=True)
def _no_otel_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith(("OTEL_", "MCP_TRACING_")):
            monkeypatch.delenv(key)


class TestResolveOtlpExporterSettings:
    """The precedence itself, before any SDK is involved."""

    def test_nothing_set_leaves_everything_to_the_sdk(self) -> None:
        assert resolve_otlp_exporter_settings() == OtlpExporterSettings("grpc", None, None)

    @pytest.mark.parametrize(
        ("env", "protocol"),
        [
            ({"OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf"}, "http/protobuf"),
            ({"OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"}, "grpc"),
            ({"OTEL_EXPORTER_OTLP_PROTOCOL": " HTTP/Protobuf "}, "http/protobuf"),
            (
                {"OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf", "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"},
                "http/protobuf",
            ),
            (
                {"OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "", "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf"},
                "http/protobuf",
            ),
            ({"OTEL_EXPORTER_OTLP_PROTOCOL": "http/json"}, "http/json"),
        ],
    )
    def test_protocol(self, monkeypatch: pytest.MonkeyPatch, env: dict[str, str], protocol: str) -> None:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        assert resolve_otlp_exporter_settings().protocol == protocol

    def test_hangars_endpoint_and_insecure_are_used_while_no_variable_is_set(self) -> None:
        assert resolve_otlp_exporter_settings(endpoint=HANGAR, insecure=True) == OtlpExporterSettings(
            "grpc", HANGAR, True
        )

    @pytest.mark.parametrize("var", ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"])
    def test_an_endpoint_variable_hands_endpoint_and_insecure_to_the_sdk(
        self, monkeypatch: pytest.MonkeyPatch, var: str
    ) -> None:
        monkeypatch.setenv(var, TRACES)
        settings = resolve_otlp_exporter_settings(endpoint=HANGAR, insecure=True)
        assert (settings.endpoint, settings.insecure) == (None, None)

    @pytest.mark.parametrize("var", ["OTEL_EXPORTER_OTLP_TRACES_INSECURE", "OTEL_EXPORTER_OTLP_INSECURE"])
    def test_an_insecure_variable_hands_insecure_to_the_sdk(self, monkeypatch: pytest.MonkeyPatch, var: str) -> None:
        monkeypatch.setenv(var, "false")
        assert resolve_otlp_exporter_settings(endpoint=HANGAR, insecure=True) == OtlpExporterSettings(
            "grpc", HANGAR, None
        )

    def test_an_empty_generic_endpoint_means_no_exporter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        assert resolve_otlp_exporter_settings(endpoint=HANGAR).endpoint == ""
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", TRACES)
        assert resolve_otlp_exporter_settings(endpoint=HANGAR).endpoint is None

    def test_the_signal_names_the_variable_family(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", TRACES)
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_LOGS_PROTOCOL", "http/protobuf")
        assert resolve_otlp_exporter_settings("logs", HANGAR) == OtlpExporterSettings("http/protobuf", HANGAR, None)
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", TRACES)
        assert resolve_otlp_exporter_settings("logs", HANGAR).endpoint is None


@pytest.mark.otel_sdk
class TestRealExporters:
    """The SDK exporter Hangar builds, and what it resolved."""

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

        def _build(endpoint: str | None = None, **env: str):  # noqa: ANN202
            for key, value in env.items():
                monkeypatch.setenv(key, value)
            exporter = tracing._build_otlp_span_exporter(resolve_otlp_exporter_settings("traces", endpoint))
            if exporter is not None:
                built.append(exporter)
            return exporter

        yield _build
        for exporter in built:
            exporter.shutdown()

    @staticmethod
    def _assert_grpc(exporter, channels: list[str], endpoint: str, insecure: bool) -> None:  # noqa: ANN001
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        assert type(exporter) is OTLPSpanExporter
        assert exporter._endpoint == endpoint
        assert channels == ["insecure_channel" if insecure else "secure_channel"]
        if hasattr(exporter, "_insecure"):  # SDK >= 1.37 keeps it; 1.35 only picks the channel
            assert exporter._insecure is insecure

    @staticmethod
    def _assert_http(exporter, endpoint: str) -> None:  # noqa: ANN001
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        assert type(exporter) is OTLPSpanExporter
        assert exporter._endpoint == endpoint

    # -- protocol ---------------------------------------------------------

    @pytest.mark.parametrize("env", [{}, {"OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"}])
    def test_grpc_is_the_default_and_the_localhost_default_stays_plaintext(self, build, channels, env) -> None:  # noqa: ANN001
        self._assert_grpc(build(**env), channels, "localhost:4317", insecure=True)

    def test_http_protobuf_selects_the_http_exporter_and_its_default(self, build, channels) -> None:  # noqa: ANN001
        self._assert_http(build(OTEL_EXPORTER_OTLP_PROTOCOL="http/protobuf"), "http://localhost:4318/v1/traces")
        assert channels == []

    def test_the_traces_protocol_beats_the_generic_one(self, build) -> None:  # noqa: ANN001
        exporter = build(OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="http/protobuf", OTEL_EXPORTER_OTLP_PROTOCOL="grpc")
        self._assert_http(exporter, "http://localhost:4318/v1/traces")

    def test_an_unsupported_protocol_builds_nothing_and_says_why(self, build, channels) -> None:  # noqa: ANN001
        with patch.object(tracing, "logger") as logger:
            assert build(OTEL_EXPORTER_OTLP_PROTOCOL="http/json") is None
        logger.warning.assert_called_once_with(
            "tracing_otlp_exporter_unavailable",
            reason="unsupported_protocol",
            protocol="http/json",
            supported=["grpc", "http/protobuf"],
        )
        logger.info.assert_not_called()
        assert channels == []

    # -- endpoint ---------------------------------------------------------

    def test_the_traces_endpoint_is_honoured(self, build, channels) -> None:  # noqa: ANN001
        self._assert_grpc(build(OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=TRACES), channels, "traces-collector:4317", True)

    def test_the_traces_endpoint_beats_the_generic_one(self, build, channels) -> None:  # noqa: ANN001
        exporter = build(OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=TRACES, OTEL_EXPORTER_OTLP_ENDPOINT=GENERIC)
        self._assert_grpc(exporter, channels, "traces-collector:4317", insecure=True)

    def test_the_traces_endpoint_beats_the_generic_one_over_http(self, build) -> None:  # noqa: ANN001
        exporter = build(
            OTEL_EXPORTER_OTLP_PROTOCOL="http/protobuf",
            OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="http://traces-collector:4318/custom/path",
            OTEL_EXPORTER_OTLP_ENDPOINT="http://generic-collector:4318",
        )
        self._assert_http(exporter, "http://traces-collector:4318/custom/path")

    def test_the_generic_endpoint_gets_the_signal_path_over_http(self, build) -> None:  # noqa: ANN001
        exporter = build(OTEL_EXPORTER_OTLP_PROTOCOL="http/protobuf", OTEL_EXPORTER_OTLP_ENDPOINT="http://generic:4318")
        self._assert_http(exporter, "http://generic:4318/v1/traces")

    def test_hangars_endpoint_is_used_while_no_variable_is_set(self, build, channels) -> None:  # noqa: ANN001
        self._assert_grpc(build(HANGAR), channels, "hangar-collector:4317", insecure=True)

    def test_hangars_endpoint_is_used_verbatim_over_http(self, build) -> None:  # noqa: ANN001
        exporter = build("https://hangar-collector:4318/v1/traces", OTEL_EXPORTER_OTLP_PROTOCOL="http/protobuf")
        self._assert_http(exporter, "https://hangar-collector:4318/v1/traces")

    @pytest.mark.parametrize(
        ("var", "host"),
        [
            ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "traces-collector:4317"),
            ("OTEL_EXPORTER_OTLP_ENDPOINT", "generic-collector:4317"),
        ],
    )
    def test_an_endpoint_variable_beats_hangars(self, build, channels, var, host) -> None:  # noqa: ANN001
        self._assert_grpc(build(HANGAR, **{var: TRACES if "TRACES" in var else GENERIC}), channels, host, True)

    def test_an_empty_generic_endpoint_builds_nothing(self, build, channels) -> None:  # noqa: ANN001
        with patch.object(tracing, "logger") as logger:
            assert build(OTEL_EXPORTER_OTLP_ENDPOINT="") is None
        logger.info.assert_called_once_with("tracing_otlp_exporter_skipped", reason="empty_endpoint")
        assert channels == []

    # -- TLS --------------------------------------------------------------

    def test_insecure_false_is_honoured_for_an_http_endpoint(self, build, channels) -> None:  # noqa: ANN001
        exporter = build(OTEL_EXPORTER_OTLP_ENDPOINT=GENERIC, OTEL_EXPORTER_OTLP_INSECURE="false")
        self._assert_grpc(exporter, channels, "generic-collector:4317", insecure=False)

    def test_a_scheme_less_endpoint_uses_tls_by_default(self, build, channels) -> None:  # noqa: ANN001
        exporter = build(OTEL_EXPORTER_OTLP_ENDPOINT="generic-collector:4317")
        self._assert_grpc(exporter, channels, "generic-collector:4317", insecure=False)

    def test_the_traces_insecure_flag_is_honoured(self, build, channels) -> None:  # noqa: ANN001
        exporter = build(
            OTEL_EXPORTER_OTLP_ENDPOINT="generic-collector:4317", OTEL_EXPORTER_OTLP_TRACES_INSECURE="true"
        )
        self._assert_grpc(exporter, channels, "generic-collector:4317", insecure=True)

    def test_the_traces_insecure_flag_beats_the_generic_one(self, build, channels) -> None:  # noqa: ANN001
        exporter = build(
            OTEL_EXPORTER_OTLP_ENDPOINT=GENERIC,
            OTEL_EXPORTER_OTLP_TRACES_INSECURE="false",
            OTEL_EXPORTER_OTLP_INSECURE="true",
        )
        self._assert_grpc(exporter, channels, "generic-collector:4317", insecure=False)

    @pytest.mark.parametrize("hangar", [None, "https://hangar-collector:4317"])
    def test_https_uses_tls_even_when_insecure_is_asked_for(self, build, channels, hangar) -> None:  # noqa: ANN001
        env = {"OTEL_EXPORTER_OTLP_INSECURE": "true"}
        if hangar is None:
            env["OTEL_EXPORTER_OTLP_ENDPOINT"] = "https://generic-collector:4317"
        exporter = build(hangar, **env)
        host = "hangar-collector:4317" if hangar else "generic-collector:4317"
        self._assert_grpc(exporter, channels, host, insecure=False)

    def test_https_uses_tls_over_http(self, build) -> None:  # noqa: ANN001
        exporter = build(
            OTEL_EXPORTER_OTLP_PROTOCOL="http/protobuf",
            OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="https://traces-collector:4318/v1/traces",
        )
        self._assert_http(exporter, "https://traces-collector:4318/v1/traces")

    # -- the bootstrap's mapping ------------------------------------------

    def test_config_yaml_reaches_the_exporter_and_the_env_still_beats_it(self, build, channels, monkeypatch) -> None:  # noqa: ANN001
        from mcp_hangar.server.bootstrap.observability import _parse_observability_config, TracingConfig

        assert TracingConfig().otlp_endpoint is None
        assert _parse_observability_config({}).tracing.otlp_endpoint is None
        yaml = {"observability": {"tracing": {"otlp_endpoint": HANGAR}}}
        self._assert_grpc(
            build(_parse_observability_config(yaml).tracing.otlp_endpoint), channels, "hangar-collector:4317", True
        )
        channels.clear()
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", GENERIC)
        mapped = _parse_observability_config(yaml).tracing.otlp_endpoint
        assert mapped == GENERIC  # what #1289's audit wiring reads
        self._assert_grpc(build(mapped), channels, "generic-collector:4317", True)


_PRELUDE = """
import json, sys
from mcp_hangar.observability import tracing as t

inner = []
class Spy(t._MeteredSpanExporter):  # Hangar's wrapper, observed; the SDK exporter inside is real
    def __init__(self, exporter):
        inner.append(exporter)
        super().__init__(exporter)
t._MeteredSpanExporter = Spy

from mcp_hangar.server.bootstrap.observability import _parse_observability_config, init_tracing
ok = init_tracing(_parse_observability_config({}).tracing)
e = inner[0] if inner else None
print(json.dumps({"initialized": ok, "exporter": e and type(e).__module__, "endpoint": e and e._endpoint}))
"""


def _run(block: str = "", **env: str) -> tuple[dict, str]:
    """Bootstrap tracing in a fresh interpreter; its observations and its output.

    The output is everything the child wrote except the observation line, which
    this test prints itself (it carries the exporter's resolved endpoint).
    """
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "MCP_TRACING_"))}
    code = (f"import sys; sys.modules[{block!r}] = None\n" if block else "") + _PRELUDE
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, env={**clean, **env}
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    *output, observed = proc.stdout.strip().splitlines()
    return json.loads(observed), "\n".join(output) + proc.stderr


@pytest.mark.otel_sdk
class TestBootstrap:
    """Through the bootstrap, the way the server starts."""

    def test_the_standard_variables_reach_the_registered_exporter(self) -> None:
        seen, _ = _run(
            OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="http/protobuf",
            OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="https://traces-collector:4318/v1/traces",
            OTEL_EXPORTER_OTLP_ENDPOINT="http://generic-collector:4318",
        )
        assert seen == {
            "initialized": True,
            "exporter": "opentelemetry.exporter.otlp.proto.http.trace_exporter",
            "endpoint": "https://traces-collector:4318/v1/traces",
        }

    @pytest.mark.parametrize(
        ("block", "env", "reason"),
        [
            ("", {"OTEL_EXPORTER_OTLP_PROTOCOL": "http/json"}, "unsupported_protocol"),
            (
                "opentelemetry.exporter.otlp.proto.http.trace_exporter",
                {"OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf"},
                "package_missing",
            ),
            ("opentelemetry.exporter.otlp.proto.grpc.trace_exporter", {}, "package_missing"),
        ],
    )
    def test_no_usable_exporter_is_named_and_not_reported_as_initialized(
        self, block: str, env: dict[str, str], reason: str
    ) -> None:
        seen, logs = _run(block, **env)
        assert seen == {"initialized": False, "exporter": None, "endpoint": None}
        assert "tracing_otlp_exporter_unavailable" in logs and reason in logs
        if reason == "package_missing":
            assert "opentelemetry-exporter-otlp-proto-" in logs
        for claim in ("tracing_otlp_exporter_added", "tracing_initialized"):
            assert claim not in logs, claim

    @pytest.mark.parametrize("protocol", ["grpc", "http/protobuf", "http/json"])
    def test_headers_and_endpoint_credentials_are_never_logged(self, protocol: str) -> None:
        endpoint = f"https://user:{SECRET}@traces-collector:4318/v1/traces?token={SECRET}"
        seen, logs = _run(
            OTEL_EXPORTER_OTLP_PROTOCOL=protocol,
            OTEL_EXPORTER_OTLP_HEADERS=f"authorization=Bearer%20{SECRET},x-api-key={SECRET}",
            OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=endpoint,
        )
        assert seen["initialized"] is (protocol != "http/json")
        assert "tracing_otlp_exporter_" in logs  # the lines that would carry them were written
        assert SECRET not in logs
