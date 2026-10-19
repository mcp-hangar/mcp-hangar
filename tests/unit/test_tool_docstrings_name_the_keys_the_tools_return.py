"""The docstrings LLM clients read name the keys the tools really return (#1670).

`hangar_list` and `hangar_details` documented `mcp_server` and sent
`mcp_server_id`; `hangar_load` documented `tools: list[str]` and sent
`[{"name": ...}]`; `hangar_start` named an `unknown_group` error that nothing
raises. A client written to the docstring read `None`.

The docstrings are read from the source, so nothing here starts a gateway. The
real keys come from the read models and results the tools return as they are.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from mcp_hangar.application.commands.load_handlers import LoadResult
from mcp_hangar.application.read_models.mcp_server_views import (
    DeadInfo,
    HealthInfo,
    McpServerDetails,
    McpServerSummary,
)

_TOOLS = Path(__file__).resolve().parents[2] / "src" / "mcp_hangar" / "server" / "tools"


def _functions(module: str) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse((_TOOLS / f"{module}.py").read_text(encoding="utf-8"))
    return {node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _returns(module: str, function: str) -> str:
    doc = ast.get_docstring(_functions(module)[function]) or ""
    return doc.split("Returns:", 1)[1].split("Example:", 1)[0]


def _keys(text: str, opener: str) -> set[str]:
    """The top-level keys, written ``name:`` or ``name?:``, of the ``{...}`` that follows *opener*."""
    start = text.index("{", text.index(opener))
    depth, top = 0, []
    for ch in text[start:]:
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                break
        elif depth == 1:
            top.append(ch)
        if depth != 1 and ch not in "{[}]":
            top.append(" ")
    return set(re.findall(r"(\w+)\??:", "".join(top)))


def _summary() -> dict:
    dead = DeadInfo("crashed", 1.0, None, "start")
    return McpServerSummary("s", "ready", "subprocess", True, 1, "healthy", "d", False, dead).to_dict()


def _details() -> dict:
    health = HealthInfo(0, 0, 0, 1.0, True)
    return McpServerDetails("s", "ready", "subprocess", True, [], health, 0.0, {}, None).to_dict()


def test_hangar_list_documents_the_summary_keys_it_returns():
    documented = _keys(_returns("hangar", "_hangar_list"), "mcp_servers: [{")
    assert documented == set(_summary()), sorted(documented ^ set(_summary()))


def test_hangar_details_documents_the_server_keys_it_returns():
    documented = _keys(_returns("mcp_server", "hangar_details"), "McpServer: {")
    # `tools_policy` and `withheld_tools` (#1776) are added by the tool on top of the read model.
    returned = set(_details()) | {"tools_policy", "withheld_tools"}
    assert documented == returned, sorted(documented ^ returned)


def test_hangar_load_documents_the_keys_a_loaded_server_returns():
    loaded = LoadResult(
        status="loaded",
        mcp_server_id="s",
        mcp_server_name="n",
        tools=[{"name": "t"}],
        message="m",
        warnings=["w"],
    ).to_dict()
    documented = _keys(_returns("hangar", "hangar_load"), "Success: {")
    assert documented == set(loaded), sorted(documented ^ set(loaded))
    assert "tools: [{name: str}]" in _returns("hangar", "hangar_load")


def _strings_outside_docstrings(tree: ast.AST) -> str:
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    return " ".join(
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    )


@pytest.mark.parametrize("module", ["hangar", "mcp_server", "groups", "discovery"])
def test_an_error_code_a_docstring_names_is_raised_by_the_module(module):
    """`unknown_group` was documented on `hangar_start`, which never raises it."""
    tree = ast.parse((_TOOLS / f"{module}.py").read_text(encoding="utf-8"))
    code = _strings_outside_docstrings(tree)
    for fn in _functions(module).values():
        for name in set(re.findall(r"\bunknown_\w+", ast.get_docstring(fn) or "")):
            assert name in code, f"{fn.name} documents {name}, which {module}.py never raises"
