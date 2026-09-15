"""A tool call reads a reload's governance as one set (#1431).

A reload swaps the tool-access policies, then the withdrawals and pins, then
the `header_exposure` blocks. The executor's gates used to read them one gate
after another, and the front door's listing and routing one tool at a time. So
a call that arrived between two swaps could combine the new policy with the
previous withdrawals. The issue's example is an edit that moves a control from
`tools.deny_list: [t]` to `tool_projection.withdrawn: [t]`. The old file denies
`t` and the new one withdraws it, but between the two swaps `t` was allowed.

Each test pauses a real reload after it swapped the policies and before it
swapped the withdrawals, and makes a call through a served surface in that
window: `hangar_call`, and the front door's `tools/call` and listing. The call
waits out the swap and gets the new file's answer, never an allow. The last
test does the same for the re-check after an approval hold, with a reload
that lands in the middle of it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import json
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import anyio
import pytest

import mcp_hangar.server.tools.batch as batch
from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
    ToolProjectionRegistry,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.policies.header_exposure import clear_header_exposure_policies
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.fastmcp_server import flat_tool_projection
from mcp_hangar.server import config as server_config
from mcp_hangar.server.state import get_runtime, GROUPS
from mcp_hangar.server.tools.batch import hangar_call
from mcp_hangar.server.tools.batch.executor import BatchExecutor
from mcp_hangar.server.tools.batch.models import CallSpec

SERVER = "store"
TOOL = "t"
OPEN = "open"  # a tool neither file restricts
TENANT = "tenant:a"
METHOD_NOT_FOUND = -32601


def _server(**extra: Any) -> dict[str, Any]:
    """A server that is never started: the calls are refused, or answered by a mock command bus."""
    return {"mode": "subprocess", "command": ["python", "-c", "pass"], **extra}


DENIED = {SERVER: _server(tools={"deny_list": [TOOL]})}
WITHDRAWN = {SERVER: _server(tool_projection={"withdrawn": [TOOL]})}


def _reset() -> None:
    reset_tool_access_resolver()
    reset_tool_projection_registry()
    clear_header_exposure_policies()
    server_config._BUILT_FROM.clear()
    repository = get_runtime().repository
    if repository.exists(SERVER):
        repository.remove(SERVER)
    GROUPS.clear()


def _in_force(file: dict[str, Any], *, then: dict[str, Any]) -> Any:
    """Put *file* in force with `t` and `open` in the catalogue, and build *then*, the file a reload puts in force."""
    server_config.load_config(file)
    get_tool_projection_registry().build_from_tools(
        SERVER, [ToolSchema(name=name, description=name, input_schema={}) for name in (TOOL, OPEN)]
    )
    return server_config.build_config(then)


@pytest.fixture
def served() -> Iterator[Mock]:
    """`hangar_call` and the front door, dispatching through a mock command bus that answers every call."""
    _reset()
    context = Mock()
    context.command_bus.send.return_value = {"content": [{"type": "text", "text": "ok"}]}
    context.governed_task_store = None
    context.approval_gate = None
    context.auth_components = None  # auth off
    server = McpServer(mcp_server_id=SERVER, mode="subprocess", command=["unused"])
    context.get_mcp_server.side_effect = {SERVER: server}.get
    context.mcp_server_exists.side_effect = lambda server_id: server_id == SERVER
    with (
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=context),
        patch("mcp_hangar.server.tools.batch.validator.get_context", return_value=context),
        patch.object(batch, "get_context", lambda: SimpleNamespace(auth_components=None), create=True),
        patch("mcp_hangar.server.tools.tool_permissions.management_tools_for", lambda _ctx: set()),
    ):
        yield context
    _reset()


def _as_tenant(call: Callable[[], Any]) -> Any:
    token = identity_context_var.set(
        IdentityContext(
            caller=CallerIdentity(
                user_id="alice", agent_id=None, session_id=None, principal_type="user", tenant_id=TENANT
            )
        )
    )
    try:
        return call()
    finally:
        identity_context_var.reset(token)


def _hangar_call(tool: str) -> tuple[bool, str | None]:
    """``(success, error type)`` of a `hangar_call` of *tool*."""
    response = _as_tenant(lambda: hangar_call(calls=[{"mcp_server": SERVER, "tool": tool, "arguments": {}}], ctx=None))
    [result] = response["results"]
    return result["success"], result["error_type"]


def _front_door_call(tool: str) -> tuple[bool, Any]:
    """``(success, error code or text)`` of the front door's `tools/call` of *tool*, as a tenant's request."""
    handlers: dict[str, Any] = {}
    low = SimpleNamespace(add_request_handler=lambda method, _params, handler: handlers.__setitem__(method, handler))
    flat_tool_projection.register_flat_tool_handlers(SimpleNamespace(_mcp_server=low))
    principal = Principal(id=PrincipalId("user:alice"), type=PrincipalType.USER, tenant_id=TENANT)
    body = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": tool, "arguments": {}}}
    request = SimpleNamespace(
        state=SimpleNamespace(auth=SimpleNamespace(principal=principal)),
        _body=json.dumps(body).encode(),
        headers={"mcp-protocol-version": "2026-07-28"},
    )
    answer: list[tuple[bool, Any]] = []

    async def _call() -> None:
        try:
            result = await handlers["tools/call"](
                SimpleNamespace(request=request), SimpleNamespace(name=tool, arguments={})
            )
        except Exception as exc:  # noqa: BLE001 -- the refusal is the subject of the test
            code = getattr(exc, "code", None) or getattr(getattr(exc, "error", None), "code", None)
            answer.append((False, code))
        else:
            payload = result.model_dump(by_alias=True)
            refused = bool(payload.get("isError"))
            answer.append((not refused, payload["content"][0]["text"] if refused else None))

    anyio.run(_call)
    return answer[0]


def _listing() -> list[str]:
    """The tools the front door lists to the tenant."""
    return sorted(flat_tool_projection.generate_projection(TENANT).routes)


class _PausedReload:
    """A reload held after it swapped the policies, before it swaps the withdrawals and pins."""

    def __init__(self, staged: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        self.paused, self.resume = threading.Event(), threading.Event()
        adopt = ToolProjectionRegistry.adopt_config_overlays

        def pause_then_adopt(registry: ToolProjectionRegistry, *args: Any, **kwargs: Any) -> None:
            self.paused.set()
            self.resume.wait(5)
            adopt(registry, *args, **kwargs)

        monkeypatch.setattr(ToolProjectionRegistry, "adopt_config_overlays", pause_then_adopt)
        self._reload = threading.Thread(target=staged.commit, kwargs={"replace": True})

    def call_in_the_window(self, call: Callable[[], Any]) -> Any:
        """Make *call* while the reload is held, and return its answer once the reload is done."""
        answers: list[Any] = []
        caller = threading.Thread(target=lambda: answers.append(call()))
        self._reload.start()
        try:
            assert self.paused.wait(5)
            # The window itself: the new policy no longer denies `t`, and the
            # previous withdrawals do not withdraw it yet.
            assert get_tool_access_resolver().is_tool_allowed(SERVER, TOOL, member_id=TENANT)
            assert not get_tool_projection_registry().is_withdrawn(SERVER, TOOL, tenant_id=TENANT)

            caller.start()
            caller.join(0.2)
            assert caller.is_alive() and not answers, "the call waits out the swap in progress"
        finally:
            self.resume.set()
            self._reload.join(5)
        caller.join(5)
        [answer] = answers
        return answer


def test_a_hangar_call_between_two_swaps_gets_the_new_files_answer(served: Mock, monkeypatch) -> None:
    staged = _in_force(DENIED, then=WITHDRAWN)
    assert _hangar_call(TOOL) == (False, "ToolAccessDeniedError"), "the old file's answer"
    assert _hangar_call(OPEN) == (True, None), "this path runs a call it allows"

    answer = _PausedReload(staged, monkeypatch).call_in_the_window(lambda: _hangar_call(TOOL))

    assert answer == (False, "ToolWithdrawnError")
    assert _hangar_call(TOOL) == answer, "the new file's answer"


def test_a_front_door_call_between_two_swaps_gets_the_new_files_answer(served: Mock, monkeypatch) -> None:
    staged = _in_force(DENIED, then=WITHDRAWN)
    assert _front_door_call(TOOL) == (False, METHOD_NOT_FOUND), "the old file's answer: not listed, not callable"
    assert _front_door_call(OPEN) == (True, None), "this path runs a call it allows"

    answer = _PausedReload(staged, monkeypatch).call_in_the_window(lambda: _front_door_call(TOOL))

    # Not routed at all: the executor's withdrawal refusal would mean the
    # front door had listed and routed `t` on the mix.
    assert answer == (False, METHOD_NOT_FOUND)
    assert _front_door_call(TOOL) == answer, "the new file's answer"


def test_the_front_door_listing_between_two_swaps_is_the_new_files(served: Mock, monkeypatch) -> None:
    staged = _in_force(DENIED, then=WITHDRAWN)
    assert _listing() == [OPEN]

    assert _PausedReload(staged, monkeypatch).call_in_the_window(_listing) == [OPEN]


def test_the_re_check_after_an_approval_hold_reads_one_set(served: Mock, monkeypatch) -> None:
    """A reload lands between the re-check's policy read and its withdrawal read, and the re-check is made again.

    The reverse edit: `t` moves from the withdrawals to the deny list. The
    previous policy with the new withdrawals would let the approved call run.
    """
    staged = _in_force(WITHDRAWN, then=DENIED)
    registry = get_tool_projection_registry()
    resolve = registry.resolve
    reloaded: list[bool] = []

    def resolve_after_the_reload(*args: Any, **kwargs: Any) -> Any:
        if not reloaded:
            reloaded.append(True)
            staged.commit(replace=True)
        return resolve(*args, **kwargs)

    monkeypatch.setattr(registry, "resolve", resolve_after_the_reload)
    refusal = BatchExecutor()._revalidate_after_hold(
        CallSpec(index=0, call_id="c-1", mcp_server=SERVER, tool=TOOL, arguments={}),
        get_tool_access_resolver(),
        served,
        "approval-1",
        None,
        registry,
        TENANT,
        lambda _projection, _pin: None,
        target_server_id=SERVER,
    )

    assert reloaded == [True]
    assert refusal is not None
    assert (refusal.error, refusal.error_type) == (
        "Approval no longer valid at dispatch: tool is no longer allowed by policy",
        "ToolAccessDenied",
    )
