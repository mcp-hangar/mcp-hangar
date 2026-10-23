"""A server's peer identity survives the fleet snapshot, and an older release declines its row (#1946).

ADR-030 §10 and §4e. The snapshot carried no `tls`, so a server rebuilt from it
on restart or on a peer replica came back without its key list and was served
as a plain one. And a release that does not know the field would rebuild it
plainly from the same row; storing it under a mode that release does not know
makes it decline the row instead.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from mcp_hangar.domain.contracts.persistence import PEER_KEY_ROW_MODE, McpServerConfigSnapshot
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.services.fleet_snapshot import server_from_snapshot, snapshot_of
from mcp_hangar.domain.value_objects import McpServerMode
from mcp_hangar.infrastructure.persistence import Database, DatabaseConfig
from mcp_hangar.infrastructure.persistence.config_repository import (
    InMemoryMcpServerConfigRepository,
    SQLiteMcpServerConfigRepository,
)

PIN = "sha256:" + "e" * 64
TLS = {"peer_identity": {"key_ref": "lineage", "spki_sha256": [PIN]}, "max_connection_age": "30m"}


def _pinned() -> McpServer:
    return McpServer(mcp_server_id="payments", mode="remote", endpoint="https://10.0.0.5:8443", tls=dict(TLS))


class TestTheSnapshotCarriesTheTlsBlock:
    def test_a_rebuilt_server_has_the_same_peer_identity(self) -> None:
        rebuilt = server_from_snapshot(snapshot_of(_pinned()))

        assert rebuilt.upstream_identity() == "peer-key:lineage"
        assert rebuilt._tls_config == TLS
        assert rebuilt.mode is McpServerMode.REMOTE

    def test_it_round_trips_through_storage_on_both_paths(self, tmp_path) -> None:
        database = Database(DatabaseConfig(path=str(tmp_path / "fleet.db")))
        asyncio.run(database.initialize())
        sqlite = SQLiteMcpServerConfigRepository(database)
        memory = InMemoryMcpServerConfigRepository()
        for repository in (sqlite, memory):
            asyncio.run(repository.save(snapshot_of(_pinned())))
            stored = asyncio.run(repository.get("payments"))

            assert stored is not None and stored.mode == "remote" and stored.tls == TLS
            assert server_from_snapshot(stored).upstream_identity() == "peer-key:lineage"

    def test_a_server_without_tls_is_unchanged(self) -> None:
        plain = McpServer(mcp_server_id="plain", mode="remote", endpoint="https://plain:8443")
        stored = snapshot_of(plain).to_dict()

        assert stored["mode"] == "remote"
        assert stored["tls"] is None
        assert server_from_snapshot(McpServerConfigSnapshot.from_dict(stored)).upstream_identity().startswith("remote:")

    def test_a_row_from_before_the_field_reads_back_without_tls(self) -> None:
        old = {"mcp_server_id": "plain", "mode": "remote", "endpoint": "https://plain:8443"}

        assert McpServerConfigSnapshot.from_dict(old).tls is None


class TestTheRowIsDeclinedByAReleaseThatDoesNotKnowIt:
    def test_the_stored_mode_is_one_the_previous_release_cannot_read(self) -> None:
        stored = json.loads(json.dumps(snapshot_of(_pinned()).to_dict()))

        assert stored["mode"] == PEER_KEY_ROW_MODE
        # What the previous release does with the row: build the server from the
        # stored mode. It raises, so recovery skips the row and the projection's
        # fault barrier drops the delivery; nothing serves it as a plain server.
        with pytest.raises(ValueError):
            McpServerMode.normalize(stored["mode"])

    def test_this_release_reads_it_back_as_remote(self) -> None:
        stored = snapshot_of(_pinned()).to_dict()

        assert McpServerConfigSnapshot.from_dict(stored).mode == "remote"

    def test_an_encoded_row_without_its_peer_identity_is_refused(self) -> None:
        stored = snapshot_of(_pinned()).to_dict()
        stored["tls"] = {"verify_ssl": True}

        with pytest.raises(ValueError, match=f"{PEER_KEY_ROW_MODE} row without tls.peer_identity"):
            McpServerConfigSnapshot.from_dict(stored)

    def test_an_encoded_row_with_a_malformed_peer_identity_is_refused(self) -> None:
        stored = snapshot_of(_pinned()).to_dict()
        stored["tls"] = {"peer_identity": {"key_ref": "lineage", "spki_sha256": ["not-a-pin"]}}

        with pytest.raises(ValueError, match="is not a key pin"):
            McpServerConfigSnapshot.from_dict(stored)
