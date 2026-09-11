"""Which resource attributes Hangar's tracer provider exports, one interpreter per case (#1300).

OpenTelemetry registers the global tracer provider once per process, so each case
runs in a fresh subprocess with the real SDK. Hangar's OTLP exporter is swapped
for an in-memory one and the case reports the resource on the span it exported,
which is what a collector would receive. The precedence under test is stated on
``mcp_hangar.observability.tracing._build_resource``.

Kept out of test_observability_tracing.py so that parallel changes to tracing.py
do not also collide in one test file.
"""

from importlib.metadata import version
import json
import os
import subprocess
import sys
from typing import Any
import uuid

import pytest

pytestmark = pytest.mark.otel_sdk

_PRELUDE = """
import json
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from mcp_hangar.observability import tracing as t

built = []

def factory(**kwargs):
    built.append(InMemorySpanExporter())
    return built[-1]

t.OTLP_AVAILABLE = True
t.OTLPSpanExporter = factory
t.BatchSpanProcessor = SimpleSpanProcessor

def report_exported_resource(**extra):
    with t.get_tracer("case").start_as_current_span("span"):
        pass
    (span,) = built[0].get_finished_spans()
    print(json.dumps({**dict(span.resource.attributes), **extra}))
"""

# What bootstrap() does, in its order: mint the instance identity, then start
# tracing from the parsed configuration. The event is a real one, built after.
_THROUGH_THE_BOOTSTRAP = """
from mcp_hangar.domain.events import ToolInvocationRequested
from mcp_hangar.server.bootstrap import _init_instance_identity
from mcp_hangar.server.bootstrap.observability import _parse_observability_config, init_tracing

minted = _init_instance_identity()
assert init_tracing(_parse_observability_config(CONFIG).tracing)
report_exported_resource(minted=minted, produced_by=ToolInvocationRequested(mcp_server_id="math").produced_by)
"""

_CONFIG_NAMING_THE_SERVICE = {"observability": {"tracing": {"service_name": "from-config"}}}

_HANGAR_CALL = "assert t.init_tracing(service_name='configured', service_instance_id='hangar-given')"


def _exported_resource(body: str, **env: str) -> dict[str, Any]:
    ambient = ("OTEL_", "MCP_TRACING_", "MCP_ENVIRONMENT", "HANGAR_INSTANCE_LABEL")
    clean = {k: v for k, v in os.environ.items() if not k.startswith(ambient)}
    proc = subprocess.run(
        [sys.executable, "-c", _PRELUDE + body],
        capture_output=True,
        text=True,
        timeout=60,
        env={**clean, **env},
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize(
    ("call", "env", "expected"),
    [
        pytest.param(
            "assert t.init_tracing()",
            {},
            {"service.name": "mcp-hangar", "deployment.environment": "development"},
            id="hangar-defaults",
        ),
        pytest.param(
            _HANGAR_CALL,
            {"MCP_ENVIRONMENT": "staging"},
            {"service.name": "configured", "deployment.environment": "staging", "service.instance.id": "hangar-given"},
            id="hangar-configuration-over-defaults",
        ),
        pytest.param(
            _HANGAR_CALL,
            {
                "MCP_ENVIRONMENT": "staging",
                "OTEL_RESOURCE_ATTRIBUTES": (
                    "service.name=from-attributes,deployment.environment=prod,"
                    "service.instance.id=from-env,service.version=9.9.9"
                ),
            },
            {
                "service.name": "from-attributes",
                "deployment.environment": "prod",
                "service.instance.id": "from-env",
                "service.version": "9.9.9",
            },
            id="resource-attributes-over-hangar-configuration",
        ),
        pytest.param(
            _HANGAR_CALL,
            {"OTEL_SERVICE_NAME": "from-service-name", "OTEL_RESOURCE_ATTRIBUTES": "service.name=from-attributes"},
            {"service.name": "from-service-name"},
            id="otel-service-name-over-resource-attributes",
        ),
    ],
)
def test_each_resource_attribute_takes_the_first_source_that_sets_it(
    call: str, env: dict[str, str], expected: dict[str, str]
) -> None:
    resource = _exported_resource(call + "\nreport_exported_resource()", **env)

    assert {key: resource.get(key) for key in expected} == expected
    if "service.version" not in expected:
        assert resource["service.version"] == version("mcp-hangar")


def test_without_an_instance_id_the_sdk_mints_its_own() -> None:
    """A bare init_tracing(), outside the bootstrap, leaves service.instance.id to the SDK."""
    resource = _exported_resource("""
from mcp_hangar.domain.events import ToolInvocationRequested

assert t.init_tracing()
report_exported_resource(produced_by=ToolInvocationRequested(mcp_server_id="math").produced_by)
""")

    assert uuid.UUID(resource["service.instance.id"]).version == 4
    assert resource["service.instance.id"] != resource["produced_by"]


def test_through_the_bootstrap_the_instance_is_the_one_events_name() -> None:
    resource = _exported_resource(
        f"CONFIG = {_CONFIG_NAMING_THE_SERVICE!r}\n" + _THROUGH_THE_BOOTSTRAP,
        HANGAR_INSTANCE_LABEL="replica-a",
    )

    assert resource["minted"].startswith("replica-a-")
    assert resource["service.instance.id"] == resource["produced_by"] == resource["minted"]
    assert resource["service.name"] == "from-config"
    assert resource["deployment.environment"] == "development"


def test_through_the_bootstrap_the_environment_beats_config_and_identity() -> None:
    """An env service.name now beats config.yaml's, the one behaviour change here."""
    resource = _exported_resource(
        f"CONFIG = {_CONFIG_NAMING_THE_SERVICE!r}\n" + _THROUGH_THE_BOOTSTRAP,
        HANGAR_INSTANCE_LABEL="replica-a",
        OTEL_RESOURCE_ATTRIBUTES="service.name=from-attributes,service.instance.id=from-env",
    )

    assert resource["service.name"] == "from-attributes"
    assert resource["service.instance.id"] == "from-env"
    # The operator relabels the resource, not the instance: events keep the minted id.
    assert resource["produced_by"] == resource["minted"]
