"""The declared `mcp` dependency must stay on the SDK line this branch targets.

Both release lines solve the same class of problem and must keep solving it:

* `main` (v1) serves `mcp.server.fastmcp`, which SDK v2 removed, so it caps at
  `>=1.28.1,<2`. Uncapped, a plain install follows the SDK into 2.x and the
  gateway dies at import (#561).
* this branch (v2) is pinned **exactly** to a beta. The Tasks surface is still
  moving inside the series — SEP-2663 removes `tasks/list` and adds
  `tasks/update` — and `_sdk_compat` shims against b2 internals, so drifting
  within the betas breaks silently. b2 -> b3 is a deliberate bump.

A published wheel cannot be edited, so a bad pin is one of the few defects a
release makes permanent for everyone who installed it. Hence a test on the
metadata rather than trusting review.
"""

from __future__ import annotations

from pathlib import Path
import tomllib

from packaging.requirements import Requirement
from packaging.version import Version
import pytest

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

#: SDK versions this branch's code cannot run on. It targets the v2 server
#: surface (`mcp.server.mcpserver`), so a v1 resolve is the failure mode here --
#: the exact mirror of what `main` guards against.
_UNRUNNABLE = ("1.28.1", "1.0.0")


def _requirement(name: str) -> Requirement:
    """Return the declared requirement for *name*, parsed properly.

    Uses `packaging` rather than string surgery. A hand-rolled parser split the
    whole line on "," and inspected prefixes, which silently mis-read
    `mcp==2.0.0b2` -- the first part starts with the package name, not the
    operator -- and only appeared to work on `main`'s two-part `>=1.28.1,<2`.
    """
    data = tomllib.loads(_PYPROJECT.read_text())
    for raw in data["project"]["dependencies"]:
        requirement = Requirement(raw)
        if requirement.name == name:
            return requirement
    raise AssertionError(f"{name} is not a declared dependency of mcp-hangar")


class TestMcpSdkPin:
    def test_the_pin_is_bounded_above(self):
        """Some SDK version must be excluded, or resolution can cross a major."""
        specifier = _requirement("mcp").specifier

        assert any(spec.operator in ("<", "<=", "==", "~=", "===") for spec in specifier), (
            f"`{specifier}` is unbounded above: resolution will follow the SDK past what this branch runs on"
        )

    def test_the_pin_excludes_the_sdk_line_this_branch_cannot_run(self):
        """A bound that exists but admits the wrong major is not the property that matters."""
        specifier = _requirement("mcp").specifier

        for rejected in _UNRUNNABLE:
            assert Version(rejected) not in specifier, (
                f"mcp {rejected} still satisfies `{specifier}` -- this branch uses a server surface it does not have"
            )

    def test_the_protocol_types_dist_tracks_the_sdk_exactly(self):
        """`mcp-types` is the split protocol-types dist; a mismatch is a wire mismatch.

        Specific to the v2 line -- v1 had no separate types distribution.
        """
        mcp = _requirement("mcp").specifier
        mcp_types = _requirement("mcp-types").specifier

        assert str(mcp) == str(mcp_types), f"mcp {mcp} and mcp-types {mcp_types} would resolve independently"

    @pytest.mark.parametrize("package", ["structlog", "pydantic", "httpx"])
    def test_other_runtime_pins_are_declared(self, package: str):
        """Guards the lookup above: if these vanish, the mcp assertions are vacuous."""
        assert _requirement(package)


class TestHttpxPin:
    """`httpx` is the second dependency this code cannot follow across a major.

    httpx 1.0 drops `httpx.AsyncClient`, which the proxy path uses throughout.
    Unbounded, `pip install --pre mcp-hangar` -- the install the v2-preview docs
    recommend -- resolved httpx to `1.0.dev3` and the gateway died at startup.
    Found by the published-artifact smoke (gate D, #550), which is the only
    check that resolves dependencies the way a user's install does.
    """

    def test_the_pin_is_bounded_below_the_next_major(self):
        specifier = _requirement("httpx").specifier

        assert Version("1.0.0") not in specifier, (
            f"httpx 1.0 satisfies `{specifier}`, and it has no `AsyncClient` -- the gateway will not start"
        )

    def test_a_current_supported_httpx_still_resolves(self):
        """The cap must exclude the break, not the versions in use."""
        specifier = _requirement("httpx").specifier

        assert Version("0.28.1") in specifier, f"`{specifier}` excludes httpx 0.28.1, which this code runs on"
