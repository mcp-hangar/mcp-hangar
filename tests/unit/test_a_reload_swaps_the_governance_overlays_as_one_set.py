"""A reload swaps the governance overlays as one set (#1431).

A configuration's tool-access policies, group policies included, its
withdrawals and pins, and its `header_exposure` blocks live in three
registries, and a reload swaps each one whole (#1424). It swapped them one
after another with nothing tying them together. So a decision that read two of
them during a reload could apply the new value of one with the previous value
of another: a state that neither file declares. The commit now swaps them as
one generation, and a decision read through `read_as_one_set` is made against
one configuration's overlays in full.

A call's decisions include a tool call's (#1431): the executor's decision
for `hangar_call` -- access, withdrawal and pins together -- and the front
door's listing and routing.

The servers and the groups, with each group's membership, are swapped in the
same set (#1488). The files move a server from one group to the other and
rebuild it, and the server the previous file built carries an L7 egress
policy: a call to it reads the groups that own it, and the L7 policy it is
routed on, with the overlays.

The two files below differ in every overlay, and each test checks a call
against both of them: the one the reload replaces and the one it puts in force.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.policies import header_exposure
from mcp_hangar.domain.policies.egress_l7 import L7Policy
from mcp_hangar.domain.policies.header_exposure import clear_header_exposure_policies, get_header_exposure_policy
from mcp_hangar.domain.services.governance_overlays import read_as_one_set, swapping
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.fastmcp_server import flat_tool_projection
from mcp_hangar.fastmcp_server.flat_tool_projection import is_governed_allowed
from mcp_hangar.server import config as server_config
from mcp_hangar.server.state import GROUPS, get_runtime
from mcp_hangar.server.tools.batch import executor

SERVER = "store"
GROUP = "g"
MEMBER = "m1"
OTHER_GROUP = "g2"
OTHER_MEMBER = "m2"
#: A server the reload moves from `g` to `g2`, and rebuilds.
MOVER = "mover"
TENANT = "tenant:a"


def _server(**extra: Any) -> dict[str, Any]:
    """A server that is never started: the tests only build and commit it."""
    return {"mode": "subprocess", "command": ["python", "-c", "pass"], **extra}


def _file(*, deny: str, withdrawn: str, pin: str, exposure: str, group_deny: str, mover_in: str) -> dict[str, Any]:
    members: dict[str, list[dict[str, Any]]] = {
        GROUP: [{"id": MEMBER, **_server()}],
        OTHER_GROUP: [{"id": OTHER_MEMBER, **_server()}],
    }
    members[mover_in].append({"id": MOVER})
    return {
        SERVER: _server(
            tools={"deny_list": [deny]},
            tool_projection={"withdrawn": [withdrawn], "pins": {"p": pin}},
            header_exposure={"deny_annotated": [exposure]},
        ),
        # Built from another command in each file, so a reload replaces it.
        MOVER: {"mode": "subprocess", "command": ["python", "-c", f"pass  # in {mover_in}"]},
        GROUP: {
            "mode": "group",
            "auto_start": False,
            "tools": {"deny_list": [group_deny]},
            "members": members[GROUP],
        },
        OTHER_GROUP: {"mode": "group", "auto_start": False, "members": members[OTHER_GROUP]},
    }


#: The file in force. Moving `t` from the deny list to the withdrawals is the
#: edit where a mix shows: the new policy with the old withdrawals allows `t`.
#: `mover` is in `g`, which denies `ga`.
OLD = _file(deny="t", withdrawn="w", pin="a" * 64, exposure="*old*", group_deny="ga", mover_in=GROUP)
#: The file a reload puts in force. `mover` is in `g2`, and `g` denies `gb`
#: instead: the new policies with the old membership allow `mover` `ga`.
NEW = _file(deny="u", withdrawn="t", pin="b" * 64, exposure="*new*", group_deny="gb", mover_in=OTHER_GROUP)


def _tool_call(mcp_server: str, tool: str) -> Any:
    """The decision the executor makes for a `hangar_call` of *tool* on *mcp_server*, read as it is."""
    return executor._decide_governance(
        get_tool_access_resolver(),
        get_tool_projection_registry(),
        mcp_server,
        tool,
        TENANT,
        is_group=False,
        target_server_id=mcp_server,
        servers=get_runtime().repository,
    )


def _decisions() -> tuple[Any, ...]:
    """What a call reads off every overlay: the policy, withdrawals, pin, `header_exposure` and group policy.

    Then what a tool call decides from them (#1431): `hangar_call`'s access,
    withdrawal and pins, for a server and for a group member, and the front
    door's flat map, which is both its listing and its routing. Then, for the
    server a reload moves between groups (#1488), the groups `hangar_call`
    reads as owning it, its access, and whether it carries an L7 policy, and
    the front door's access.
    """
    resolver = get_tool_access_resolver()
    registry = get_tool_projection_registry()
    pin = registry.resolve_pin(SERVER, "p", None)
    exposure = get_header_exposure_policy(SERVER)
    call, pinned, member = _tool_call(SERVER, "t"), _tool_call(SERVER, "p"), _tool_call(MEMBER, "ga")
    mover = _tool_call(MOVER, "ga")
    return (
        resolver.is_tool_allowed(SERVER, "t"),
        resolver.is_tool_allowed(SERVER, "u"),
        registry.is_withdrawn(SERVER, "t"),
        registry.is_withdrawn(SERVER, "w"),
        pin.sha256 if pin is not None else None,
        exposure.deny_annotated if exposure is not None else None,
        is_governed_allowed(MEMBER, "ga", kind="tool", tenant_id=TENANT),
        is_governed_allowed(MEMBER, "gb", kind="tool", tenant_id=TENANT),
        (call.allowed, call.withdrawn),
        tuple(pin.sha256 for _scope, pin, _mode in pinned.pins),
        member.allowed,
        tuple(sorted(flat_tool_projection._flat_map_now(TENANT))),
        (mover.owning_groups, mover.allowed, mover.l7_policy is not None),
        is_governed_allowed(MOVER, "ga", kind="tool", tenant_id=TENANT),
    )


def _reset() -> None:
    reset_tool_access_resolver()
    reset_tool_projection_registry()
    clear_header_exposure_policies()
    server_config._BUILT_FROM.clear()
    repository = get_runtime().repository
    for mcp_server_id in (SERVER, MEMBER, OTHER_MEMBER, MOVER):
        if repository.exists(mcp_server_id):
            repository.remove(mcp_server_id)
    GROUPS.clear()


@dataclass(frozen=True)
class _Files:
    """Both files built, and what a call decides under each."""

    old: Any
    new: Any
    decided_under_old: tuple[Any, ...]
    decided_under_new: tuple[Any, ...]


@pytest.fixture
def files() -> Any:
    _reset()
    server_config.load_config(OLD)
    # What the front door lists: `t`, `u` and `w` are each denied or withdrawn by one file.
    get_tool_projection_registry().build_from_tools(
        SERVER, [ToolSchema(name=name, description=name, input_schema={}) for name in ("t", "u", "w")]
    )
    old, new = server_config.build_config(OLD), server_config.build_config(NEW)
    assert old.servers[MOVER] is not new.servers[MOVER], "each file builds its own `mover`"
    # As the REST endpoint sets it: on the running server, not in the file.
    old.servers[MOVER].set_l7_policy(L7Policy())
    decided_under_old = _decisions()
    new.commit(replace=True)
    decided_under_new = _decisions()
    old.commit(replace=True)
    assert all(a != b for a, b in zip(decided_under_old, decided_under_new, strict=True)), "differ in every overlay"
    assert _decisions() == decided_under_old
    yield _Files(old, new, decided_under_old, decided_under_new)
    _reset()


def test_a_call_made_between_two_overlay_swaps_sees_one_set(files: _Files, monkeypatch: pytest.MonkeyPatch) -> None:
    """The acceptance case: a reload paused after the policies, withdrawals and pins, before `header_exposure`."""
    paused, resume = threading.Event(), threading.Event()
    adopt = header_exposure.adopt_header_exposure_policies

    def pause_then_adopt(*args: Any, **kwargs: Any) -> None:
        paused.set()
        resume.wait(5)
        adopt(*args, **kwargs)

    monkeypatch.setattr(header_exposure, "adopt_header_exposure_policies", pause_then_adopt)
    answers: list[tuple[Any, ...]] = []
    call = threading.Thread(target=lambda: answers.append(read_as_one_set(_decisions)))
    reload = threading.Thread(target=files.new.commit, kwargs={"replace": True})
    reload.start()
    try:
        assert paused.wait(5)
        # The window itself: every overlay is the new file's but `header_exposure`.
        assert _decisions() not in (files.decided_under_old, files.decided_under_new)

        call.start()
        call.join(0.2)
        assert call.is_alive() and not answers, "the call waits out the swap in progress"
    finally:
        resume.set()
        reload.join(5)
    call.join(5)

    assert answers == [files.decided_under_new]


def test_a_decision_that_straddles_a_reload_is_made_again(files: _Files) -> None:
    """The issue's example: `tools.deny_list: [t]` becomes `withdrawn: [t]`, and the reload lands between two reads."""
    resolver, registry = get_tool_access_resolver(), get_tool_projection_registry()
    runs: list[bool] = []

    def t_allowed() -> bool:
        not_withdrawn = not registry.is_withdrawn(SERVER, "t")
        if not runs:
            files.new.commit(replace=True)
        allowed = not_withdrawn and resolver.is_tool_allowed(SERVER, "t")
        runs.append(allowed)
        return allowed

    assert read_as_one_set(t_allowed) is False
    assert runs == [True, False], "the first run took the old withdrawals with the new policy, and was made again"


def test_no_call_sees_a_mix_while_reloads_swap_between_two_files(
    files: _Files, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Callers read every overlay in a loop while reloads swap between the two files."""
    # A moment after each server a reload puts in force, so the callers also
    # read while the servers and groups are swapped, not only the overlays (#1488).
    repository = get_runtime().repository
    add = repository.add

    def add_then_yield(*args: Any, **kwargs: Any) -> None:
        add(*args, **kwargs)
        time.sleep(0.0001)

    monkeypatch.setattr(repository, "add", add_then_yield)
    stop = threading.Event()
    seen: list[list[tuple[Any, ...]]] = [[] for _ in range(4)]

    reads = [0]
    reads_lock = threading.Lock()

    def call(answers: list[tuple[Any, ...]]) -> None:
        while not stop.is_set():
            answers.append(read_as_one_set(_decisions))
            with reads_lock:
                reads[0] += 1

    switch_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    callers = [threading.Thread(target=call, args=(answers,)) for answers in seen]
    try:
        for caller in callers:
            caller.start()
        for reload in range(200):
            (files.new if reload % 2 == 0 else files.old).commit(replace=True)
            # Let a caller finish one read under this file before the next swap.
            # A read that a swap overlaps is made again, so back-to-back swaps
            # under a slow tracer (coverage) could starve every caller until the
            # loop ends, and they would only ever see the last file. A read that
            # completes after the commit returned was made entirely under it.
            with reads_lock:
                before = reads[0]
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                with reads_lock:
                    if reads[0] > before:
                        break
                time.sleep(0.0002)
    finally:
        stop.set()
        for caller in callers:
            caller.join(10)
        sys.setswitchinterval(switch_interval)

    answers = [answer for per_caller in seen for answer in per_caller]
    mixed = [answer for answer in answers if answer not in (files.decided_under_old, files.decided_under_new)]
    assert answers, "the callers made no call"
    assert mixed == []
    assert files.decided_under_old in answers and files.decided_under_new in answers


def test_a_swap_that_raises_still_ends() -> None:
    with pytest.raises(RuntimeError), swapping():
        raise RuntimeError("a commit failed part-way")

    assert read_as_one_set(lambda: "settled") == "settled"
