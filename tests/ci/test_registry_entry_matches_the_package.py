"""The MCP Registry entry has to keep agreeing with what we actually ship.

Two couplings that no other check sees, and both fail late and permanently:

* The registry proves package ownership by fetching the PyPI metadata for
  exactly `packages[0].version` and scanning the README it serves for the token
  `mcp-name: <name>`. A README whose marker drifts from `server.json` -- or a
  name renamed on one side only -- means the publish step fails at release time,
  after the tag exists.
* `server.json`'s two version fields are written by release-please's
  `extra-files` updater. If that updater stops firing, the release publishes a
  stale version, and the registry rejects a re-publish of a version it already
  has.
"""

import json
import re
from pathlib import Path


ROOT = Path(__file__).parents[2]
SERVER_JSON = json.loads((ROOT / "server.json").read_text())


def test_readme_carries_the_ownership_marker_for_this_server_name() -> None:
    readme = (ROOT / "README.md").read_text()
    name = SERVER_JSON["name"]

    # The registry requires a boundary after the name so a short name cannot
    # claim a longer one: the next character must not continue a server name
    # ([A-Za-z0-9._/-]), or must open a comment close (`-->`).
    marker = f"mcp-name: {name}"
    assert marker in readme, f"README.md must contain {marker!r} for PyPI ownership validation"
    rest = readme.split(marker, 1)[1]
    assert rest.startswith("-->") or not re.match(r"[A-Za-z0-9._/-]", rest[:1] or " ")


def test_server_json_versions_track_pyproject() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text()
    version = re.search(r'^version = "(.*)"', pyproject, re.MULTILINE).group(1)

    assert SERVER_JSON["version"] == version
    assert SERVER_JSON["packages"][0]["version"] == version


def test_the_published_package_is_the_pypi_distribution() -> None:
    pkg = SERVER_JSON["packages"][0]

    assert pkg["registryType"] == "pypi"
    assert pkg["identifier"] == "mcp-hangar"
    # stdio, not http: `mcp-hangar` with no arguments starts the stdio server,
    # and there is no hosted instance to point a URL at.
    assert pkg["transport"] == {"type": "stdio"}
    assert "remotes" not in SERVER_JSON
