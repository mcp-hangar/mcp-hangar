"""The Langfuse adapter is gone, its settings say so, and Langfuse still gets spans (#1683).

The adapter was built and never called after 2.22.0, so `MCP_LANGFUSE_*`,
`HANGAR_LANGFUSE_*` and `observability.langfuse` did nothing. A scrub setting
asked Hangar to keep payloads from a third party; one that silently stops
applying is the failure #1655 was about, so it refuses the boot. The other
removed settings are named in a warning, and the config schema names the block.

The replacement is the standard OTLP exporter pointed at Langfuse's endpoint:
the last test sends a span through Hangar's own bootstrap to a local stand-in
for that endpoint and reads back what arrived.
"""

import json
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.server.bootstrap import observability as obs
from mcp_hangar.server.config_schema import validate_config

SCRUB_ENV = (
    "MCP_LANGFUSE_SCRUB_INPUTS",
    "MCP_LANGFUSE_SCRUB_OUTPUTS",
    "HANGAR_LANGFUSE_SCRUB_INPUTS",
    "HANGAR_LANGFUSE_SCRUB_OUTPUTS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith(("OTEL_", "MCP_TRACING_", "MCP_AUDIT_", "MCP_LANGFUSE_", "HANGAR_LANGFUSE_", "LANGFUSE_")):
            monkeypatch.delenv(key)


def _init(config: dict):
    """init_observability with nothing behind it, and the mocks to say what ran."""
    with (
        patch.object(obs, "init_tracing", return_value=False) as init_tracing,
        patch.object(obs, "init_audit_log_export") as init_audit,
        patch.object(obs, "logger") as logger,
    ):
        obs.init_observability(config)
    return init_tracing.called and init_audit.called, logger


@pytest.mark.parametrize("key", SCRUB_ENV)
@pytest.mark.parametrize("value", ["true", "on", "false"])
def test_a_scrub_env_var_refuses_the_boot_whatever_its_value(
    monkeypatch: pytest.MonkeyPatch, key: str, value: str
) -> None:
    monkeypatch.setenv(key, value)
    with (
        patch.object(obs, "init_tracing") as init_tracing,
        patch.object(obs, "init_audit_log_export") as init_audit,
        pytest.raises(ConfigurationError, match=key) as refused,
    ):
        obs.init_observability({})

    assert "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT" in str(refused.value)
    assert "Collector" in str(refused.value)  # where redaction lives now
    init_tracing.assert_not_called()  # refused before anything exported
    init_audit.assert_not_called()


@pytest.mark.parametrize("key", ["scrub_inputs", "scrub_outputs"])
def test_a_scrub_key_in_the_file_refuses_the_boot(key: str) -> None:
    config = {"observability": {"langfuse": {"enabled": True, key: True}}}
    with pytest.raises(ConfigurationError, match=f"observability.langfuse.{key}"):
        obs.init_observability(config)


@pytest.mark.parametrize(
    "key",
    ["MCP_LANGFUSE_ENABLED", "MCP_LANGFUSE_SAMPLE_RATE", "HANGAR_LANGFUSE_ENABLED", "HANGAR_LANGFUSE_SAMPLE_RATE"],
)
def test_another_removed_env_var_is_named_with_its_replacement(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    monkeypatch.setenv(key, "true")

    ran, logger = _init({})

    assert ran
    logger.warning.assert_any_call("langfuse_settings_removed", settings=[key], replacement=obs.LANGFUSE_OVER_OTLP)


def test_the_langfuse_sdk_variables_are_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """A co-located application may set them; they were never Hangar's own switch."""
    for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
        monkeypatch.setenv(key, "x")

    ran, logger = _init({})

    assert ran
    assert all(c.args[0] != "langfuse_settings_removed" for c in logger.warning.call_args_list)


def test_the_config_schema_names_the_block_and_its_replacement() -> None:
    problems = validate_config({"observability": {"langfuse": {"enabled": True}}})

    assert len(problems) == 1
    assert problems[0].startswith("observability.langfuse was removed")
    assert "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT" in problems[0]


_TO_LANGFUSE = r"""
import json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

seen = []
class Langfuse(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        seen.append({"path": self.path, "authorization": self.headers.get("Authorization"),
                     "version": self.headers.get("x-langfuse-ingestion-version"),
                     "type": self.headers.get("Content-Type"), "body": body.decode("latin-1")})
        self.send_response(200); self.end_headers()
    def log_message(self, *a): pass

server = HTTPServer(("127.0.0.1", 0), Langfuse)
threading.Thread(target=server.serve_forever, daemon=True).start()
import os
os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] = f"http://127.0.0.1:{server.server_port}/api/public/otel/v1/traces"

from mcp_hangar.server.bootstrap.observability import init_observability, shutdown_observability
from mcp_hangar.observability.tracing import get_tracer
init_observability({})
with get_tracer("langfuse-test").start_as_current_span("canary-span-name"):
    pass
shutdown_observability()
server.shutdown()
print(json.dumps(seen))
"""


@pytest.mark.otel_sdk
def test_spans_reach_a_langfuse_otlp_endpoint_with_its_credential() -> None:
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "MCP_TRACING_", "MCP_AUDIT_"))}
    env = {
        **clean,
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
        # base64("pk-lf-test:sk-lf-test"), with the space percent-encoded as the README says.
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS": "Authorization=Basic%20cGstbGYtdGVzdDpzay1sZi10ZXN0,"
        "x-langfuse-ingestion-version=4",
    }
    proc = subprocess.run([sys.executable, "-c", _TO_LANGFUSE], capture_output=True, text=True, timeout=60, env=env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    seen = json.loads(proc.stdout.strip().splitlines()[-1])

    # Traces only: the TRACES_ endpoint leaves audit log export off, which Langfuse would refuse.
    assert [s["path"] for s in seen] == ["/api/public/otel/v1/traces"]
    assert seen[0]["authorization"] == "Basic cGstbGYtdGVzdDpzay1sZi10ZXN0"
    assert seen[0]["version"] == "4"
    assert seen[0]["type"] == "application/x-protobuf"
    assert "canary-span-name" in seen[0]["body"]
