"""The launcher port must describe a transport, not name two of them.

`domain/contracts/launcher.py` declared

    LaunchResult = StdioClient | HttpClient

which is a port importing its own adapters -- the two
`domain.contracts.launcher -> http_client` / `-> stdio_client` lines in the
import-contract debt ledger. A third transport could not be added without
editing the domain, and the domain could not be reasoned about without reading
two infrastructure modules.

It also did no work. The aggregate holds the launched client as
`self._client: Any | None`, with "StdioClient or HttpClient" in a comment beside
it, so nothing was type-checked at the one place it mattered.

What the domain actually needs is three methods -- `is_alive`, `close`, `call`
-- so that is what the port now says, as a Protocol both transports satisfy
structurally. These tests pin that the protocol stays true to its
implementations: a Protocol nobody conforms to is worse than the union it
replaced, because it fails at runtime instead of at the import.
"""

from __future__ import annotations

import inspect

from mcp_hangar.domain.contracts.launcher import LaunchResult, TransportClient
from mcp_hangar.http_client import HttpClient
from mcp_hangar.stdio_client import StdioClient

_TRANSPORTS = (StdioClient, HttpClient)


class TestBothTransportsSatisfyThePort:
    def test_the_protocol_names_exactly_what_the_domain_calls(self):
        """If this grows, the domain started depending on more of the transport."""
        declared = {name for name in vars(TransportClient) if not name.startswith("_")}
        assert declared == {"is_alive", "close", "call"}

    def test_stdio_client_conforms(self):
        assert isinstance(StdioClient.__new__(StdioClient), TransportClient)

    def test_http_client_conforms(self):
        assert isinstance(HttpClient.__new__(HttpClient), TransportClient)

    def test_launch_result_is_the_protocol_not_a_union_of_adapters(self):
        """A union of concrete classes is the thing this replaced."""
        assert LaunchResult is TransportClient


class TestTheSignaturesActuallyLineUp:
    """`runtime_checkable` only checks that a name exists, not that it fits.

    So the isinstance assertions above would pass against a transport whose
    `call` took entirely different arguments, and the failure would surface as a
    TypeError on a live call path. These compare the parameters.
    """

    def test_call_accepts_what_the_domain_passes(self):
        """Both call sites in the aggregate use `call(method, params, timeout=float)`."""
        for transport in _TRANSPORTS:
            params = inspect.signature(transport.call).parameters
            assert list(params)[:3] == ["self", "method", "params"], transport.__name__
            assert "timeout" in params, transport.__name__

    def test_is_alive_and_close_take_no_arguments(self):
        for transport in _TRANSPORTS:
            for name in ("is_alive", "close"):
                params = inspect.signature(getattr(transport, name)).parameters
                assert list(params) == ["self"], f"{transport.__name__}.{name}"

    def test_a_timeout_the_domain_passes_is_accepted_by_both(self):
        """The domain passes a plain float; a transport requiring None would break."""
        for transport in _TRANSPORTS:
            timeout = inspect.signature(transport.call).parameters["timeout"]
            assert timeout.default is not inspect.Parameter.empty, (
                f"{transport.__name__}.call requires an explicit timeout; the protocol makes it optional"
            )


class TestTheDomainDoesNotImportTransports:
    """The point of the exercise, asserted directly rather than only by lint-imports."""

    def test_the_launcher_contract_imports_no_adapter(self):
        import pathlib

        import mcp_hangar.domain.contracts.launcher as module

        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        for adapter in ("http_client", "stdio_client"):
            assert f"import {adapter}" not in source and f"from mcp_hangar.{adapter}" not in source, (
                f"the launcher port imports {adapter} again"
            )
