"""A subprocess backend must talk over its pipe, not open a port.

Found by a security audit against 2.3.0. The built-in default configuration --
what runs when there is no `config.yaml` -- launched
`examples.provider_math.server` as a subprocess with no environment, and that
module defaulted to `streamable-http` on `MCP_HOST`, which defaults to
`0.0.0.0`.

Two consequences, and the second is the security one:

* the launcher speaks stdio, so the backend never answered and every call
  failed with `startup_timeout` after 30 s -- a fresh install could not invoke
  a single tool;
* the child served MCP on `0.0.0.0:8080` with no authentication, no rate limit,
  no audit trail and no L7 egress policy. Anyone who could reach the host could
  call the backend's tools directly, going around the gateway rather than
  through it.

Nothing caught it because nothing exercised that path: `grep -rl math_subprocess
tests/` returned nothing, and every live test sets `MCP_TRANSPORT: stdio`
explicitly, configuring its way around the broken default.

The fix is in the launcher rather than only in the example, because the same
mistake is available to anyone writing a subprocess provider against an SDK
whose server defaults to HTTP. These tests hold the launcher and the examples.
The third layer, the built-in default configuration, is gone: a missing
`config.yaml` is now refused rather than replaced (#1650).
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from mcp_hangar.infrastructure.launchers.subprocess import SubprocessLauncher

REPO = pathlib.Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples"


class TestTheLauncherDefaultsTheChildToStdio:
    """The layer that protects providers we did not write."""

    def test_a_child_with_no_env_gets_stdio(self):
        env = SubprocessLauncher()._prepare_env(None)
        assert env["MCP_TRANSPORT"] == "stdio"

    def test_a_child_with_other_env_still_gets_stdio(self):
        env = SubprocessLauncher()._prepare_env({"SOME_SETTING": "x"})
        assert env["MCP_TRANSPORT"] == "stdio"
        assert env["SOME_SETTING"] == "x"

    def test_an_explicit_transport_still_wins(self):
        """A default, not a rule -- a provider using HTTP deliberately is not broken."""
        env = SubprocessLauncher()._prepare_env({"MCP_TRANSPORT": "streamable-http"})
        assert env["MCP_TRANSPORT"] == "streamable-http"


def _example_transport_default(path: pathlib.Path) -> str | None:
    """The literal default in `os.environ.get("MCP_TRANSPORT", <default>)`."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "MCP_TRANSPORT"
            and len(node.args) > 1
            and isinstance(node.args[1], ast.Constant)
        ):
            return node.args[1].value
    return None


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*/server.py")), ids=lambda p: p.parent.name)
def test_no_example_backend_defaults_to_a_network_transport(path):
    """Examples are copied. One that binds a port by default teaches that.

    `provider_math` defaulted to `streamable-http` while `provider_identity`
    defaulted to `stdio`, so which one a reader copied decided whether their
    backend was reachable from the network.
    """
    default = _example_transport_default(path)
    if default is None:
        pytest.skip(f"{path.parent.name} does not read MCP_TRANSPORT")
    assert default == "stdio", (
        f"{path.parent.name} defaults to {default!r}; a copied example should not "
        "open a network listener unless its author asks for one"
    )
