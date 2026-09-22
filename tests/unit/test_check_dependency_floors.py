"""The `deps-floor-audit` gate: a declared floor may not sit below what `mcp` forces.

The case it exists for: we declared `pydantic>=2.0.0` and `pyjwt>=2.8.0` while
`mcp==2.0.0` required `pydantic>=2.12.0` and `pyjwt[crypto]>=2.10.1`, so the
metadata described installs nobody could get, and a scanner reading it audited
versions that were never there.
"""

from __future__ import annotations

import datetime
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_dependency_floors.py"


def _load():
    spec = importlib.util.spec_from_file_location("_dependency_floors_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


floors = _load()

MCP = [
    "pydantic>=2.12.0",
    "pyjwt[crypto]>=2.10.1",
    "anyio>=4.9; python_version < '3.14'",
    "anyio>=4.10; python_version >= '3.14'",
    "typer>=0.16.0; extra == 'cli'",
]


def test_the_installed_declaration_is_not_below_mcp():
    assert floors.check_floors() == 0


def test_a_floor_lowered_below_mcp_is_named_with_both_values():
    problems = floors.fictions(["pydantic>=2.0.0", "pyjwt[crypto]>=2.13.0"], MCP, "mcp")

    assert problems == [
        "pydantic: we declare `pydantic>=2.0.0` but mcp requires `pydantic>=2.12.0`; raise our floor to at least 2.12.0"
    ]


def test_a_missing_floor_counts_as_below():
    assert floors.fictions(["PyJWT"], MCP, "mcp")[0].startswith("pyjwt: we declare `PyJWT`")


def test_every_python_variant_of_their_floor_must_hold():
    problems = floors.fictions(["anyio>=4.9"], MCP, "mcp")

    assert len(problems) == 1 and "anyio>=4.10" in problems[0]


def test_their_extras_are_not_ours():
    assert floors.fictions(["typer>=0.12.0"], MCP, "mcp") == []


def test_equal_or_higher_floors_pass():
    assert floors.fictions(["pydantic>=2.12.0", "pyjwt[crypto]>=2.13.0", "anyio>=4.10"], MCP, "mcp") == []


TODAY = datetime.date(2026, 9, 22)


def _ignore_file(tmp_path, body: str) -> pathlib.Path:
    path = tmp_path / ".pip-audit-ignore.toml"
    path.write_text(body)
    return path


def test_the_committed_ignore_file_is_valid():
    floors.ignore_args(ROOT / ".pip-audit-ignore.toml", datetime.date.today())


def test_a_complete_entry_becomes_an_ignore_argument(tmp_path):
    path = _ignore_file(tmp_path, '[[ignore]]\nid = "PYSEC-1"\nreason = "never called"\nexpires = 2026-12-31\n')

    assert floors.ignore_args(path, TODAY) == ["--ignore-vuln", "PYSEC-1"]


def test_an_entry_past_its_expiry_fails(tmp_path):
    path = _ignore_file(tmp_path, '[[ignore]]\nid = "PYSEC-1"\nreason = "never called"\nexpires = 2026-09-21\n')

    with pytest.raises(ValueError, match="PYSEC-1: expired on 2026-09-21"):
        floors.ignore_args(path, TODAY)


@pytest.mark.parametrize(
    "body, message",
    [
        ('[[ignore]]\nid = "PYSEC-1"\nexpires = 2026-12-31\n', "missing `reason`"),
        ('[[ignore]]\nid = "PYSEC-1"\nreason = "  "\nexpires = 2026-12-31\n', "missing `reason`"),
        ('[[ignore]]\nid = "PYSEC-1"\nreason = "never called"\n', "`expires` must be a date"),
        ('[[ignore]]\nid = "PYSEC-1"\nreason = "never called"\nexpires = "someday"\n', "`expires` must be a date"),
        ('[[ignore]]\nreason = "never called"\nexpires = 2026-12-31\n', "missing `id`"),
    ],
)
def test_an_incomplete_entry_fails(tmp_path, body, message):
    with pytest.raises(ValueError, match=message):
        floors.ignore_args(_ignore_file(tmp_path, body), TODAY)
