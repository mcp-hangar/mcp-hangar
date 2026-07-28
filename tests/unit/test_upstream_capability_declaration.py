"""What Hangar declares to an UPSTREAM at handshake, and why it matters for tasks.

The ADR-014 relay is built on the assumption that an upstream mints a task and
Hangar governs it. Whether an upstream ever mints one is not up to Hangar's
serving surface -- it is up to what Hangar declared when it connected.

Today `_perform_mcp_handshake` sends a hardcoded ``"capabilities": {}``. These
tests pin that, and pin the reasoning around it, because two different mistakes
are easy to make here and they point in opposite directions.

**Mistake one: assume a downstream client's extensions propagate.** They cannot.
The upstream connection is established once at cold start, is shared by every
downstream client of that server, and outlives any of them. There is no
per-client upstream handshake to carry a per-client declaration, so "does the
client's declared extension reach the upstream 1:1" is not a question with a
well-defined answer -- it is the wrong model. What Hangar may honestly declare
upstream is the set it can *itself* service on behalf of any caller.

**Mistake two: assume the empty declaration is harmless.** It is not, and this is
the part worth watching. SEP-2663 makes task augmentation the *server's* decision
and gates it on the client having declared the extension -- python-sdk#3005
enforces exactly that with `require_client_extension(ctx, EXTENSION_ID)`, and
answers a non-declaring client with `-32021`. Hangar declares nothing. So against
a #3005-based upstream, Hangar is a non-declaring client: that upstream will
never augment a `tools/call` into a task, and the governed relay will sit idle
having never been offered one.

That is not a bug in the relay, and it is not fixed by realigning the served
wire. It is a separate decision -- what Hangar declares upstream, and on whose
behalf -- and it should be made deliberately rather than discovered when a real
upstream fails to produce tasks.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mcp_hangar.protocol import HANGAR_CLIENT_INFO, SUPPORTED_PROTOCOL_VERSION

_MCP_SERVER_SOURCE = Path(__file__).resolve().parents[2] / "src" / "mcp_hangar" / "domain" / "model" / "mcp_server.py"


def _handshake_params(monkeypatch) -> dict:
    """Drive the real handshake against a fake client and capture its params."""
    from mcp_hangar.domain.model.mcp_server import McpServer

    captured: dict = {}

    def _call(method: str, params: dict, *args, **kwargs):
        if method == "initialize":
            captured.update(params)
            return {"result": {"protocolVersion": SUPPORTED_PROTOCOL_VERSION, "capabilities": {}}}
        return {"result": {"tools": []}}

    client = MagicMock()
    client.call.side_effect = _call

    # Called unbound against a stand-in `self`. `mcp_server_id` is a read-only
    # property on the real class, and building a genuine McpServer would drag in
    # config, a launcher and a repository -- none of which participate in the
    # handshake params, which is the only thing under test here.
    McpServer._perform_mcp_handshake(MagicMock(mcp_server_id="upstream-under-test"), client)

    return captured


class TestUpstreamHandshakeDeclaration:
    def test_hangar_declares_no_capabilities_upstream(self, monkeypatch):
        """Characterization, deliberately.

        Changing this to a non-empty set is a real protocol decision -- it tells
        upstreams what Hangar will service on a caller's behalf -- so it should
        break a test and be argued for, not slip in.
        """
        params = _handshake_params(monkeypatch)

        assert params["capabilities"] == {}

    def test_the_handshake_identifies_hangar_and_its_protocol(self, monkeypatch):
        params = _handshake_params(monkeypatch)

        assert params["clientInfo"] == dict(HANGAR_CLIENT_INFO)
        assert params["protocolVersion"] == SUPPORTED_PROTOCOL_VERSION

    def test_the_tasks_extension_is_not_declared_upstream(self, monkeypatch):
        """The reason the relay can sit idle against a spec-compliant upstream.

        SEP-2663 gates augmentation on the client declaring
        `io.modelcontextprotocol/tasks`; python-sdk#3005 enforces it and answers
        a non-declaring client with `-32021`. Hangar declares nothing, so such an
        upstream will never mint a task for it.

        When that changes, this test should be *inverted* rather than deleted --
        the declaration is exactly the thing worth pinning.
        """
        params = _handshake_params(monkeypatch)

        assert "extensions" not in params["capabilities"]
        assert "io.modelcontextprotocol/tasks" not in str(params["capabilities"])


class TestThereIsOnlyOneUpstreamHandshake:
    def test_no_second_path_can_declare_something_else(self):
        """One call site, so one declaration -- checked structurally.

        A second handshake path that declared capabilities independently would
        make the tests above true and irrelevant at the same time: they would
        keep passing while a different code path told upstreams something else.
        Asserted over the AST rather than by grepping for the literal, so a
        rename of the method does not silently empty this check.
        """
        tree = ast.parse(_MCP_SERVER_SOURCE.read_text())

        initialize_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "initialize"
        ]

        assert len(initialize_calls) == 1, (
            f"expected exactly one upstream `initialize` call site, found {len(initialize_calls)} -- "
            "a second one can declare different capabilities without failing the tests above"
        )

    def test_the_declaration_is_a_literal_not_computed(self):
        """If it ever becomes computed, these characterizations stop being safe.

        A literal `{}` cannot vary by upstream, config or caller. The moment it
        becomes an expression, "what does Hangar declare" is no longer answerable
        from one test, and this suite needs to grow rather than be trusted.
        """
        tree = ast.parse(_MCP_SERVER_SOURCE.read_text())

        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "initialize"
            ):
                continue

            params = node.args[1]
            assert isinstance(params, ast.Dict), "initialize params should be a dict literal"

            capabilities = [
                value
                for key, value in zip(params.keys, params.values, strict=True)
                if isinstance(key, ast.Constant) and key.value == "capabilities"
            ]
            assert capabilities, "initialize must declare a capabilities key"
            assert isinstance(capabilities[0], ast.Dict), (
                "the upstream capability declaration is no longer a literal -- "
                "it can now vary at runtime, so it needs behavioural tests, not characterization"
            )
            return

        pytest.fail("no upstream initialize call found")
