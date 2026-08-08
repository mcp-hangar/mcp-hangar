"""The registry lists what the orchestrator runs, not what the config asked for.

Giving a configured source an id fixed "visible but not scannable" by parsing
`config.yaml` a second time, in `_register_configured_sources`, rather than by
asking the orchestrator what it had built. The two readings disagree, and each
disagreement is the original defect wearing a different hat.

**A misspelt mode.** The builder resolves anything that is not `authoritative`
to additive, so `mode: additivee` produces a working source that appears in
`GET /api/discovery/sources` with an id. The second reading called
`DiscoveryMode("additivee")`, caught the `ValueError` and skipped -- so
`POST /api/discovery/sources/<id>/scan` answered 404 for the id the listing had
just handed out.

**A source that did not build.** A missing optional dependency, or any other
failure while constructing a source, degrades to a log line: the orchestrator
has no source and the listing shows none. The second reading required only a
`type` and a parsable `mode` and registered it regardless, so a scan on the
derived id answered `200 {"scan_triggered": true}` for a source that does not
exist.

Both directions are closed by having one list. The mode a spec carries is the
mode the built source is actually running in, which is the answer to the
question `GET /sources` is asking.
"""

from __future__ import annotations

import pytest

from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode
from mcp_hangar.domain.value_objects.discovery import config_source_id
from mcp_hangar.infrastructure.discovery.registry import create_source
from mcp_hangar.server.bootstrap import _register_configured_sources


class _Orchestrator:
    """Holds what was built, the way the real one does."""

    def __init__(self, *sources) -> None:
        self._sources = list(sources)

    def get_sources(self) -> list:
        return list(self._sources)


class _Registry:
    def __init__(self, *sources) -> None:
        self.orchestrator = _Orchestrator(*sources)
        self.registered: list = []

    def register_source(self, spec) -> None:
        self.registered.append(spec)


def _build(source_config: dict):
    """Build a source exactly as `create_discovery_orchestrator` does."""
    return create_source(source_config["type"], source_config)


class TestAMisspeltModeIsStillScannable:
    def test_the_builder_accepts_it(self) -> None:
        # Stated as a fact this test depends on, not as an endorsement: an
        # unknown mode resolves to additive rather than being refused. The
        # divergence below exists because the other reading disagreed.
        source = _build({"type": "docker", "mode": "additivee"})

        assert source.mode is DiscoveryMode.ADDITIVE

    def test_the_source_the_listing_advertises_can_be_scanned(self) -> None:
        source_config = {"type": "docker", "mode": "additivee"}
        registry = _Registry(_build(source_config))

        _register_configured_sources(registry, {"discovery": {"sources": [source_config]}})

        # `GET /sources` reads the orchestrator and derives this id. It must
        # resolve in the registry, which is what `POST .../scan` looks in.
        assert [s.source_id for s in registry.registered] == [config_source_id("docker")]

    def test_the_spec_carries_the_mode_the_source_is_running_in(self) -> None:
        source_config = {"type": "docker", "mode": "additivee"}
        registry = _Registry(_build(source_config))

        _register_configured_sources(registry, {"discovery": {"sources": [source_config]}})

        assert registry.registered[0].mode is DiscoveryMode.ADDITIVE

    @pytest.mark.parametrize("mode", ["additive", "authoritative", "additivee", "AUTHORITATIVE", "", None])
    def test_whatever_was_written_the_two_never_disagree(self, mode) -> None:
        source_config = {"type": "docker"} | ({} if mode is None else {"mode": mode})
        source = _build(source_config)
        registry = _Registry(source)

        _register_configured_sources(registry, {"discovery": {"sources": [source_config]}})

        assert [s.mode for s in registry.registered] == [source.mode]


class TestASourceThatDidNotBuildIsNotAddressable:
    def test_a_declared_source_the_orchestrator_never_got_is_not_registered(self) -> None:
        # An ImportError for an optional dependency, or any other failure, is
        # caught where the source is built and logged. The orchestrator is
        # empty, and so is the registry.
        registry = _Registry()

        _register_configured_sources(
            registry,
            {"discovery": {"sources": [{"type": "kubernetes", "mode": "additive", "namespaces": ["default"]}]}},
        )

        assert registry.registered == []

    def test_the_one_that_did_build_is_unaffected_by_the_one_that_did_not(self) -> None:
        docker = {"type": "docker", "mode": "additive"}
        registry = _Registry(_build(docker))

        _register_configured_sources(
            registry,
            {"discovery": {"sources": [docker, {"type": "kubernetes", "mode": "additive"}]}},
        )

        assert [s.source_type for s in registry.registered] == ["docker"]


class TestTheRegistryDoesNotReparseTheConfig:
    def test_the_membership_is_read_from_the_orchestrator(self) -> None:
        import inspect

        # The docstring explains the old reading, so it is not the code.
        body = inspect.getsource(_register_configured_sources).split('"""')[2]

        assert "registry.orchestrator.get_sources()" in body
        # A second `DiscoveryMode(...)` here is a second opinion about what the
        # source is, and the source is the one holding the answer.
        assert "DiscoveryMode(" not in body
