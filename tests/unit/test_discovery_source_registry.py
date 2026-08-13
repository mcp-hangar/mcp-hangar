"""A new discovery source costs one file, not a patch to the core.

Adding a source used to mean editing `server/bootstrap/discovery.py` -- a
branch in an `if/elif` over every known `source_type`, with the delivery layer
unpacking that source's option names. So "add Consul" meant changing three core
files for one adapter, and the adapter was the only part that should have been
work.

The port was never the problem: `DiscoverySource` is three methods and it is
fine. This pins the composition half -- that a source nobody in this repo has
heard of can be registered and built, and that core learns none of its option
names on the way.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_hangar.domain.discovery.discovery_source import DiscoverySource
from mcp_hangar.domain.value_objects.discovery import DiscoveryMode
from mcp_hangar.infrastructure.discovery import registry
from mcp_hangar.infrastructure.discovery.registry import (
    UnknownDiscoverySourceError,
    available_source_types,
    create_source,
    register_source_factory,
)


class _PretendConsulSource(DiscoverySource):
    """Stands in for a third-party adapter. Core knows nothing about it."""

    def __init__(self, mode: DiscoveryMode, *, datacenter: str, token: str | None = None) -> None:
        super().__init__(mode=mode)
        self.datacenter = datacenter
        self.token = token

    @property
    def source_type(self) -> str:
        return "pretend-consul"

    async def discover(self) -> list:
        return []

    async def health_check(self) -> bool:
        return True


def _factory(mode: DiscoveryMode, config: dict[str, Any]) -> DiscoverySource:
    return _PretendConsulSource(mode, datacenter=config["datacenter"], token=config.get("token"))


@pytest.fixture
def clean_registry():
    """Registration is process-global; put it back afterwards."""
    saved = dict(registry._FACTORIES)
    yield
    registry._FACTORIES.clear()
    registry._FACTORIES.update(saved)


class TestAThirdPartySourceNeedsNoCoreChange:
    def test_it_registers_and_builds(self, clean_registry) -> None:
        register_source_factory("pretend-consul", _factory)

        source = create_source("pretend-consul", {"datacenter": "dc1", "token": "t"})

        assert isinstance(source, _PretendConsulSource)
        assert source.source_type == "pretend-consul"

    def test_core_never_reads_its_option_names(self, clean_registry) -> None:
        # `datacenter` and `token` appear nowhere outside the factory. The
        # registry passes the dict through untouched, which is what makes the
        # adapter the only file a new source needs.
        register_source_factory("pretend-consul", _factory)

        source = create_source("pretend-consul", {"datacenter": "dc2", "token": "secret", "unknown_key": 1})

        assert source.datacenter == "dc2"
        assert source.token == "secret"

    def test_mode_is_still_the_core_s_business(self, clean_registry) -> None:
        # The one key core does read, because every source has a mode and the
        # orchestrator's behaviour depends on it.
        register_source_factory("pretend-consul", _factory)

        source = create_source("pretend-consul", {"datacenter": "dc", "mode": "authoritative"})

        assert source.mode is DiscoveryMode.AUTHORITATIVE


class TestABuiltInIsJustAnEarlyRegistration:
    def test_the_four_built_ins_are_registered(self) -> None:
        assert {"docker", "filesystem", "kubernetes", "entrypoint"} <= set(available_source_types())

    def test_a_plugin_cannot_quietly_shadow_one(self, clean_registry) -> None:
        # Taking over `kubernetes` should be a decision, not an import side
        # effect in somebody's package.
        with pytest.raises(ValueError, match="already registered"):
            register_source_factory("kubernetes", _factory)

        register_source_factory("kubernetes", _factory, replace=True)  # deliberate, allowed


class TestAConfiguredSourceThatCannotExistIsLoud:
    def test_an_unknown_type_raises(self) -> None:
        with pytest.raises(UnknownDiscoverySourceError) as excinfo:
            create_source("consul", {})

        # The message has to be actionable: an operator who typed the name
        # wrong, and a plugin author who forgot the entry point, both read this.
        message = str(excinfo.value)
        assert "consul" in message
        assert "kubernetes" in message, "the known types belong in the error"
        assert registry.ENTRY_POINT_GROUP in message, "so does how to add one"

    def test_it_is_not_swallowed_as_a_missing_dependency(self) -> None:
        # ImportError means an optional package is absent -- a deployment shape,
        # and bootstrap degrades on it. An unknown type is a configuration
        # mistake and must not ride the same path.
        assert not issubclass(UnknownDiscoverySourceError, ImportError)


class TestDiscoveryModeParsing:
    def test_an_unknown_mode_is_rejected_instead_of_becoming_additive(self, clean_registry):
        register_source_factory("pretend-consul", _factory)

        with pytest.raises(ValueError, match=r"unknown discovery mode 'authoritativee'") as excinfo:
            create_source("pretend-consul", {"datacenter": "dc", "mode": "authoritativee"})

        assert "additive" in str(excinfo.value)
        assert "authoritative" in str(excinfo.value)

    def test_mode_values_are_case_sensitive(self, clean_registry):
        register_source_factory("pretend-consul", _factory)

        with pytest.raises(ValueError, match=r"unknown discovery mode 'Authoritative'"):
            create_source("pretend-consul", {"datacenter": "dc", "mode": "Authoritative"})
