"""Tier 3 live verification: the governance boundary exports the caller's identity (#1278).

BLACK-BOX, over the wire twice. Two real ``mcp-hangar serve --http`` processes --
one ``egress`` for ``hangar_call``, one ``front_door`` for a flat tool, because
neither surface serves the other's entry point -- export through their own OTLP
gRPC exporters to the in-process receiver in ``_otlp_receiver``. Every attribute
asserted here crossed the wire as OTLP protobuf, from a request that carried a
real ``X-API-Key`` bound to a tenant.

This tier is the one the acceptance criteria of #1278 name, and the reason is
mechanical: identity reaches a tool handler through an ASGI contextvar that the
MCP SDK's per-message task does not inherit, so each surface re-binds it. A unit
test with a mock context exercises none of that machinery and has passed before
while the real path exported nothing -- which is how the decorator this issue
deletes went years without a caller.

Proven: a ``hangar_call`` and a flat-tool call each produce a
``batch.call.<tool>`` span carrying the caller's tenant, id, type and user id;
no identity attribute is exported as an empty string; and an attribute the
identity context does not hold -- ``mcp.session.id``, for an API-key caller on
this transport -- is absent rather than empty. Both gateways opt in with
``MCP_TRACING_CALLER_IDS=true``: caller ids are off spans by default (#1580),
which ``tests/integration/test_caller_ids_on_the_served_app.py`` proves over the
served app on both entry points. Not proven: anything about
anonymous callers or other transports, or the attributes of spans other than
the enrichment boundary. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t3_governance_identity.py -m "live and t3" -o addopts=""
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from mcp_hangar.observability.conventions import MCP, Caller
from tests.live import _group_support as gs
from tests.live._otlp_receiver import OtlpReceiver, Received, poll
from tests.live.conftest import _MATH_SERVER, running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t3]

_TENANT = "tenant-trace"
_TOOL = "add"
_ARGS = {"a": 2, "b": 3}
_ARRIVAL_TIMEOUT_S = 30.0

#: The identity keys the boundary exports for this caller. `mcp.session.id` is
#: deliberately not among them: see `_assert_identity`.
_REQUIRED = (Caller.TENANT, Caller.ID, Caller.TYPE, MCP.USER_ID)

_CONFIG = """\
logging:
  level: WARNING
{topology}auth:
  enabled: true
  allow_anonymous: false
  api_key:
    enabled: true
    header_name: X-API-Key
  storage:
    driver: sqlite
    path: {auth_db}
  role_assignments:
    - principal: "svc:{tenant}"
      role: developer
      scope: global
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    env:
      MCP_TRANSPORT: stdio
    idle_ttl_s: 60
"""

_FRONT_DOOR = "tool_access:\n  mode: front_door\n"


@dataclass
class _Gateway:
    base_url: str
    run_id: str


@dataclass
class _Harness:
    receiver: OtlpReceiver
    api_key: str
    egress: _Gateway
    front_door: _Gateway


def _env(receiver: OtlpReceiver, run_id: str) -> dict[str, str]:
    """The gateway's environment: the receiver as its OTLP endpoint, ``run_id`` as its instance id."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "MCP_TRACING"))}
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = receiver.endpoint  # http:// -- plaintext gRPC
    env["OTEL_RESOURCE_ATTRIBUTES"] = f"service.instance.id={run_id}"
    env["MCP_TRACING_CALLER_IDS"] = "true"  # the opt-in this tier exercises (#1580)
    return env


def _config(auth_db: Path, *, front_door: bool) -> str:
    return _CONFIG.format(
        topology=_FRONT_DOOR if front_door else "",
        auth_db=str(auth_db),
        tenant=_TENANT,
        python=sys.executable,
        server=str(_MATH_SERVER),
    )


@pytest.fixture(scope="module")
def harness(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Harness]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")

    workdir = tmp_path_factory.mktemp("governance_identity")
    auth_db = workdir / "auth.db"
    keys = gs.seed_tenant_keys(auth_db, [_TENANT])

    egress_dir, front_dir = workdir / "egress", workdir / "front"
    egress_dir.mkdir()
    front_dir.mkdir()

    receiver = OtlpReceiver()
    try:
        egress_id, front_id = uuid.uuid4().hex, uuid.uuid4().hex
        with running_hangar(egress_dir, _config(auth_db, front_door=False), _env(receiver, egress_id)) as one:
            with running_hangar(front_dir, _config(auth_db, front_door=True), _env(receiver, front_id)) as two:
                yield _Harness(
                    receiver=receiver,
                    api_key=keys[_TENANT],
                    egress=_Gateway(base_url=one.base_url, run_id=egress_id),
                    front_door=_Gateway(base_url=two.base_url, run_id=front_id),
                )
    finally:
        receiver.stop()


def _call(base_url: str, api_key: str, tool: str, arguments: dict[str, Any]) -> Any:
    """Call an MCP tool over streamable-HTTP as the tenant's key."""
    from mcp import ClientSession

    from tests.live._mcp_client import open_mcp_streams

    async def _run() -> Any:
        async with open_mcp_streams(f"{base_url}/mcp", {"X-API-Key": api_key}) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(tool, arguments)

    return asyncio.run(_run())


def _boundary_span(receiver: OtlpReceiver, run_id: str) -> Received | None:
    """The ``batch.call.<tool>`` span from this gateway, once it has arrived."""

    def _probe() -> Received | None:
        for span in receiver.spans(run_id):
            if span.name == f"batch.call.{_TOOL}":
                return span
        return None

    return poll(_probe, _ARRIVAL_TIMEOUT_S)


def _assert_identity(span: Received | None, where: str) -> None:
    assert span is not None, f"{where}: no batch.call.{_TOOL} span reached the receiver"
    attributes = span.attributes

    missing = [key for key in _REQUIRED if key not in attributes]
    assert not missing, f"{where}: the boundary exported no {missing}; got {sorted(attributes)}"

    assert attributes[Caller.TENANT] == _TENANT, f"{where}: {attributes[Caller.TENANT]!r}"
    assert attributes[Caller.ID] == f"svc:{_TENANT}", f"{where}: {attributes[Caller.ID]!r}"
    assert attributes[Caller.TYPE], f"{where}: caller type is empty"
    assert attributes[MCP.USER_ID] == f"svc:{_TENANT}", f"{where}: {attributes[MCP.USER_ID]!r}"

    # An empty attribute is worse than a missing one: it makes every call look
    # like it had a tenant. The boundary omits what it does not know.
    empty = [key for key, value in attributes.items() if key.startswith(("mcp.caller.", "mcp.session")) and value == ""]
    assert not empty, f"{where}: exported empty identity attributes {empty}"

    # This run found that an API-key caller over streamable-HTTP has no session
    # id in its IdentityContext: the MCP session exists on the wire, and the
    # identity bridges do not carry it. Asserted as observed, because the
    # contract here is that an unknown value is OMITTED -- exporting an empty
    # `mcp.session.id` would make every span claim a session it never had.
    # Populating it is identity plumbing, not tracing, and is filed as #1539.
    assert MCP.SESSION_ID not in attributes, (
        f"{where}: a session id is reaching the boundary now -- if the identity bridges were "
        f"taught to carry one, move MCP.SESSION_ID into _REQUIRED and delete this assertion"
    )


def test_hangar_call_exports_the_callers_identity(harness: _Harness) -> None:
    """The batch entry point, on the surface that serves it."""
    result = _call(
        harness.egress.base_url,
        harness.api_key,
        "hangar_call",
        {"calls": [{"mcp_server": "math", "tool": _TOOL, "arguments": _ARGS}]},
    )
    assert not getattr(result, "is_error", False), result

    _assert_identity(_boundary_span(harness.receiver, harness.egress.run_id), "hangar_call")


def test_flat_tool_call_exports_the_callers_identity(harness: _Harness) -> None:
    """The front door's flat projection, which re-binds identity on its own path.

    This is the half a mock-context test cannot reach: the flat path passes no
    request context to the executor and re-binds the caller itself, so it can
    regress independently of ``hangar_call``.
    """
    result = _call(harness.front_door.base_url, harness.api_key, _TOOL, _ARGS)
    assert not getattr(result, "is_error", False), result

    _assert_identity(_boundary_span(harness.receiver, harness.front_door.run_id), "flat tool")
