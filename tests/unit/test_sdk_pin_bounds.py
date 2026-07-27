"""The declared `mcp` dependency must stay on the SDK line this branch targets (#561).

This branch serves the **v1** SDK surface (`mcp.server.fastmcp`), which SDK v2
removed. An uncapped `mcp>=1.28.1` therefore resolves to a 2.x release and the
gateway dies at import with `ModuleNotFoundError: No module named
'mcp.server.fastmcp'` — already the case under `--pre` (it picks 2.0.0b2), and
the case for a **plain** `pip install` the moment mcp 2.0.0 ships.

A published wheel cannot be edited, so this is one of the few defects that a
release makes permanent for everyone who installed it. Hence a test on the
metadata itself rather than trusting review.
"""

from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _requirement(name: str) -> str:
    data = tomllib.loads(_PYPROJECT.read_text())
    for raw in data["project"]["dependencies"]:
        # "mcp>=1.28.1,<2" -> name is everything up to the first specifier char.
        head = raw.split(";")[0].strip()
        package = head.split("[")[0]
        for token in ("<=", ">=", "==", "!=", "~=", "<", ">"):
            package = package.split(token)[0]
        if package.strip() == name:
            return head
    raise AssertionError(f"{name} is not a declared dependency of mcp-hangar")


def _upper_bounds(requirement: str) -> list[str]:
    return [part.strip() for part in requirement.split(",") if part.strip().startswith(("<", "==", "~="))]


class TestMcpSdkPin:
    def test_the_mcp_pin_has_an_upper_bound(self):
        """Without one, resolution silently crosses the SDK major that broke us."""
        requirement = _requirement("mcp")

        assert _upper_bounds(requirement), (
            f"`{requirement}` is unbounded above: a plain install will follow mcp into 2.x, "
            "whose server surface this branch does not use. See #561."
        )

    def test_the_upper_bound_excludes_the_v2_line(self):
        """The bound must actually exclude 2.x, not merely exist."""
        from packaging.requirements import Requirement
        from packaging.version import Version

        specifier = Requirement(_requirement("mcp")).specifier

        assert Version("1.28.1") in specifier, "the supported v1 SDK no longer satisfies the pin"
        for rejected in ("2.0.0", "2.0.0b2", "2.1.0"):
            assert Version(rejected) not in specifier, (
                f"mcp {rejected} still satisfies `{specifier}` — the import surface it removed is used here"
            )

    @pytest.mark.parametrize("package", ["structlog", "pydantic", "httpx"])
    def test_other_runtime_pins_are_declared(self, package: str):
        """Guards the parser above: if these vanish, the mcp assertions are vacuous."""
        assert _requirement(package)


class TestHttpxPin:
    """`httpx` is the second dependency this code cannot follow across a major.

    httpx 1.0 drops `httpx.AsyncClient`, which the proxy path uses throughout.
    Not yet breaking on this line -- httpx 1.0 is still a dev release, and a
    plain `pip install` will not take it -- but the same latent break as the
    `mcp` pin above, and permanent in any wheel published after 1.0 goes final.

    Proven on the v2 line, where the documented `pip install --pre mcp-hangar`
    resolved httpx to `1.0.dev3` and the gateway died at startup with
    `module 'httpx' has no attribute 'AsyncClient'`. Found by the
    published-artifact smoke (gate D, #550).
    """

    def test_the_pin_has_an_upper_bound(self):
        requirement = _requirement("httpx")

        assert _upper_bounds(requirement), (
            f"`{requirement}` is unbounded above: resolution will follow httpx into 1.x, which has no AsyncClient"
        )

    def test_the_upper_bound_excludes_httpx_1_0(self):
        """A bound that exists but still admits 1.0 is not the property that matters."""
        requirement = _requirement("httpx")

        assert "<1" in requirement.replace(" ", ""), f"`{requirement}` does not exclude httpx 1.x"
