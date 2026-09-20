"""A publish that keeps no record is one module's door, and stays one module's door.

#1410 added `EventBus.publish_local`: deliver a domain event to this replica's
handlers and deliberately never append it to the shared log. The decision is
right -- a group's rotation and circuit breaker are facts about this pod, not
about the group (#1358) -- but it put a non-persisting publish door on
`IEventBus` and on the `server.context` Protocol, so every holder of a bus can
see it.

That is the shape #772 catalogued, and
`tests/unit/test_publish_persists_by_construction.py` is the guard that came out
of it: there were two publish methods, one of them forgot, and thirty-four call
sites took the forgetful one. Its lesson is that "which method should I call" is
not a question a caller gets right reliably, and getting it wrong is silent.

`publish_local` is far better defended than that one was -- it is named for what
it does, and `test_a_groups_events_go_out_on_the_path_that_raised_them.py` makes
a new group event get classified into exactly one register on purpose. But that
test pins *which events* may go local. This one pins *who may send them*: a
future caller handing some other aggregate's event to `publish_local` gets
silent non-persistence and, without this, no failing test.
"""

from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[2] / "src"

#: The one module that may publish an event without recording it. It is the
#: module that decides which events those are, which is why it is the one.
CALLER = "mcp_hangar/application/group_events.py"

_ADVICE = """
`publish_local` delivers to this replica's handlers and appends nothing, so an
event that takes it has no record anywhere. A new event has two legitimate ways
out, and neither is a second call site:

  - classify it in `application/group_events.py` -- put it in
    REPLICA_LOCAL_GROUP_EVENTS or SHARED_GROUP_EVENTS and let
    `publish_group_events` route it; or
  - call `publish`, which derives the stream from the event and keeps the record.

If a second caller is genuinely right, widen this test on purpose and say why.
"""


def _call_sites(root: pathlib.Path = SRC) -> list[str]:
    """Every module under *root* that calls `publish_local`, once per call.

    A call, specifically: an `ast.Call` whose callee is named `publish_local`.
    The three `def publish_local` -- the concrete bus, the port, and the context
    Protocol -- are `FunctionDef`s; the `TypeError` in `infrastructure.event_bus`
    names the method twice but does so in a string constant; docstring prose is a
    constant too. None of them is a Call, so a grep's three false positives are
    structurally impossible here, while a real caller counts whether it is
    written `bus.publish_local(e)` or bound and called as `publish_local(e)`.
    """
    sites: list[str] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a parse failure is a bigger problem
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if callee == "publish_local":
                sites.append(path.relative_to(root).as_posix())
    return sites


class TestOnlyGroupEventsPublishesWithoutRecording:
    def test_it_is_the_only_caller_under_src(self) -> None:
        sites = _call_sites()

        assert sites == [CALLER], f"expected `publish_local` to be called once, from {CALLER}; found {sites}\n{_ADVICE}"

    def test_the_caller_is_that_module_and_not_merely_a_single_one(self) -> None:
        """Counting alone would pass if the one call moved somewhere with no claim to it.

        `application.group_events` may publish locally because it is what sorts a
        group's events into the two registers. A lone call anywhere else is the
        same silent non-persistence, arrived at by relocation instead of by
        addition, so the assertion above is on the module, not on the number.
        """
        assert CALLER in _call_sites()


class TestTheWalkCountsCallsAndNothingElse:
    """Guards the guard: a matcher that found nothing would pass the tests above."""

    def test_a_definition_a_message_and_prose_are_not_call_sites(self, tmp_path: pathlib.Path) -> None:
        # Everything `grep -rn publish_local src/` reports today except the one
        # real call -- three definitions, the TypeError that names it twice, and
        # a docstring mentioning it.
        (tmp_path / "decoys.py").write_text(
            '"""Prose about publish_local and when to use it."""\n'
            "class Bus:\n"
            "    def publish_local(self, event):\n"
            '        """Deliver without recording."""\n'
            '        raise TypeError("publish_local() takes one event. Call publish_local() for each.")\n',
            encoding="utf-8",
        )

        assert _call_sites(tmp_path) == []

    def test_a_real_call_is_found_however_it_is_spelled(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "caller.py").write_text(
            "def drain(bus, events, publish_local):\n    bus.publish_local(events[0])\n    publish_local(events[1])\n",
            encoding="utf-8",
        )

        assert _call_sites(tmp_path) == ["caller.py", "caller.py"]
