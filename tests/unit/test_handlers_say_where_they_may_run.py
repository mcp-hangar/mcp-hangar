"""A handler declares what it does, and that decides where it may run.

Two kinds. A **projection** keeps a local view -- a tool catalogue, a risk
score, a live event feed -- and must run on every replica for every event,
whoever produced it, or it is a view of a third of the system. An **effect**
does something outward -- exports to a SIEM, charges a budget, sends an alert --
and must run only on the instance that produced the event, or three replicas
send three copies of every audit record.

The classification lands *before* the tailer that makes it matter. Afterwards
would mean shipping a period where every replica exports every event, and then
re-deriving on the side what each of a dozen handlers actually does.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.events import DomainEvent
from mcp_hangar.infrastructure.event_bus import EventBus


@dataclass
class _ThingHappened(DomainEvent):
    thing: str = "x"


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


class TestALocallyProducedEventReachesEverything:
    def test_projections_run(self, bus) -> None:
        seen: list[DomainEvent] = []
        bus.subscribe(_ThingHappened, seen.append, kind=HandlerKind.PROJECTION)

        bus.publish(_ThingHappened())

        assert len(seen) == 1

    def test_effects_run(self, bus) -> None:
        # The replica that did the work is the one that exports it.
        seen: list[DomainEvent] = []
        bus.subscribe(_ThingHappened, seen.append, kind=HandlerKind.EFFECT)

        bus.publish(_ThingHappened())

        assert len(seen) == 1


class TestATailedEventReachesProjectionsOnly:
    def test_a_projection_runs_on_a_peers_event(self, bus) -> None:
        seen: list[DomainEvent] = []
        bus.subscribe(_ThingHappened, seen.append, kind=HandlerKind.PROJECTION)

        bus.deliver_tailed(_ThingHappened())

        assert len(seen) == 1

    def test_an_effect_does_not(self, bus) -> None:
        # The test this whole file exists for. Three replicas, one tool call,
        # one CEF record -- not three.
        exported: list[DomainEvent] = []
        bus.subscribe(_ThingHappened, exported.append, kind=HandlerKind.EFFECT)

        bus.deliver_tailed(_ThingHappened())

        assert exported == []

    def test_the_two_are_separated_within_one_event(self, bus) -> None:
        # Both kinds subscribed to the same event type, which is the ordinary
        # case: `ToolInvocationCompleted` feeds the SIEM and the cost ledger
        # *and* whatever keeps a view.
        view: list[DomainEvent] = []
        exported: list[DomainEvent] = []
        bus.subscribe(_ThingHappened, view.append, kind=HandlerKind.PROJECTION)
        bus.subscribe(_ThingHappened, exported.append, kind=HandlerKind.EFFECT)

        bus.deliver_tailed(_ThingHappened())

        assert (len(view), len(exported)) == (1, 0)

    def test_subscribe_to_all_is_filtered_the_same_way(self, bus) -> None:
        # Logging, metrics, alerting and audit are all registered this way, and
        # they are all effects. A replica counting every peer's events would
        # triple every Prometheus total once the scrapes are summed.
        counted: list[DomainEvent] = []
        streamed: list[DomainEvent] = []
        bus.subscribe_to_all(counted.append, kind=HandlerKind.EFFECT)
        bus.subscribe_to_all(streamed.append, kind=HandlerKind.PROJECTION)

        bus.deliver_tailed(_ThingHappened())

        assert (len(counted), len(streamed)) == (0, 1)

    def test_a_tailed_event_is_not_appended_again(self, bus) -> None:
        # It came *out* of the log. Publishing it would write it back, on every
        # replica, and each copy would be tailed in turn.
        appended: list[object] = []

        class _Store:
            can_replay = True

            def append(self, stream_id, events, expected_version):
                appended.extend(events)
                return expected_version + len(events)

        bus.set_event_store(_Store())

        bus.deliver_tailed(_ThingHappened())

        assert appended == []


class TestAnUnclassifiedHandlerIsRefused:
    def test_subscribing_without_a_kind_fails(self, bus) -> None:
        # No default is right for both, and both wrong answers are silent: an
        # unclassified effect exports from three replicas, an unclassified
        # projection leaves two of them stale.
        with pytest.raises(TypeError):
            bus.subscribe(_ThingHappened, lambda event: None)  # type: ignore[call-arg]

    def test_subscribing_to_all_without_a_kind_fails(self, bus) -> None:
        with pytest.raises(TypeError):
            bus.subscribe_to_all(lambda event: None)  # type: ignore[call-arg]


class TestUnsubscribingStillWorks:
    def test_a_handler_can_be_removed_without_naming_its_kind(self, bus) -> None:
        seen: list[DomainEvent] = []
        bus.subscribe(_ThingHappened, seen.append, kind=HandlerKind.EFFECT)

        bus.unsubscribe(_ThingHappened, seen.append)
        bus.publish(_ThingHappened())

        assert seen == []

    def test_a_bound_method_is_matched_by_equality_not_identity(self, bus) -> None:
        # `obj.handle` builds a fresh bound method every time it is evaluated,
        # so subscribing and unsubscribing pass two objects that are equal and
        # not identical. `list.remove` got this right; a rewrite to `is` would
        # leave every websocket subscriber attached for the life of the process.
        class _Subscriber:
            def __init__(self) -> None:
                self.seen: list[DomainEvent] = []

            def handle(self, event: DomainEvent) -> None:
                self.seen.append(event)

        subscriber = _Subscriber()
        bus.subscribe_to_all(subscriber.handle, kind=HandlerKind.EFFECT)

        bus.unsubscribe_from_all(subscriber.handle)
        bus.publish(_ThingHappened())

        assert subscriber.seen == []


class TestTheAuditOfWhatIsAlreadySubscribed:
    def test_every_subscription_in_the_tree_declares_a_kind(self) -> None:
        # The audit is the item, not a follow-up: classifying later means
        # re-deriving on the side what each handler does, which is the work
        # this test makes impossible to skip when a new handler is added.
        import pathlib
        import re

        source_root = pathlib.Path("src/mcp_hangar")
        unclassified: list[str] = []
        pattern = re.compile(r"\.subscribe(?:_to_all)?\(")
        for path in source_root.rglob("*.py"):
            if path.name == "event_bus.py":
                continue
            text = path.read_text(encoding="utf-8")
            for match in pattern.finditer(text):
                # The call may wrap; look at the balanced parentheses after it.
                depth, index = 0, match.end() - 1
                while index < len(text):
                    if text[index] == "(":
                        depth += 1
                    elif text[index] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    index += 1
                if "kind=" not in text[match.start() : index]:
                    unclassified.append(f"{path}:{text[: match.start()].count(chr(10)) + 1}")

        assert unclassified == [], f"handlers subscribed without a kind: {unclassified}"

    def test_the_siem_exporter_is_an_effect(self) -> None:
        # Named specifically because it is the one where a wrong answer is a
        # compliance problem rather than a performance one.
        import pathlib

        text = pathlib.Path("src/mcp_hangar/server/bootstrap/event_handlers.py").read_text(encoding="utf-8")
        block = text[text.index("compliance_handler = ") : text.index("detection_enforcement_handler = ")]

        assert "HandlerKind.PROJECTION" not in block
        assert block.count("HandlerKind.EFFECT") == 3

    def test_the_tool_catalogue_is_a_projection(self) -> None:
        # And this is the one where a wrong answer means a replica serves a
        # third of the tools, depending on where each start request landed.
        import pathlib

        text = pathlib.Path("src/mcp_hangar/server/bootstrap/event_handlers.py").read_text(encoding="utf-8")
        line = next(line for line in text.splitlines() if "tool_projection_handler.handle" in line)

        assert "HandlerKind.PROJECTION" in line
