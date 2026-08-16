**core:** Hangar is published in the Official MCP Registry as
`io.mcp-hangar/hangar`. A `server.json` at the repository root describes the
PyPI distribution started over stdio -- `mcp-hangar` with no arguments -- and
nothing else: there is no hosted instance, so the entry declares no `remotes`.
Both of its version fields track `pyproject.toml` through release-please, and a
`publish-registry` job in the release workflow publishes the entry after the
PyPI upload for that tag exists, since the registry proves ownership by reading
the `mcp-name:` marker out of the README that PyPI serves for exactly that
version. Stable releases only: PyPI serves a prerelease under its PEP 440
spelling, which is not the spelling `server.json` carries
