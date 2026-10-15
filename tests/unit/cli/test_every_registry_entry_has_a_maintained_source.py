"""Every entry `init` and `add` can write has a source that is maintained (#1663).

`add sqlite` wrote `@modelcontextprotocol/server-sqlite` (404 on npm) and
`add github` / `add postgres` wrote packages npm marks "no longer supported".

This test cannot ask a registry; `scripts/check_registry_packages.py` does, weekly.
What it can do is make adding an entry a conscious act: a package is written
down here with the date someone looked it up, and an entry nobody looked up
fails.
"""

from __future__ import annotations

from mcp_hangar.server.cli.services import PROVIDER_BUNDLES, get_all_mcp_servers

#: Looked up on 2026-10-15: published, not deprecated, and from the publisher named.
VERIFIED = {
    ("npx", "@modelcontextprotocol/server-filesystem"): "npm, Anthropic modelcontextprotocol/servers",
    ("npx", "@modelcontextprotocol/server-memory"): "npm, Anthropic modelcontextprotocol/servers",
    ("uvx", "mcp-server-fetch"): "PyPI, Anthropic, PBC.",
    ("uvx", "mcp-server-git"): "PyPI, Anthropic, PBC.",
}

#: Packages that were in the registry and are dead: 404 on npm, or npm marks them unsupported.
DEAD = {
    "@modelcontextprotocol/server-github",
    "@modelcontextprotocol/server-postgres",
    "@modelcontextprotocol/server-slack",
    "@modelcontextprotocol/server-puppeteer",
    "@modelcontextprotocol/server-brave-search",
    "@modelcontextprotocol/server-google-maps",
    "@modelcontextprotocol/server-sqlite",
    "@modelcontextprotocol/server-fetch",
    "@modelcontextprotocol/server-git",
}


def test_every_entry_was_looked_up():
    unlooked = {(e.install_type, e.package) for e in get_all_mcp_servers()} - set(VERIFIED)
    assert not unlooked, f"look these up (npm view / PyPI), then add them to VERIFIED: {sorted(unlooked)}"


def test_no_entry_names_a_dead_package():
    for entry in get_all_mcp_servers():
        assert entry.package not in DEAD, entry.name
        assert entry.uvx_package not in DEAD, entry.name


def test_every_bundle_member_is_an_entry():
    names = {e.name for e in get_all_mcp_servers()}
    for bundle, members in PROVIDER_BUNDLES.items():
        assert set(members) <= names, (bundle, set(members) - names)


def test_no_bundle_is_empty_or_the_same_as_another():
    seen: dict[tuple[str, ...], str] = {}
    for bundle, members in PROVIDER_BUNDLES.items():
        assert members, bundle
        key = tuple(sorted(members))
        assert key not in seen, f"{bundle} is the same as {seen[key]}"
        seen[key] = bundle
