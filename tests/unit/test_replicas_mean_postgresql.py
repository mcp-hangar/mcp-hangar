"""In a cluster, storage a peer cannot reach is refused rather than warned about.

Three replicas on SQLite do not collide. Each pod gets its own file, grants
itself its own lease -- the SQLite adapter always grants, correctly, because a
file admits one writer -- runs its own management loops and holds its own
fleet. They never disagree, because they cannot see each other. Every health
check is green and the deployment has as many fleets as it has pods.

Confirmed on a real cluster before this existed: three replicas, all three
answering `manages_fleet: true`, and the API also claiming
`coordinates_with_peers: true` because a lease keeper existed. A keeper is not
coordination; a *shared* backend is.

So the rule is the one the deployment actually needs: **a hangar cluster
requires PostgreSQL**. Asked on the axis the operator controls -- a
`coordination:` block is the statement that these replicas are meant to be one
gateway -- rather than by sniffing the environment. A thousand pods each with
their own storage are a thousand gateways, which is a legitimate thing to run
and nobody's business but the operator's.
"""

from __future__ import annotations

import pytest

from mcp_hangar.infrastructure.persistence.registry import create_backend, is_shared
from mcp_hangar.server.bootstrap.persistence import (
    ClusterNeedsSharedStorageError,
    refuse_a_cluster_on_unshared_storage,
)


@pytest.fixture
def sqlite_backend(monkeypatch, tmp_path):
    from mcp_hangar.server.bootstrap import composition

    backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
    monkeypatch.setattr(composition, "_persistence_backend", backend)
    yield backend
    backend.close()


#: What makes a deployment a cluster: the operator saying so. A thousand pods
#: each with their own storage are a thousand gateways, which is legitimate and
#: nobody's business but theirs.
AS_A_CLUSTER = {"coordination": {"lease_ttl_s": 15}}


class TestABackendSaysWhetherItCanBeShared:
    def test_sqlite_cannot(self, tmp_path) -> None:
        backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
        try:
            assert is_shared(backend) is False
        finally:
            backend.close()

    def test_postgresql_can(self) -> None:
        from mcp_hangar.infrastructure.persistence.backends.postgresql import PostgresqlBackend

        assert PostgresqlBackend.shared_across_instances is True

    def test_a_backend_that_never_considered_it_counts_as_unshared(self) -> None:
        # The conservative default. A backend whose adapters have not been
        # examined for this is not one to assume the property of.
        assert is_shared(object()) is False


class TestAskingForAClusterOnStorageNobodyShares:
    def test_it_is_refused(self, sqlite_backend) -> None:
        with pytest.raises(ClusterNeedsSharedStorageError) as excinfo:
            refuse_a_cluster_on_unshared_storage(AS_A_CLUSTER)

        message = str(excinfo.value)
        # The refusal has to carry both ways out, or it is an outage with an
        # opinion: use postgresql, or stop calling this a cluster.
        assert "postgresql" in message
        assert "coordination" in message

    def test_a_shared_backend_is_never_refused(self, monkeypatch) -> None:
        from mcp_hangar.server.bootstrap import composition

        class _Shared:
            shared_across_instances = True

        monkeypatch.setattr(composition, "_persistence_backend", _Shared())

        refuse_a_cluster_on_unshared_storage(AS_A_CLUSTER)


class TestNotAskingForOne:
    def test_a_file_backed_backend_is_fine(self, sqlite_backend) -> None:
        # A laptop, a compose file, a `pip install` -- and equally a thousand
        # independent pods. None of them claimed to be one gateway.
        refuse_a_cluster_on_unshared_storage({})

    def test_no_backend_at_all_is_fine(self, monkeypatch) -> None:
        from mcp_hangar.server.bootstrap import composition

        monkeypatch.setattr(composition, "_persistence_backend", None)

        refuse_a_cluster_on_unshared_storage(AS_A_CLUSTER)


class TestAFileBackedBackendGetsNoLeaseKeeper:
    def test_there_is_nothing_to_coordinate_with(self, sqlite_backend) -> None:
        # A keeper on a file-backed backend grants itself the lease every time.
        # That is not coordination, it is theatre -- and it is what made three
        # replicas each report that they managed the fleet.
        from mcp_hangar.server.bootstrap import coordination

        assert coordination.init_lease_keeper({}) is None

    def test_a_shared_backend_does_get_one(self, monkeypatch, tmp_path) -> None:
        from mcp_hangar.server.bootstrap import composition, coordination

        backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
        monkeypatch.setattr(backend.__class__, "shared_across_instances", True, raising=False)
        monkeypatch.setattr(composition, "_persistence_backend", backend)
        try:
            assert coordination.init_lease_keeper({}) is not None
        finally:
            coordination._keeper = None
            backend.close()


class TestTheApiStopsClaimingCoordinationItDoesNotHave:
    def test_a_file_backed_backend_does_not_coordinate(self, sqlite_backend, monkeypatch) -> None:
        # It reported `coordinates_with_peers: true` on all three replicas of a
        # real deployment, because a lease keeper existed. A keeper is not
        # coordination.
        from mcp_hangar.server.api.system import _instance_info
        from mcp_hangar.server.bootstrap import coordination

        monkeypatch.setattr(coordination, "_keeper", _AKeeper())
        info = _instance_info()

        assert info["coordinates_with_peers"] is False
        assert info["storage_is_shareable"] is False

    def test_a_shared_backend_with_a_keeper_does(self, monkeypatch) -> None:
        from mcp_hangar.server.api.system import _instance_info
        from mcp_hangar.server.bootstrap import composition, coordination

        class _Shared:
            shared_across_instances = True

        monkeypatch.setattr(composition, "_persistence_backend", _Shared())
        monkeypatch.setattr(coordination, "_keeper", _AKeeper())

        assert _instance_info()["coordinates_with_peers"] is True

    def test_managing_its_own_island_is_still_reported_truthfully(self, sqlite_backend, monkeypatch) -> None:
        # `manages_fleet: true` on a file-backed gateway is not a lie: it really
        # is the manager of the only fleet it can see. Paired with
        # `coordinates_with_peers: false`, an operator can read what that means.
        from mcp_hangar.server.api.system import _instance_info
        from mcp_hangar.server.bootstrap import coordination

        monkeypatch.setattr(coordination, "_keeper", _AKeeper())

        assert _instance_info()["manages_fleet"] is True


class _AKeeper:
    def may_manage(self) -> bool:
        return True

    @property
    def lease(self):
        return None


@pytest.fixture(autouse=True)
def restore_holders():
    from mcp_hangar.server.bootstrap import composition, coordination

    backend, keeper = composition._persistence_backend, coordination._keeper
    yield
    composition._persistence_backend, coordination._keeper = backend, keeper
