"""Either one backend or the other. A mixture is refused, not resolved.

Storage was decided in two independent places -- `auth.storage.driver` and
`event_store.driver` -- and nothing compared them, so a deployment could keep its
API keys in PostgreSQL and its event log in a local SQLite file and look
correctly configured from either end.

`persistence.backend` is now the decision. A legacy key that disagrees with it is
a startup refusal, because every precedence rule silently ignores half of what
the operator wrote, and the half that loses is the one they are most sure about.
"""

from __future__ import annotations

import pytest

from mcp_hangar.infrastructure.persistence.registry import UnknownPersistenceBackendError
from mcp_hangar.server.bootstrap.persistence import (
    ConflictingStorageConfigurationError,
    select_backend,
)


class TestNoSelectionKeepsTheOldBehaviour:
    def test_an_absent_persistence_block_selects_nothing(self) -> None:
        # 2.4.0 is released. A storage rewiring must not change what an existing
        # configuration does, so the new key is opt-in.
        assert select_backend({}) is None

    def test_an_empty_backend_name_selects_nothing(self) -> None:
        assert select_backend({"persistence": {"backend": ""}}) is None

    def test_legacy_keys_alone_are_left_alone(self) -> None:
        config = {"auth": {"storage": {"driver": "postgresql"}}, "event_store": {"driver": "sqlite"}}

        # Mixed, and not this module's business until a backend is selected --
        # refusing here would break deployments that upgrade without touching
        # their configuration.
        assert select_backend(config) is None


class TestAMixtureIsRefused:
    def test_auth_naming_a_different_backend_is_a_refusal(self, tmp_path) -> None:
        config = {
            "persistence": {"backend": "sqlite", "sqlite": {"data_dir": str(tmp_path)}},
            "auth": {"storage": {"driver": "postgresql"}},
        }

        with pytest.raises(ConflictingStorageConfigurationError) as excinfo:
            select_backend(config)

        assert "auth.storage.driver" in str(excinfo.value)
        assert excinfo.value.conflicts == [("auth.storage.driver", "postgresql")]

    def test_the_event_store_naming_a_different_backend_is_a_refusal(self, tmp_path) -> None:
        config = {
            "persistence": {"backend": "postgresql"},
            "event_store": {"driver": "sqlite"},
        }

        with pytest.raises(ConflictingStorageConfigurationError):
            select_backend(config)

    def test_every_conflict_is_reported_at_once(self) -> None:
        config = {
            "persistence": {"backend": "postgresql"},
            "auth": {"storage": {"driver": "sqlite"}},
            "event_store": {"driver": "sqlite"},
        }

        with pytest.raises(ConflictingStorageConfigurationError) as excinfo:
            select_backend(config)

        assert len(excinfo.value.conflicts) == 2

    def test_an_agreeing_legacy_key_is_not_a_conflict(self, tmp_path) -> None:
        # Saying the same thing twice is redundant, not contradictory.
        config = {
            "persistence": {"backend": "sqlite", "sqlite": {"data_dir": str(tmp_path)}},
            "auth": {"storage": {"driver": "sqlite"}},
        }

        backend = select_backend(config)
        try:
            assert backend is not None
        finally:
            backend.close()  # type: ignore[union-attr]

    def test_memory_is_not_a_backend_and_never_conflicts(self, tmp_path) -> None:
        # `memory` is a testing choice, not a storage backend. Treating it as a
        # conflict would make every test configuration unstartable.
        config = {
            "persistence": {"backend": "sqlite", "sqlite": {"data_dir": str(tmp_path)}},
            "auth": {"storage": {"driver": "memory"}},
            "event_store": {"driver": "memory"},
        }

        backend = select_backend(config)
        try:
            assert backend is not None
        finally:
            backend.close()  # type: ignore[union-attr]


class TestTheBackendGetsItsOwnConfigurationOnly:
    def test_the_named_block_is_what_reaches_the_factory(self, tmp_path) -> None:
        # `data_dir` means nothing to PostgreSQL and `host` means nothing to
        # SQLite; neither backend should have to know the other's vocabulary.
        data_dir = tmp_path / "chosen"
        config = {
            "persistence": {
                "backend": "sqlite",
                "sqlite": {"data_dir": str(data_dir)},
                "postgresql": {"host": "db.internal.example"},
            }
        }

        backend = select_backend(config)
        try:
            backend.event_store()  # type: ignore[union-attr]
            assert (data_dir / "events.db").exists()
        finally:
            backend.close()  # type: ignore[union-attr]

    def test_an_unknown_backend_is_refused_rather_than_defaulted(self) -> None:
        with pytest.raises(UnknownPersistenceBackendError):
            select_backend({"persistence": {"backend": "mysql"}})
