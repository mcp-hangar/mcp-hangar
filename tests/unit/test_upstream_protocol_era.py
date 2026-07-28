"""Hangar must not stamp the modern `_meta` envelope on a legacy upstream.

From `mcp==2.0.0` the SDK enforces era separation. A connection that negotiated
a legacy version at `initialize` rejects **every** later request carrying the
2026-07-28 envelope:

    -32600  "this connection serves the handshake protocol era; requests
             carrying the 2026-07-28 envelope are not accepted on it"

Hangar stamped that envelope unconditionally, so against any SDK-built legacy
upstream `tools/list` failed, the cold start never completed, and the caller saw
a **hang** rather than an error -- the batch sat until its global timeout. The
beta tolerated it; the stable release does not.

The published-artifact smoke (gate D) caught this before the wheel shipped. No
unit test could have: the defect only exists between two processes speaking a
real protocol, and every test double answers whatever it is asked.

What these tests can do is pin the two halves that were got wrong:

* the envelope is withheld on a legacy connection -- the bug itself;
* `_meta` still EXISTS when it is withheld -- the bug in the first fix, which
  turned a clean refusal into a `KeyError` because `_meta` is also the
  trace-context carrier and the caller writes into it immediately after.
"""

from __future__ import annotations

from mcp_hangar.protocol import (
    _META_CLIENT_INFO_KEY,
    _META_PROTOCOL_VERSION_KEY,
    inject_protocol_meta,
)


class TestTheModernEnvelopeIsConditional:
    def test_a_modern_connection_gets_the_protocol_keys(self):
        meta = inject_protocol_meta({"name": "t"})["_meta"]

        assert _META_PROTOCOL_VERSION_KEY in meta
        assert _META_CLIENT_INFO_KEY in meta

    def test_a_legacy_connection_gets_none_of_them(self):
        """The whole point: these keys are what the era gate rejects."""
        meta = inject_protocol_meta({"name": "t"}, modern_envelope=False)["_meta"]

        assert _META_PROTOCOL_VERSION_KEY not in meta
        assert _META_CLIENT_INFO_KEY not in meta

    def test_meta_still_exists_when_the_envelope_is_withheld(self):
        """Regression on the first attempt at this fix.

        Returning params without `_meta` looked tidy and broke every legacy
        upstream differently: the caller injects trace context into
        `params["_meta"]` on the next line, so it raised `KeyError` instead. The
        era gate is about the protocol keys, not about `_meta` as such.
        """
        params = inject_protocol_meta({"name": "t"}, modern_envelope=False)

        assert "_meta" in params
        assert params["_meta"] == {}

    def test_existing_meta_survives_either_way(self):
        """Trace context set by a caller must not be dropped by the era choice."""
        for modern in (True, False):
            params = inject_protocol_meta({"_meta": {"traceparent": "00-abc"}}, modern_envelope=modern)

            assert params["_meta"]["traceparent"] == "00-abc", modern

    def test_the_caller_params_are_never_mutated(self):
        original = {"name": "t"}

        inject_protocol_meta(original, modern_envelope=False)

        assert "_meta" not in original


class TestClientsDefaultToTheModernEnvelope:
    """Default True so the handshake itself, and stateless upstreams, carry it.

    A SEP-2575 upstream has no `initialize` at all -- the envelope is the only
    way it learns the protocol version and client info, so defaulting to False
    would break exactly the case the envelope exists for.
    """

    def test_stdio_client_starts_modern(self):
        from unittest.mock import MagicMock

        from mcp_hangar.stdio_client import StdioClient

        client = StdioClient(MagicMock(), "probe")

        assert client.modern_envelope is True

    def test_http_client_starts_modern(self):
        from mcp_hangar.http_client import HttpClient

        client = HttpClient("http://127.0.0.1:1/mcp", mcp_server_id="probe")

        assert client.modern_envelope is True
