"""uvicorn finds a WebSocket library in an install of the declared dependencies (#1676).

uvicorn upgrades a WebSocket connection only through a library it can import;
without one it logs "No supported WebSocket library detected" and every
WebSocket route, `/api/ws/events` among them, answers 404. Only the Dockerfile
installed one, so a pip or uv install of the package served no event stream.

The served behaviour is in
`tests/integration/test_the_event_stream_upgrades_on_a_served_gateway.py`. This
file pins the two facts it rests on, cheaply: the requirement is a base one (an
extra, `dev` included, would leave a plain install without it while the test
environment still had it), and uvicorn's `auto` choice resolves to a protocol.
"""

from __future__ import annotations

from importlib.metadata import requires

import pytest
import uvicorn
from packaging.requirements import Requirement


def test_websockets_is_a_base_requirement() -> None:
    base = [
        Requirement(raw)
        for raw in requires("mcp-hangar") or []
        if Requirement(raw).marker is None or "extra" not in str(Requirement(raw).marker)
    ]

    assert "websockets" in {req.name for req in base}


# The locked uvicorn picks the `websockets` legacy protocol, which websockets
# deprecates; a newer uvicorn picks its sans-I/O one. Either is an upgrade.
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_uvicorn_resolves_a_websocket_protocol() -> None:
    async def app(scope: object, receive: object, send: object) -> None:  # pragma: no cover - never served
        raise AssertionError("not served")

    config = uvicorn.Config(app, ws="auto", log_config=None)
    config.load()

    assert config.ws_protocol_class is not None
