"""A `coordination:` block plus a `subprocess` server is a configuration error.

Registering such a server through the API is refused where storage is
shareable, and launching one on a follower is refused again. A server declared
in `config.yaml` goes through neither path: it is loaded on every replica and
only the lease holder can start it.

What that produces is not an error message. It is
`GET /api/mcp_servers/<id>/tools` answering with five tools on one pod and an
empty list on the others, and a 409 from whichever replica the load balancer
picked -- measured on two replicas sharing one database.

Asked on the axis the operator controls. The `coordination:` block is the
statement that these replicas are meant to be one gateway; without it, a single
gateway that merely uses PostgreSQL keeps running its child processes exactly
as before.
"""

from __future__ import annotations

import pytest

from mcp_hangar.server.bootstrap.persistence import (
    LocalModeInDeclaredClusterError,
    refuse_local_modes_in_a_declared_cluster,
)

CLUSTER = {"lease_ttl_s": 15}


def _config(servers: dict, *, cluster: bool = True) -> dict:
    config: dict = {"mcp_servers": servers}
    if cluster:
        config["coordination"] = CLUSTER
    return config


class TestADeclaredClusterRefusesThem:
    @pytest.mark.parametrize("mode", ["subprocess", "docker", "container"])
    def test_every_child_process_mode(self, mode: str) -> None:
        with pytest.raises(LocalModeInDeclaredClusterError) as excinfo:
            refuse_local_modes_in_a_declared_cluster(_config({"tools": {"mode": mode}}))

        assert excinfo.value.offenders == [("tools", mode)]

    def test_the_message_names_the_server_and_the_mode_that_works(self) -> None:
        with pytest.raises(LocalModeInDeclaredClusterError) as excinfo:
            refuse_local_modes_in_a_declared_cluster(_config({"reports": {"mode": "subprocess"}}))

        message = str(excinfo.value)
        assert "'reports'" in message
        assert "remote" in message
        assert "coordination:" in message

    def test_every_offender_at_once(self) -> None:
        # Fixing these one restart at a time is the experience this refuses to ship.
        config = _config(
            {
                "a": {"mode": "subprocess"},
                "b": {"mode": "remote", "endpoint": "http://x/mcp"},
                "c": {"mode": "docker"},
            }
        )

        with pytest.raises(LocalModeInDeclaredClusterError) as excinfo:
            refuse_local_modes_in_a_declared_cluster(config)

        assert {server for server, _ in excinfo.value.offenders} == {"a", "c"}

    def test_the_mode_is_matched_however_it_is_spelled(self) -> None:
        with pytest.raises(LocalModeInDeclaredClusterError):
            refuse_local_modes_in_a_declared_cluster(_config({"x": {"mode": " Subprocess "}}))


class TestWithoutTheDeclarationNothingChanges:
    def test_a_single_gateway_on_postgresql_keeps_its_child_processes(self) -> None:
        # The case the axis exists for. No `coordination:` block, so this is one
        # gateway that happens to use a shareable backend -- refusing here would
        # take away a working deployment for a peer that does not exist.
        refuse_local_modes_in_a_declared_cluster(_config({"tools": {"mode": "subprocess"}}, cluster=False))

    def test_a_cluster_of_remote_servers_is_the_supported_shape(self) -> None:
        refuse_local_modes_in_a_declared_cluster(_config({"tools": {"mode": "remote", "endpoint": "http://x/mcp"}}))

    def test_no_servers_at_all(self) -> None:
        refuse_local_modes_in_a_declared_cluster({"coordination": CLUSTER})

    def test_no_configuration_at_all(self) -> None:
        refuse_local_modes_in_a_declared_cluster(None)

    def test_a_malformed_servers_block_is_not_this_check_s_business(self) -> None:
        # Someone else refuses this properly; guessing here would turn a clear
        # schema error into a confusing one about clusters.
        refuse_local_modes_in_a_declared_cluster({"coordination": CLUSTER, "mcp_servers": ["not", "a", "map"]})

    def test_a_server_with_no_mode_is_left_to_the_schema(self) -> None:
        refuse_local_modes_in_a_declared_cluster(_config({"x": {"endpoint": "http://x/mcp"}}))


class TestItRunsBeforeTheBackendIsBuilt:
    def test_bootstrap_asks_before_selecting_storage(self) -> None:
        # A cluster declaring a child-process server is wrong whether or not its
        # database is reachable, and it should not be told about the database
        # first: the storage error would send the operator to the wrong problem.
        import inspect

        from mcp_hangar.server import bootstrap

        source = inspect.getsource(bootstrap)

        assert source.index("refuse_local_modes_in_a_declared_cluster(full_config)") < source.index(
            "_backend = select_backend(full_config)"
        )


class TestTheRegistrationRefusalStopsMisdescribingItself:
    def test_it_no_longer_tells_a_single_instance_to_be_one(self) -> None:
        # The condition is shareable storage, not observed peers, so a single
        # gateway on PostgreSQL met a message ending "or run a single instance"
        # -- which it already was.
        import inspect

        from mcp_hangar.application.commands import crud_handlers

        source = inspect.getsource(crud_handlers.CreateMcpServerHandler._refuse_local_mode_when_coordinating)

        assert "run a single instance" not in source
        assert "storage that peers can share" in source
        assert "persistence.backend: sqlite" in source
