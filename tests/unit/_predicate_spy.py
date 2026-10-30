"""The spy that records which security predicates ran, and with what scope (#1386, #1786).

Shared by ``test_security_predicates_are_reachable.py``, which drives the served
HTTP app in the test process, and ``_predicate_reachability_harness.py``, which
drives the surfaces that need a process of their own (the facade and stdio), so
both hold their paths to the same recorder.

It is a profile hook matched on each predicate's code object. It sees a call
however the caller reached the function: an import alias, the executor's tuple
of gates, or a closure. It also sees calls on every thread started while it is
installed, which matters because ``hangar_call`` runs its calls on a worker pool.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from types import FrameType
from typing import Any

from tests.unit._security_predicates import Predicate

#: What a recording holds: which predicates ran, as (predicate, the `by` value),
#: and their scope arguments, as (predicate, role, (parameter, value)).
Calls = set[tuple[str, Any]]
Scopes = set[tuple[str, str, tuple[str, Any]]]


def plain(value: Any) -> Any:
    """A recorded argument: a string or None as it is, anything else as its repr."""
    return value if value is None or isinstance(value, str) else repr(value)


def argument(frame_locals: dict[str, Any], param: str) -> Any:
    """The value of *param* in the call: a parameter, or an attribute path under one (``principal.tenant_id``)."""
    root, *attributes = param.split(".")
    value = frame_locals.get(root)
    for attribute in attributes:
        value = getattr(value, attribute, None)
    return value


class Spy:
    """Which registered predicates ran, with the argument naming the call site."""

    def __init__(self, predicates: Iterable[Predicate]) -> None:
        # Keyed by identity. The functions are module attributes, so their code
        # objects, and so these ids, live as long as the process.
        self._watched = {id(p.function.__code__): p for p in predicates}
        self._lock = threading.Lock()
        self._recording = False
        self._calls: Calls = set()
        self._scopes: Scopes = set()

    def _hook(self, frame: FrameType, event: str, arg: Any) -> None:
        if event not in ("call", "return") or not self._recording:
            return
        predicate = self._watched.get(id(frame.f_code))
        if predicate is None:
            return
        if event == "return":
            # What a predicate whose answer is a scope answered. None also
            # when it raised, which is a refusal, not a scope.
            if predicate.returns is not None:
                with self._lock:
                    self._scopes.add((predicate.name, predicate.returns, ("return", plain(arg))))
            return
        value = frame.f_locals.get(predicate.by) if predicate.by else None
        scoped = [(param, role, plain(argument(frame.f_locals, param))) for param, role in predicate.scope.items()]
        with self._lock:
            self._calls.add((predicate.name, value))
            self._scopes.update((predicate.name, role, (param, held)) for param, role, held in scoped)

    @contextmanager
    def installed(self) -> Iterator[None]:
        """Hook this thread and every thread started while installed."""
        previous = sys.getprofile(), threading.getprofile()
        sys.setprofile(self._hook)
        threading.setprofile(self._hook)
        try:
            yield
        finally:
            sys.setprofile(previous[0])
            threading.setprofile(previous[1])

    @contextmanager
    def recording(self) -> Iterator[tuple[Calls, Scopes]]:
        calls: Calls = set()
        scopes: Scopes = set()
        with self._lock:
            self._calls = calls
            self._scopes = scopes
            self._recording = True
        try:
            yield calls, scopes
        finally:
            with self._lock:
                self._recording = False


def to_json(calls: Calls, scopes: Scopes) -> dict[str, Any]:
    """A recording, as JSON a harness can write."""
    return {
        "calls": sorted(([name, value] for name, value in calls), key=repr),
        "scopes": sorted(([name, role, param, value] for name, role, (param, value) in scopes), key=repr),
    }


def from_json(
    recorded: dict[str, Any],
) -> tuple[frozenset[tuple[str, Any]], frozenset[tuple[str, str, tuple[str, Any]]]]:
    """A recording a harness wrote, in the tuple shapes the checks compare."""
    calls = frozenset((name, value) for name, value in recorded["calls"])
    scopes = frozenset((name, role, (param, value)) for name, role, param, value in recorded["scopes"])
    return calls, scopes
