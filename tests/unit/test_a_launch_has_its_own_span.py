"""A cold start says which part was slow: the launch is its own span (#1546).

#1279 gave readiness a span and the handshake already had `initialize` and
`tools/list`, but spawning the process, running the container or opening the
HTTP transport had none. A four-second `mcp_server.cold_start` could not say
whether the process was starting, the image pulling or the handshake waiting.

`McpServerLauncher.launch` is now a template method around each launcher's
`_launch`. These tests read the span from a real SDK exporter. The failure path
and the wiring through `McpServer._get_launch_config` use the real
`SubprocessLauncher`; the other launchers are driven with their `_launch`
replaced, because what is under test is the span around it, not Docker.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.trace import StatusCode

from mcp_hangar.domain.model import McpServer
from mcp_hangar.infrastructure.launchers import base
from mcp_hangar.infrastructure.launchers.container import ContainerLauncher
from mcp_hangar.infrastructure.launchers.docker import DockerLauncher
from mcp_hangar.infrastructure.launchers.http import HttpLauncher
from mcp_hangar.infrastructure.launchers.subprocess import SubprocessLauncher

SERVER = "launch-span-server"
MOCK_PROVIDER = Path(__file__).resolve().parents[1] / "mock_provider.py"


@pytest.fixture()
def exporter(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A local TracerProvider + InMemorySpanExporter, never registered globally."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(base, "get_tracer", lambda name: provider.get_tracer("test-1546"))
    yield exporter
    exporter.clear()


def _launch_spans(exporter: Any) -> list[Any]:
    return [s for s in exporter.get_finished_spans() if s.name == "mcp_server.launch"]


def _stubbed(cls: type[base.McpServerLauncher], seen: dict[str, Any]) -> base.McpServerLauncher:
    """A launcher whose `_launch` records its arguments instead of starting anything."""
    launcher = object.__new__(cls)

    def fake_launch(*args: Any, **kwargs: Any) -> str:
        seen.update(kwargs)
        return "client"

    launcher._launch = fake_launch  # type: ignore[method-assign]
    return launcher


@pytest.mark.parametrize(
    ("cls", "mode", "receives_id"),
    [
        (SubprocessLauncher, "subprocess", False),
        (HttpLauncher, "remote", False),
        (DockerLauncher, "docker", True),
        (ContainerLauncher, "container", True),
    ],
)
def test_every_launcher_opens_one_launch_span_with_the_server_and_mode(
    exporter: Any, cls: type[base.McpServerLauncher], mode: str, receives_id: bool
) -> None:
    seen: dict[str, Any] = {}

    result = _stubbed(cls, seen).launch(mcp_server_id=SERVER)

    assert result == "client"
    [span] = _launch_spans(exporter)
    assert span.attributes["mcp.server.id"] == SERVER
    assert span.attributes["mcp.server.mode"] == mode
    assert span.status.status_code is StatusCode.UNSET
    # Docker and container launchers use the id themselves; the other two never
    # took it and must not start receiving it.
    assert ("mcp_server_id" in seen) is receives_id


def test_a_failed_launch_is_an_error_with_a_bounded_type(exporter: Any) -> None:
    """An empty command is refused by the real launcher before anything spawns."""
    from mcp_hangar.domain.exceptions import ValidationError

    with pytest.raises(ValidationError):
        SubprocessLauncher().launch(command=[], mcp_server_id=SERVER)

    [span] = _launch_spans(exporter)
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description is None
    assert span.attributes["error.type"] == "ValidationError"
    assert span.attributes["mcp.server.id"] == SERVER


def test_a_subprocess_server_start_is_spanned_with_its_id(exporter: Any) -> None:
    """The wiring: the aggregate passes its id, and the real subprocess launcher accepts it."""
    server = McpServer(
        mcp_server_id=SERVER,
        mode="subprocess",
        command=[sys.executable, str(MOCK_PROVIDER)],
    )
    config = server._get_launch_config()

    client = SubprocessLauncher().launch(**config)
    try:
        [span] = _launch_spans(exporter)
        assert span.attributes["mcp.server.id"] == SERVER
        assert span.attributes["mcp.server.mode"] == "subprocess"
        assert span.status.status_code is StatusCode.UNSET
    finally:
        client.close()


def test_launch_with_config_is_one_span_not_two(exporter: Any) -> None:
    from mcp_hangar.infrastructure.launchers.container import ContainerConfig

    seen: dict[str, Any] = {}
    launcher = _stubbed(ContainerLauncher, seen)

    launcher.launch_with_config(ContainerConfig(image="example/image:1"))

    assert len(_launch_spans(exporter)) == 1
    assert seen["image"] == "example/image:1"
