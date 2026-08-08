"""A source the orchestrator holds can be named, and therefore scanned.

There were two registries and a source only ever reached one of them. A source
declared in configuration went to the orchestrator, which runs it; the
UUID-keyed `DiscoveryRegistry` was created empty and only the REST API ever
wrote to it.

So `POST /api/discovery/sources/<id>/scan` answered 404 for every id an
operator could obtain -- and `GET /api/discovery/sources`, which reads the
orchestrator, returned no `id` to try in the first place. Cookbook 10's steps 3
and 4 were unreachable.

Measured against 2.5.0-rc.3 and the fix, same configuration:

    rc3:  GET /sources -> no id
    fix:  GET /sources -> id 28018ad1-...  ->  POST .../scan -> 200

Membership comes from the orchestrator. `test_the_registry_and_the_orchestrator_agree`
holds the reason.
"""

from __future__ import annotations

import pytest

from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode
from mcp_hangar.domain.value_objects.discovery import config_source_id
from mcp_hangar.server.bootstrap import _register_configured_sources


class _Source:
    """A source the orchestrator built, which is all this function reads."""

    def __init__(self, source_type: str, mode: DiscoveryMode = DiscoveryMode.ADDITIVE) -> None:
        self.source_type = source_type
        self.mode = mode


class _Orchestrator:
    def __init__(self, *sources: _Source) -> None:
        self._sources = list(sources)

    def get_sources(self) -> list[_Source]:
        return list(self._sources)


class _Registry:
    def __init__(self, *sources: _Source) -> None:
        self.orchestrator = _Orchestrator(*sources)
        self.registered: list = []

    def register_source(self, spec) -> None:
        self.registered.append(spec)


class TestTheIdIsDerivedNotGenerated:
    def test_the_same_type_gives_the_same_id_every_time(self) -> None:
        # A random id would change on every restart, so a scan a script
        # triggered yesterday would address nothing today.
        assert config_source_id("docker") == config_source_id("docker")

    def test_different_types_get_different_ids(self) -> None:
        assert config_source_id("docker") != config_source_id("kubernetes")

    def test_it_is_a_uuid(self) -> None:
        import uuid

        uuid.UUID(config_source_id("docker"))  # must not raise


class TestBuiltSourcesReachTheRegistry:
    def test_a_running_source_is_registered(self) -> None:
        registry = _Registry(_Source("docker"))

        _register_configured_sources(
            registry,
            {"discovery": {"sources": [{"type": "docker", "mode": "additive", "socket_path": "/var/run/docker.sock"}]}},
        )

        assert len(registry.registered) == 1
        spec = registry.registered[0]
        assert spec.source_id == config_source_id("docker")
        assert spec.source_type == "docker"
        assert spec.mode is DiscoveryMode.ADDITIVE

    def test_the_source_keeps_its_own_configuration(self) -> None:
        registry = _Registry(_Source("docker"))

        _register_configured_sources(
            registry,
            {"discovery": {"sources": [{"type": "docker", "mode": "additive", "socket_path": "/x.sock"}]}},
        )

        # `type` and `mode` are the registry's own fields; everything else is
        # the source's business and travels untouched.
        assert registry.registered[0].config == {"socket_path": "/x.sock"}

    def test_several_sources(self) -> None:
        registry = _Registry(_Source("docker"), _Source("kubernetes", DiscoveryMode.AUTHORITATIVE))

        _register_configured_sources(
            registry,
            {"discovery": {"sources": [{"type": "docker"}, {"type": "kubernetes", "mode": "authoritative"}]}},
        )

        assert [s.source_type for s in registry.registered] == ["docker", "kubernetes"]
        assert registry.registered[1].mode is DiscoveryMode.AUTHORITATIVE


class TestBookkeepingNeverFailsTheBoot:
    @pytest.mark.parametrize(
        "config",
        [
            {},
            {"discovery": None},
            {"discovery": {}},
            {"discovery": {"sources": None}},
            {"discovery": {"sources": []}},
        ],
    )
    def test_nothing_running_registers_nothing(self, config: dict) -> None:
        registry = _Registry()

        _register_configured_sources(registry, config)

        assert registry.registered == []

    def test_a_declared_entry_with_no_type_built_nothing_and_registers_nothing(self) -> None:
        registry = _Registry()

        _register_configured_sources(registry, {"discovery": {"sources": [{"mode": "additive"}]}})

        assert registry.registered == []

    def test_a_running_source_absent_from_the_config_still_gets_an_id(self) -> None:
        # An entry-point source registers itself; it has no config block to
        # read, and it is still something an operator can scan.
        registry = _Registry(_Source("entrypoint"))

        _register_configured_sources(registry, {"discovery": {"sources": []}})

        assert [s.source_type for s in registry.registered] == ["entrypoint"]
        assert registry.registered[0].config == {}
