"""One storage decision, taken whole. A half-Postgres deployment is unrepresentable.

Storage used to be chosen in two independent places -- `auth.storage.driver` and
`event_store.driver` -- so a deployment could keep API keys in PostgreSQL and its
event log in a local SQLite file, and nothing compared them. That is not a
hypothetical: the PostgreSQL auth driver shipped with `tap_store = None`, which
silently disabled tool-access policy management and its replay at startup,
because a partial backend was expressible.

So a backend is a named bundle of every concern, and it is refused if it does not
serve all of them. The rule is what makes "either one or the other" enforceable
rather than a convention.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_hangar.infrastructure.persistence import registry
from mcp_hangar.infrastructure.persistence.registry import (
    IncompletePersistenceBackendError,
    REQUIRED_CONCERNS,
    UnknownPersistenceBackendError,
    available_backends,
    create_backend,
    register_backend_factory,
)


@pytest.fixture
def clean_registry():
    """Registration is process-global; put it back afterwards."""
    saved = dict(registry._FACTORIES)
    yield
    registry._FACTORIES.clear()
    registry._FACTORIES.update(saved)


class _CompleteBackend:
    """A backend that serves everything, built the way a real one is."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}

    def close(self) -> None:
        return None


for _concern in REQUIRED_CONCERNS:
    setattr(_CompleteBackend, _concern, lambda self, _c=_concern: f"adapter:{_c}")


class TestABackendIsCompleteOrItIsRefused:
    def test_a_complete_backend_resolves(self, clean_registry) -> None:
        register_backend_factory("pretend", lambda config: _CompleteBackend(config))

        backend = create_backend("pretend", {"anything": 1})

        assert backend.event_store() == "adapter:event_store"

    def test_a_missing_concern_is_named(self, clean_registry) -> None:
        # The failure this rule exists for: one concern absent, every caller
        # gated on truthiness quietly doing nothing.
        class _Partial(_CompleteBackend):
            tool_access_policy_store = None

        register_backend_factory("partial", lambda config: _Partial(config))

        with pytest.raises(IncompletePersistenceBackendError) as excinfo:
            create_backend("partial", {})

        assert "tool_access_policy_store" in str(excinfo.value)
        assert excinfo.value.missing == ["tool_access_policy_store"]

    def test_incomplete_backend_rejected(self, clean_registry) -> None:
        # Callability is not completeness. A concern method that is present and
        # callable but returns None disables the feature it stores just as
        # surely as an absent one -- which is exactly how the PostgreSQL
        # tool-access policy store shipped: `return None`, gated on truthiness
        # downstream, silently off. The guard must call the concern, not merely
        # find it.
        class _ReturnsNone(_CompleteBackend):
            def tool_access_policy_store(self) -> None:
                return None

        register_backend_factory("returns_none", lambda config: _ReturnsNone(config))

        with pytest.raises(IncompletePersistenceBackendError) as excinfo:
            create_backend("returns_none", {})

        assert "tool_access_policy_store" in str(excinfo.value)
        assert excinfo.value.missing == ["tool_access_policy_store"]

    def test_every_missing_concern_is_listed_at_once(self, clean_registry) -> None:
        # An operator fixing these one error at a time would restart nine times.
        class _Empty:
            def close(self) -> None:
                return None

        register_backend_factory("empty", lambda config: _Empty())

        with pytest.raises(IncompletePersistenceBackendError) as excinfo:
            create_backend("empty", {})

        assert set(excinfo.value.missing) == set(REQUIRED_CONCERNS)

    def test_the_required_set_is_what_the_gateway_persists(self) -> None:
        # A guard on the list itself: adding a persisted concern without adding
        # it here would let a backend omit it and still be accepted.
        assert set(REQUIRED_CONCERNS) == {
            "event_store",
            "dispatch_checkpoint",
            "config_repository",
            "audit_repository",
            "saga_state_store",
            "approval_repository",
            "api_key_store",
            "role_store",
            "tool_access_policy_store",
            "metrics_history_store",
            # Coordination is persisted state like any other: which instance may
            # manage the fleet is a row, and a backend that cannot hold it
            # cannot run more than one gateway.
            "management_lease",
        }


class TestAnUnknownBackendIsLoud:
    def test_it_raises_rather_than_defaulting_to_sqlite(self) -> None:
        # Falling back would start a gateway writing to a local file while its
        # operator believes a shared database is in use -- the worst version of
        # this failure, because it looks like it worked.
        with pytest.raises(UnknownPersistenceBackendError) as excinfo:
            create_backend("mysql", {})

        message = str(excinfo.value)
        assert "mysql" in message
        assert "sqlite" in message, "the known backends belong in the error"
        assert registry.ENTRY_POINT_GROUP in message, "so does how to add one"


class TestBothBackendsAreRegisteredTheSameWay:
    def test_neither_is_privileged(self) -> None:
        # sqlite is not the real one with postgresql bolted beside it: both are
        # registered factories, and the set is what selection reads.
        assert {"sqlite", "postgresql"} <= set(available_backends())

    def test_a_plugin_cannot_quietly_shadow_a_built_in(self, clean_registry) -> None:
        with pytest.raises(ValueError, match="already registered"):
            register_backend_factory("postgresql", lambda config: _CompleteBackend(config))

        register_backend_factory("postgresql", lambda config: _CompleteBackend(config), replace=True)


class TestTheSqliteBackendServesEverything:
    def test_every_concern_builds(self, tmp_path) -> None:
        backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
        try:
            for concern in REQUIRED_CONCERNS:
                assert getattr(backend, concern)() is not None, concern
        finally:
            backend.close()

    def test_a_concern_asked_for_twice_is_the_same_instance(self, tmp_path) -> None:
        # Bootstrap asks more than once. A second call must not open a second
        # connection to the same file.
        backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
        try:
            assert backend.event_store() is backend.event_store()
        finally:
            backend.close()

    def test_the_delivery_mark_lives_with_the_log_it_points_into(self, tmp_path) -> None:
        # A mark in one database and its events in another can outlive them.
        backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
        try:
            backend.event_store()
            backend.dispatch_checkpoint()
            assert (tmp_path / "events.db").exists()
        finally:
            backend.close()
