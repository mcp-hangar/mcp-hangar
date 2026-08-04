"""The reload handler goes through `IConfigLoader`, with no way around it.

`IConfigLoader` and `ServerConfigLoader` were introduced, in their own words,
so the reload handler could "load and apply configuration without importing
server-layer symbols from the application layer". Then the handler kept a
`config_loader: IConfigLoader | None = None` parameter with a fallback branch
that imported `server.config` directly, labelled "legacy path".

Bootstrap has always injected the adapter, so that branch never ran in
production -- but it kept the import alive, which is the entire thing the port
was built to remove. Worse, the handler's own 21 tests constructed it *without*
a loader, so the tested path and the shipped path were different ones.

Two things pinned here, since both were assumed rather than checked:

* the loader is required, so a missing wiring fails at construction rather than
  silently taking a different route;
* `ServerConfigLoader` actually satisfies `IConfigLoader`. It used to match by
  shape only -- and `IConfigLoader` is an ABC, so nothing verified the two
  agreed. A rename on either side would have surfaced as an `AttributeError`
  partway through a live config reload.
"""

from __future__ import annotations

import inspect

import pytest

from mcp_hangar.application.commands.reload_handler import ReloadConfigurationHandler
from mcp_hangar.application.ports.config_loader import IConfigLoader
from mcp_hangar.server.config import ServerConfigLoader


class TestTheAdapterSatisfiesThePort:
    def test_it_declares_the_base_class(self):
        assert issubclass(ServerConfigLoader, IConfigLoader)

    def test_it_can_be_instantiated(self):
        """An ABC with an unimplemented method would raise here."""
        assert isinstance(ServerConfigLoader(), IConfigLoader)

    @pytest.mark.parametrize("method", ["load_from_file", "apply_mcp_servers"])
    def test_the_signatures_match(self, method):
        port = inspect.signature(getattr(IConfigLoader, method))
        adapter = inspect.signature(getattr(ServerConfigLoader, method))
        assert list(port.parameters) == list(adapter.parameters)


class TestTheLoaderIsRequired:
    def test_constructing_without_one_fails(self):
        """Previously this silently selected the legacy import path."""
        with pytest.raises(TypeError):
            ReloadConfigurationHandler(object(), object())  # type: ignore[arg-type]

    def test_it_is_keyword_only(self):
        """So it cannot be supplied by position and end up in `current_config_path`."""
        parameter = inspect.signature(ReloadConfigurationHandler.__init__).parameters["config_loader"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty


class TestTheApplicationLayerNoLongerImportsServerConfig:
    def test_the_module_has_no_server_import(self):
        import pathlib

        import mcp_hangar.application.commands.reload_handler as module

        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        offenders = [
            line
            for line in source.splitlines()
            if line.strip().startswith(("from ...server", "from mcp_hangar.server", "import mcp_hangar.server"))
        ]
        assert offenders == [], f"the reload handler reaches into the server layer again: {offenders}"
