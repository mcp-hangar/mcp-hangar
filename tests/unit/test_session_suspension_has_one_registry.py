"""Enforcement and the HTTP routes must suspend into the same registry.

`DetectionEnforcementHandler` used to do this:

    from ...server.api.sessions import _suspended_sessions
    _suspended_sessions.add(session_id)

Three problems in three lines: an application handler depending on the delivery
layer, reaching past the underscore into another module's private state, behind
a function-local import that hid the edge from a reader. The import-contract
ledger carried it as the only application -> delivery edge in the tree.

The store was never route code to begin with -- it is a bounded, TTL-expiring,
thread-safe cache, which is an adapter. It moved to infrastructure behind
`ISessionSuspensionRegistry`, and the handler is handed the same instance the
routes use.

"The same instance" is the part worth testing. Two registries would not raise:
a session suspended by a detection rule would stay servable, and one suspended
over HTTP would be invisible to enforcement. The two would just quietly
disagree, which is the failure mode a shared global was accidentally preventing.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from mcp_hangar.application.event_handlers import DetectionEnforcementHandler
from mcp_hangar.domain.events import DetectionRuleMatched
from mcp_hangar.infrastructure.session_suspension import InMemorySessionSuspensionRegistry
from mcp_hangar.server.api.sessions import get_session_suspension_registry, is_session_suspended

SRC = pathlib.Path(__file__).resolve().parents[2] / "src"


class _Bus:
    def __init__(self) -> None:
        self.published: list[object] = []

    def publish(self, event: object) -> None:
        self.published.append(event)


def _match(action: str, session_id: str = "s-1") -> DetectionRuleMatched:
    return DetectionRuleMatched(
        rule_id="r-1",
        rule_name="rule",
        severity="critical",
        session_id=session_id,
        mcp_server_id="srv-1",
        matched_tools=("read",),
        recommended_action=action,
    )


class TestTheHandlerWritesWhereTheRoutesRead:
    def test_a_rule_suspension_is_visible_to_the_serving_path(self):
        """The end-to-end property: enforcement suspends, the server sees it."""
        registry = get_session_suspension_registry()
        registry.unsuspend("s-shared")
        handler = DetectionEnforcementHandler(event_bus=_Bus(), session_registry=registry)

        handler.handle(_match("suspend", "s-shared"))

        assert is_session_suspended("s-shared") is True
        registry.unsuspend("s-shared")

    def test_a_separate_registry_does_not_leak_into_the_serving_path(self):
        """Pins that the wiring is what connects them, not a hidden global."""
        handler = DetectionEnforcementHandler(event_bus=_Bus(), session_registry=InMemorySessionSuspensionRegistry())

        handler.handle(_match("suspend", "s-isolated"))

        assert is_session_suspended("s-isolated") is False


class TestTheRegistryIsRequired:
    """A forgotten wiring must fail at construction, not silently at runtime.

    Defaulting it to None and raising inside `_suspend_session` would put the
    failure inside this handler's fault barrier -- one log line, and enforcement
    that quietly does nothing. That shape (a fallback beside an injected
    dependency) is exactly what hid missing wiring elsewhere in this codebase.
    """

    def test_constructing_without_one_raises(self):
        with pytest.raises(TypeError):
            DetectionEnforcementHandler(event_bus=_Bus())  # type: ignore[call-arg]


class TestTheApplicationLayerStaysOutOfDelivery:
    def test_the_handler_does_not_import_the_sessions_module(self):
        """Including function-local imports, which grimp sees but a reader may not."""
        source = (SRC / "mcp_hangar/application/event_handlers/detection_handler.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        offenders = [
            ast.unparse(n)
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module and "api.sessions" in n.module
        ]
        assert offenders == [], f"detection_handler imports the delivery layer again: {offenders}"


class TestBootstrapHandsOverTheSharedRegistry:
    """The one thing the tests above cannot see.

    Every other test in this file constructs the handler itself, so all of them
    keep passing if BOOTSTRAP is changed to build a private registry -- which is
    precisely the two-registries-quietly-disagreeing failure. Probing found this
    gap: swapping the bootstrap wiring for a fresh `InMemorySessionSuspensionRegistry`
    left the whole detection-enforcement suite green.

    So this asserts the call site itself.
    """

    def test_the_wiring_passes_the_module_accessor(self):
        source = (SRC / "mcp_hangar/server/bootstrap/event_handlers.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "DetectionEnforcementHandler"
        ]
        assert len(calls) == 1, f"expected exactly one construction site, found {len(calls)}"

        passed = {kw.arg: ast.unparse(kw.value) for kw in calls[0].keywords}
        assert passed.get("session_registry") == "get_session_suspension_registry()", (
            "bootstrap does not hand the enforcement handler the registry the HTTP routes use "
            f"(got {passed.get('session_registry')!r}); a second instance means a rule-suspended "
            "session stays servable and an HTTP-suspended one is invisible to enforcement, "
            "with nothing raising"
        )


class TestTheAdapterKeptItsBound:
    """The move must not drop the property a security test was written for.

    An unbounded suspended-session store is a memory-growth channel the caller
    controls: every entry is created by traffic they can generate.
    """

    def test_it_evicts_rather_than_growing(self):
        registry = InMemorySessionSuspensionRegistry(maxsize=3)
        for i in range(10):
            registry.suspend(f"s-{i}")

        still_held = [i for i in range(10) if registry.is_suspended(f"s-{i}")]
        assert len(still_held) <= 3, f"registry grew past its bound: {len(still_held)} entries held"
        assert registry.is_suspended("s-9"), "eviction dropped the newest entry rather than the oldest"

    def test_an_expired_suspension_stops_applying(self):
        registry = InMemorySessionSuspensionRegistry(ttl=-1.0)
        registry.suspend("s-old")
        assert registry.is_suspended("s-old") is False
