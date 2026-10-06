"""An upstream that changes a pinned tool without ``tools/list_changed`` is refused within one interval (#1693).

The digest gate compares a pin with the gateway's catalogue, which only a start
and an upstream's own ``tools/list_changed`` refreshed. An upstream that
changed a pinned tool's schema and said nothing was served under the old pin
until the gateway restarted.

The served app ``serve --http`` serves, in the egress topology so the call goes
through ``hangar_call`` and the digest gate (the front door would not list a
drifted tool at all), over one real upstream behind a real ``McpServer``. The
upstream has no GET stream, so it cannot announce anything. The pass of the
re-check worker is driven directly rather than waited for.

Naming: neutral placeholders only (store, spare, read_item, tenant:a).
"""

from __future__ import annotations

import io
import json
from typing import Any, ClassVar

from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry
from mcp_hangar.domain.events import DigestMismatchEvent
from mcp_hangar.domain.value_objects import McpServerState, ToolDigest
from mcp_hangar.pin_recheck import PinRecheckWorker
from mcp_hangar.server.context import get_context
from mcp_hangar.server.state import GROUPS

from ._front_door_harness import SERVER, TENANT_A, FrontDoor, Upstream, front_door, jsonrpc

READ = "read_item"
SPARE = "spare"


class CountingUpstream(Upstream):
    """The harness's upstream, counting the requests it is sent by method."""

    seen: ClassVar[list[str]] = []

    def do_POST(self) -> None:  # noqa: N802 -- http.server's handler name
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.seen.append(json.loads(body).get("method"))
        # Hand the body back to the base handler, which reads it again.
        self.rfile = io.BufferedReader(io.BytesIO(body))  # type: ignore[assignment]
        super().do_POST()


def _upstream_class() -> type[CountingUpstream]:
    return type("_CountingUpstream", (CountingUpstream,), {"seen": []})


def _hangar_call(door: FrontDoor) -> str | None:
    """Call READ through ``hangar_call``; the call's ``error_type``, None when it was served."""
    calls = [{"mcp_server": SERVER, "tool": READ, "arguments": {"x": "1"}}]
    payload = jsonrpc(door.call(TENANT_A, "hangar_call", {"calls": calls}))
    (result,) = payload["result"]["structuredContent"]["results"]
    return None if result["success"] else str(result["error_type"])


def _pin_to_current() -> None:
    """Pin READ, for all tenants, to the digest the upstream serves now."""
    registry = get_tool_projection_registry()
    (projection,) = [p for p in registry.list_for_server(SERVER) if p.tool == READ]
    registry.set_config_pin(SERVER, READ, None, projection.digest)


def _worker(published: list[Any]) -> PinRecheckWorker:
    bus = type("Bus", (), {"publish": lambda self, event: published.append(event)})()
    return PinRecheckWorker(get_context().repository, GROUPS, interval_s=5, event_bus=bus)


def test_a_drifted_pinned_tool_is_refused_after_one_pass() -> None:
    with front_door((READ,), topology="egress", upstream_class=_upstream_class()) as door:
        _pin_to_current()
        published: list[Any] = []
        worker = _worker(published)
        assert _hangar_call(door) is None
        assert door.upstream.called == [READ]

        # The upstream changes the pinned tool's schema and announces nothing.
        door.upstream.properties = {"x": {"type": "string"}, "exfiltrate": {"type": "string"}}

        # Before a pass, the stale catalogue still matches the pin: the defect.
        assert _hangar_call(door) is None
        assert door.upstream.called == [READ, READ]

        worker.recheck_once()

        assert _hangar_call(door) == "ToolDigestMismatchError"
        assert door.upstream.called == [READ, READ]

        mismatches = [e for e in published if isinstance(e, DigestMismatchEvent)]
        assert len(mismatches) == 1
        assert (mismatches[0].mcp_server_id, mismatches[0].tool_name) == (SERVER, READ)

        # Reported once, not on every pass.
        worker.recheck_once()
        assert len([e for e in published if isinstance(e, DigestMismatchEvent)]) == 1


def test_a_server_with_no_pin_is_not_listed() -> None:
    with front_door((READ,), topology="egress", upstream_class=_upstream_class()) as door:
        upstream = door.upstream
        assert issubclass(upstream, CountingUpstream)
        upstream.seen.clear()

        _worker([]).recheck_once()

        assert upstream.seen == []


def test_a_cold_pinned_server_is_not_started() -> None:
    with front_door((READ,), topology="egress", also=(SPARE,), upstream_class=_upstream_class()) as door:
        upstream = door.upstream
        assert issubclass(upstream, CountingUpstream)
        get_tool_projection_registry().set_config_pin(SPARE, READ, None, ToolDigest(tool_name=READ, sha256="c" * 64))
        upstream.seen.clear()

        _worker([]).recheck_once()

        assert upstream.seen == []
        assert get_context().repository.get(SPARE).state is McpServerState.COLD
