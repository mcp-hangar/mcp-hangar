"""A section with a reader and no schema entry is refused under strict mode.

`ui_resources` shipped in 2.13.1 with a reader, a docs page and an upgrade-guide
example, and no entry in `SECTIONS`. So `validate_config` called it an unknown
key: under `HANGAR_CONFIG_STRICT=1` -- the posture the docs recommend for CI and
staging -- the gateway refused to start on the config the docs told the operator
to write, and everywhere else `hangar config validate` reported a key that "is
kept and ignored, so the setting simply does not apply" about a setting that
does apply (#1167).

`SECTIONS` is maintained by hand, one entry per feature, which is the right
trade (see the module docstring on why a deeper schema would drift). This is the
cheap half of a gate for it: whatever the loader reads off the top-level config
document must be a section the schema knows.

Deliberately narrow. It matches `full_config.get("<literal>")`, which is how the
`_init_*_from_config` family reads a top-level section, and nothing else: a
broader match over every `config.get(...)` in the tree would sweep up nested
keys and turn this into the drifting second source of truth `config_schema`
declines to be. The docs-vs-schema direction cannot live here at all -- the
published docs are in the `mcp-hangar/docs` repository, not this one.
"""

from __future__ import annotations

import ast
from pathlib import Path

from mcp_hangar.server.config_schema import SECTIONS

_SRC = Path(__file__).resolve().parents[2] / "src" / "mcp_hangar"


def _sections_read_from_the_config_document() -> dict[str, list[str]]:
    """Every ``full_config.get("<name>")`` in the package, name -> where."""
    found: dict[str, list[str]] = {}
    for path in _SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "get" or not isinstance(node.func.value, ast.Name):
                continue
            if node.func.value.id != "full_config" or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.setdefault(first.value, []).append(f"{path.relative_to(_SRC)}:{node.lineno}")
    return found


def test_every_section_the_loader_reads_is_in_the_schema():
    read = _sections_read_from_the_config_document()

    # The scan itself has to keep working: an empty result would pass this test
    # while proving nothing, which is how a gate quietly stops being one.
    assert len(read) >= 10, f"the scan found almost nothing, so it is no longer matching the readers: {read}"

    missing = {name: where for name, where in read.items() if name not in SECTIONS}
    assert not missing, (
        "these top-level sections are read but not in SECTIONS, so validate_config calls them unknown "
        f"and HANGAR_CONFIG_STRICT=1 refuses the config that declares them: {missing}"
    )


def test_ui_resources_survives_strict_validation():
    """The reported config, verbatim from `docs/upgrade.md`."""
    from mcp_hangar.server.config_schema import validate_config

    problems = validate_config(
        {
            "mcp_servers": {},
            "ui_resources": {"tenants": {"tenant:a": {"allowlist": ["ui://reports/"]}}},
        }
    )

    assert problems == []


def test_a_typo_inside_the_section_is_still_reported():
    """Adding the section must not make its contents unvalidated."""
    from mcp_hangar.server.config_schema import validate_config

    problems = validate_config({"mcp_servers": {}, "ui_resources": {"tenant": {}}})

    assert problems and "tenant" in problems[0]
