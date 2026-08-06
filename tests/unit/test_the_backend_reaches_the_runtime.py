"""A server in config.yaml must not cost the gateway its storage backend.

The runtime is a singleton and a frozen dataclass: it takes the storage backend
**at construction**, and cannot be given one afterwards. Building a server
declared in `config.yaml` reaches for that singleton -- so reading the
configuration used to construct the runtime before the backend had been
selected, and the backend never arrived.

The result was a gateway that had selected PostgreSQL, said so in its logs, and
then used the in-memory config repository for the rest of its life: no durable
fleet, no fleet projection, nothing for recovery to read. One log line mentioned
it -- `fleet_writer_absent` -- and it reads like a configuration choice rather
than a defect.

**It only happened when `config.yaml` declared at least one server**, which is
the ordinary case. An empty `mcp_servers:` block was what made every earlier
test of this pass.

Found by deploying two gateways across two Kubernetes clusters with a shared
database, where the follower never learned about anything the leader registered.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def fresh_runtime(monkeypatch):
    """The singleton is process-wide; start from nothing and put it back."""
    from mcp_hangar.server.bootstrap import composition

    before = composition._runtime
    composition._runtime = None
    yield composition
    composition._runtime = before


class TestReadingTheConfigurationDoesNotBuildTheRuntime:
    def test_loading_a_file_with_servers_leaves_the_runtime_unbuilt(self, fresh_runtime, tmp_path) -> None:
        # The whole defect in one assertion: if reading the file builds the
        # runtime, the backend selected two lines later can never reach it.
        from mcp_hangar.server.config import load_configuration

        config = tmp_path / "config.yaml"
        config.write_text(
            "mcp_servers:\n  math:\n    mode: remote\n    endpoint: http://example.invalid/mcp\n",
            encoding="utf-8",
        )

        load_configuration(str(config), load_servers=False)

        assert fresh_runtime._runtime is None

    def test_loading_servers_explicitly_still_works(self, fresh_runtime) -> None:
        # The two steps are separate, not gone: bootstrap does the second one
        # after the backend is in place.
        from mcp_hangar.server.config import load_config

        load_config({"math": {"mode": "remote", "endpoint": "http://example.invalid/mcp"}})

        assert fresh_runtime._runtime is not None

    def test_the_default_configuration_does_not_build_it_either(self, fresh_runtime, tmp_path) -> None:
        # The no-config-file path declares a subprocess server of its own, and
        # would have had exactly the same effect.
        from mcp_hangar.server.config import load_configuration

        load_configuration(str(tmp_path / "does-not-exist.yaml"), load_servers=False)

        assert fresh_runtime._runtime is None


class TestBootstrapSelectsTheBackendFirst:
    def test_the_backend_is_selected_before_the_servers_are_built(self) -> None:
        import inspect
        import sys

        import mcp_hangar.server.bootstrap  # noqa: F401 -- for its side effect on sys.modules

        source = inspect.getsource(sys.modules["mcp_hangar.server.bootstrap"].bootstrap)

        assert source.index("select_backend(full_config)") < source.index('load_config(full_config.get("mcp_servers"')

    def test_the_runtime_is_built_before_the_servers_are(self) -> None:
        # And after the backend, which is the pair of orderings that matters.
        import inspect
        import sys

        import mcp_hangar.server.bootstrap  # noqa: F401

        source = inspect.getsource(sys.modules["mcp_hangar.server.bootstrap"].bootstrap)

        assert source.index("get_runtime(rate_limit=") < source.index('load_config(full_config.get("mcp_servers"')

    def test_reading_the_configuration_asks_for_no_servers(self) -> None:
        import inspect
        import sys

        import mcp_hangar.server.bootstrap  # noqa: F401

        source = inspect.getsource(sys.modules["mcp_hangar.server.bootstrap"].bootstrap)

        assert source.count("load_servers=False") == 2


class TestTheEffectThatWasLost:
    def test_a_runtime_built_with_a_backend_uses_it(self, fresh_runtime, tmp_path) -> None:
        # What the ordering bug cost, stated directly: with the backend in
        # hand the runtime persists through it; without, it does not.
        from mcp_hangar.infrastructure.persistence.registry import create_backend
        from mcp_hangar.server.bootstrap.composition import get_runtime

        backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
        try:
            runtime = get_runtime(persistence_backend=backend)

            assert type(runtime.config_repository).__name__ != "InMemoryMcpServerConfigRepository"
        finally:
            backend.close()

    def test_a_runtime_built_without_one_falls_back_to_memory(self, fresh_runtime) -> None:
        from mcp_hangar.server.bootstrap.composition import get_runtime

        runtime = get_runtime()

        assert type(runtime.config_repository).__name__ == "InMemoryMcpServerConfigRepository"

    def test_the_backend_cannot_be_supplied_afterwards(self, fresh_runtime, tmp_path) -> None:
        # Which is why the ordering is the fix rather than a late assignment:
        # the runtime is frozen, and the singleton ignores arguments after the
        # first call.
        from mcp_hangar.infrastructure.persistence.registry import create_backend
        from mcp_hangar.server.bootstrap.composition import get_runtime

        first = get_runtime()
        backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
        try:
            second = get_runtime(persistence_backend=backend)

            assert second is first
            assert type(second.config_repository).__name__ == "InMemoryMcpServerConfigRepository"
        finally:
            backend.close()
