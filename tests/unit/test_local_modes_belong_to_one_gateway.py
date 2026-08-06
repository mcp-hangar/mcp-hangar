"""A server that is a child process belongs to the instance running it.

`subprocess` and `docker` do not describe a server the gateway talks to. They
describe one it *runs*: `docker run --rm -i` with stdin and stdout attached, held
as a pipe inside one process. There is no address a peer could use.

So a replica that learns about such a server and serves a call to it does not
reach the existing copy -- it starts **its own**, with its own child process and
its own mounted volumes. Two writers to a store built for one, and a fleet whose
answer depends on which replica the request reached.

That became reachable rather than theoretical once a follower could learn about
servers it did not register: from the shared record at startup (#800) and from
the tail immediately (#804). It is refused in two places, on purpose:

- at **registration**, where the mistake is made and an operator can still act
  on it;
- at **launch**, because a server can arrive from `config.yaml` or a snapshot
  written before the rule, and by then the refusal has to be the one that holds.
"""

from __future__ import annotations

import pytest

from mcp_hangar.application.commands.crud_commands import CreateMcpServerCommand
from mcp_hangar.application.commands.crud_handlers import CreateMcpServerHandler
from mcp_hangar.domain.exceptions import ValidationError
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.infrastructure.launchers import (
    LOCAL_MODES,
    LocalModeNotOwnedError,
    get_launcher,
    set_local_mode_policy,
)


class _SilentBus:
    def publish(self, event: object) -> None:
        pass

    def publish_aggregate_events(self, *args: object, **kwargs: object) -> int:
        return 0


def _create(mode: str, **extra) -> CreateMcpServerCommand:
    fields = {"mcp_server_id": "srv", "mode": mode, "command": ["python"], "source": "api"}
    fields.update(extra)
    return CreateMcpServerCommand(**fields)


def _handler(coordinated: bool) -> CreateMcpServerHandler:
    return CreateMcpServerHandler(
        repository=InMemoryMcpServerRepository(),
        event_bus=_SilentBus(),
        coordinated=lambda: coordinated,
    )


@pytest.fixture(autouse=True)
def restore_the_launch_policy():
    """The policy is process-wide; put it back after each test."""
    from mcp_hangar.infrastructure.launchers import factory

    before = factory._may_launch_local
    yield
    factory._may_launch_local = before


class TestRegisteringALocalModeWithPeers:
    @pytest.mark.parametrize("mode", ["subprocess", "docker", "container"])
    def test_it_is_refused(self, mode) -> None:
        with pytest.raises(ValidationError) as excinfo:
            _handler(coordinated=True).handle(_create(mode))

        # The message has to say what to do instead, because "refused" alone
        # leaves an operator with a working configuration and no next step.
        assert "remote" in str(excinfo.value)

    def test_remote_is_not_refused(self) -> None:
        # Asserted on the mode check itself rather than through a full
        # registration: a remote endpoint also passes the SSRF guard, and a
        # test that tripped over *that* would be reporting on the wrong rule.
        _handler(coordinated=True)._refuse_local_mode_when_coordinating("remote")

    def test_a_standalone_gateway_registers_them_as_before(self) -> None:
        # Every deployment that has not selected a storage backend. Refusing
        # here would break the single-instance case this project started from.
        result = _handler(coordinated=False).handle(_create("subprocess"))

        assert result["created"] is True

    def test_nothing_is_written_down_for_a_refused_registration(self) -> None:
        recorded: list[object] = []

        class _Writer:
            def save(self, snapshot: object) -> None:
                recorded.append(snapshot)

            def delete(self, mcp_server_id: str, *, fenced: bool = False) -> None:
                pass

        handler = CreateMcpServerHandler(
            repository=InMemoryMcpServerRepository(),
            event_bus=_SilentBus(),
            fleet_writer=_Writer(),
            coordinated=lambda: True,
        )

        with pytest.raises(ValidationError):
            handler.handle(_create("subprocess"))

        assert recorded == []


class TestLaunchingALocalModeAsAFollower:
    @pytest.mark.parametrize("mode", sorted(LOCAL_MODES))
    def test_it_is_refused(self, mode) -> None:
        # The second refusal, and the one that has to hold: a server can arrive
        # from `config.yaml` or from a snapshot written before the rule, and by
        # then registration is long past.
        set_local_mode_policy(lambda: False)

        with pytest.raises(LocalModeNotOwnedError):
            get_launcher(mode)

    def test_remote_is_launched_by_anyone(self, mode="remote") -> None:
        # A follower must keep serving remote-mode servers -- that is the whole
        # supported configuration.
        set_local_mode_policy(lambda: False)

        assert get_launcher("remote") is not None

    def test_the_holder_launches_them(self) -> None:
        set_local_mode_policy(lambda: True)

        assert get_launcher("subprocess") is not None

    def test_a_standalone_gateway_launches_them(self) -> None:
        # No policy set means nothing is coordinating.
        set_local_mode_policy(None)

        assert get_launcher("subprocess") is not None

    def test_the_policy_is_asked_per_launch(self) -> None:
        # Not once at startup: a lease lost mid-life has to stop the *next*
        # start, not the next process.
        answers = iter([True, False])
        set_local_mode_policy(lambda: next(answers))

        assert get_launcher("subprocess") is not None
        with pytest.raises(LocalModeNotOwnedError):
            get_launcher("subprocess")

    def test_the_refusal_says_why_and_what_to_do(self) -> None:
        set_local_mode_policy(lambda: False)

        with pytest.raises(LocalModeNotOwnedError) as excinfo:
            get_launcher("docker")

        message = str(excinfo.value)
        assert "management lease" in message
        assert "remote" in message


class TestTheTwoRefusalsAreWiredToTheLease:
    def test_a_shareable_backend_makes_local_modes_leader_owned(self, monkeypatch, tmp_path) -> None:
        from mcp_hangar.infrastructure.persistence.registry import create_backend
        from mcp_hangar.server.bootstrap import composition, coordination
        from mcp_hangar.infrastructure.launchers import factory

        backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
        # Shareable is the property that matters, not the backend's name: it is
        # what says a peer could be running the same server.
        monkeypatch.setattr(backend.__class__, "shared_across_instances", True, raising=False)
        monkeypatch.setattr(composition, "_persistence_backend", backend)
        try:
            coordination.init_lease_keeper({})

            assert factory._may_launch_local is not None
        finally:
            coordination._keeper = None
            set_local_mode_policy(None)
            backend.close()

    def test_a_file_backed_backend_keeps_every_mode(self, monkeypatch, tmp_path) -> None:
        # Selecting storage is not the question -- sharing it is. A gateway on
        # its own file has no follower to be, so refusing its `subprocess`
        # servers would take a working single-node deployment away for nothing.
        from mcp_hangar.infrastructure.persistence.registry import create_backend
        from mcp_hangar.server.bootstrap import composition, coordination
        from mcp_hangar.infrastructure.launchers import factory

        backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
        monkeypatch.setattr(composition, "_persistence_backend", backend)
        set_local_mode_policy(lambda: False)  # left behind by a previous bootstrap
        try:
            coordination.init_lease_keeper({})

            assert factory._may_launch_local is None
            assert get_launcher("subprocess") is not None
        finally:
            coordination._keeper = None
            set_local_mode_policy(None)
            backend.close()

    def test_no_backend_leaves_every_mode_available(self, monkeypatch) -> None:
        from mcp_hangar.server.bootstrap import composition, coordination
        from mcp_hangar.infrastructure.launchers import factory

        monkeypatch.setattr(composition, "_persistence_backend", None)
        set_local_mode_policy(lambda: False)  # a policy left behind by a previous bootstrap

        coordination.init_lease_keeper({})

        assert factory._may_launch_local is None
        assert get_launcher("subprocess") is not None

    def test_registration_asks_whether_there_are_peers(self) -> None:
        import inspect

        from mcp_hangar.server.bootstrap import cqrs

        assert "coordinated=_coordinated" in inspect.getsource(cqrs.init_cqrs)
        assert "get_lease_keeper() is not None" in inspect.getsource(cqrs._coordinated)
