"""A docstring's `Args:` entries name parameters that exist (#1195 follow-up).

The CLI rename in #1195 rewrote `mcp_server` to "MCP server" in every string the
package renders. It caught two it should not have: an `Args:` line is not prose,
it is the parameter's name, and rewriting it produced

    Args:
        MCP server: MCP server definition

which documents an argument no function has. Nothing failed -- the docstrings
are not parsed at runtime -- which is exactly why it is worth a gate: this is
the class of edit that a renamer makes silently and a reviewer skims past.

Google-style sections are what this repo writes, and the check is deliberately
narrow: a name that is not a parameter of the function it documents. Types,
defaults, prose lines and continuation lines are left alone.
"""

import ast
from pathlib import Path
import re

import pytest

CLI = Path(__file__).resolve().parents[3] / "src" / "mcp_hangar" / "server" / "cli"
#: `    name: description` under an `Args:` header, at the entry's indent.
ENTRY = re.compile(r"^(?P<indent>\s+)(?P<name>[^\s:][^:]*?)(?:\s*\([^)]*\))?:\s+\S")


def documented_args(docstring: str) -> list[str]:
    lines = docstring.splitlines()
    names: list[str] = []
    in_args = False
    entry_indent: int | None = None
    for line in lines:
        stripped = line.strip()
        if stripped in {"Args:", "Arguments:"}:
            in_args = True
            entry_indent = None
            continue
        if not in_args:
            continue
        if stripped.endswith(":") and stripped.rstrip(":") in {
            "Returns",
            "Raises",
            "Yields",
            "Note",
            "Example",
            "Examples",
        }:
            in_args = False
            continue
        if not stripped:
            continue
        match = ENTRY.match(line)
        if not match:
            continue
        indent = len(match.group("indent"))
        if entry_indent is None:
            entry_indent = indent
        if indent != entry_indent:  # a continuation line, not a new entry
            continue
        names.append(match.group("name").strip())
    return names


def functions_with_docstrings():
    for path in sorted(CLI.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            doc = ast.get_docstring(node)
            if not doc or "Args:" not in doc:
                continue
            args = node.args
            params = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
            if args.vararg:
                params.add(args.vararg.arg)
            if args.kwarg:
                params.add(args.kwarg.arg)
            yield path.relative_to(CLI.parent.parent.parent.parent), node.name, doc, params


@pytest.mark.parametrize(
    ("path", "func", "doc", "params"),
    list(functions_with_docstrings()),
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_every_documented_arg_is_a_parameter(path, func, doc, params):
    documented = documented_args(doc)
    unknown = [name for name in documented if name not in params]

    assert unknown == [], f"{path}:{func} documents {unknown}; its parameters are {sorted(params)}"
