"""A domain event must not re-run the base constructor after its fields are set.

`DomainEvent` used to be a plain class with an `__init__` that minted
`event_id` and `occurred_at`, so every one of the 99 subclasses carried an
identical three-line `__post_init__` whose whole body was `super().__init__()`.

Now that the base is a `kw_only` dataclass, that call is not merely redundant --
it is destructive. `__post_init__` runs AFTER the generated `__init__` has
assigned the fields, so calling the base constructor again overwrites a
restored `event_id` with a fresh uuid and a restored `occurred_at` with `now`.
Replay would silently re-date history and break idempotency for any consumer
keyed on event id.

That is exactly what happened during the refactor: nine `Group*` events live in
`domain/model/mcp_server_group.py` rather than `domain/events/`, so a survey
scoped to the events package missed them, and they clobbered their own identity
until the serialization fuzz test caught it.

Which is why this guard is tree-wide and matches on the base class rather than
on a directory.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[2] / "src"


def _event_classes_with_post_init() -> list[tuple[str, str, list[str]]]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - nothing in src should fail to parse
            continue
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            if not any(isinstance(b, ast.Name) and b.id == "DomainEvent" for b in cls.bases):
                continue
            for fn in cls.body:
                if isinstance(fn, ast.FunctionDef) and fn.name == "__post_init__":
                    found.append((str(path.relative_to(SRC)), cls.name, [ast.unparse(s) for s in fn.body]))
    return found


class TestNoEventReconstructsItsOwnIdentity:
    def test_no_subclass_calls_the_base_constructor(self):
        offenders = [
            f"{path}::{name}" for path, name, body in _event_classes_with_post_init() if "super().__init__()" in body
        ]
        assert offenders == [], (
            f"{len(offenders)} domain event(s) call super().__init__() from __post_init__, "
            "which overwrites a replayed event's restored event_id and occurred_at "
            f"with fresh values: {offenders}"
        )

    def test_the_surviving_post_inits_only_normalise_fields(self):
        """The two that remain earn it; a third should have to justify itself.

        Both exist to turn an explicit `None` into an empty container, because
        the hand-written constructors they replaced did `x or {}` and consumers
        index the value without a None check.
        """
        remaining = {f"{path}::{name}" for path, name, _ in _event_classes_with_post_init()}
        assert remaining == {
            "mcp_hangar/domain/events/enforcement.py::EgressPolicyViolationObserved",
            "mcp_hangar/domain/events/invocation.py::ToolInvocationRequested",
        }, f"the set of events with a __post_init__ changed: {sorted(remaining)}"


class TestTheBaseKeepsItsContract:
    def test_identity_is_keyword_only_so_subclasses_stay_positional(self):
        """The whole reason the base can be a dataclass at all.

        Ordinary inherited fields with defaults would force every subclass field
        to have one too. Keyword-only fields sit outside that ordering, so the
        92 existing positional signatures are untouched.
        """
        import dataclasses

        from mcp_hangar.domain.events import McpServerStarted

        base_fields = {f.name: f for f in dataclasses.fields(McpServerStarted) if f.name in ("event_id", "occurred_at")}
        assert base_fields, "the identity fields are no longer dataclass fields"
        for name, f in base_fields.items():
            assert f.kw_only, f"{name} is not kw_only; subclass field ordering would break"

        # Positional construction, the thing kw_only protects.
        event = McpServerStarted("srv", "subprocess", 3, 1.0)
        assert event.mcp_server_id == "srv"

    def test_identity_stays_out_of_equality(self):
        """Preserves the semantics from before the base was a dataclass.

        Back then these were not fields, so a subclass's generated `__eq__`
        compared the payload alone. Widening equality to include identity is a
        defensible change but a separate decision from removing boilerplate, and
        it would alter behaviour silently at every site comparing events.
        """
        from mcp_hangar.domain.events import McpServerStarted

        a = McpServerStarted("srv", "subprocess", 3, 1.0)
        b = McpServerStarted("srv", "subprocess", 3, 1.0)
        assert a.event_id != b.event_id
        assert a == b

    @pytest.mark.parametrize(("event_id", "occurred_at"), [("stored", 123.0), (None, None)])
    def test_rehydrate_still_honours_the_none_convention(self, event_id, occurred_at):
        from mcp_hangar.domain.events import McpServerStarted

        event = McpServerStarted.rehydrate(
            event_id, occurred_at, mcp_server_id="srv", mode="m", tools_count=0, startup_duration_ms=0.0
        )
        if event_id is None:
            assert event.event_id and event.occurred_at > 0
        else:
            assert event.event_id == event_id
            assert event.occurred_at == occurred_at

    def test_to_dict_still_carries_the_identity(self):
        """The serializer round-trip depends on these keys being present."""
        from mcp_hangar.domain.events import McpServerStarted

        payload = McpServerStarted("srv", "subprocess", 3, 1.0).to_dict()
        assert {"event_type", "event_id", "occurred_at"} <= set(payload)
