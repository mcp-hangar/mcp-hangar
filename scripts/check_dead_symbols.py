#!/usr/bin/env python3
"""Find public symbols in `src/` that nothing references, and hold the count down.

Five times this month a defect turned out to be code that could not run: an
adapter never constructed, a port never injected, a module with no callers, a
fallback beside an injected dependency. Each was found by accident while chasing
something else. This makes the question askable on purpose.

A symbol counts as DEAD when its name appears nowhere in `src/` or `tests/` as
an expression -- not as a call, an argument, an import, an annotation, a
decorator, or a base class. Its own `def`/`class` statement does not count,
because the name there is a string attribute of the node rather than a
reference.

Three lessons are baked into how references are collected, each from a false
positive the first version produced:

* `from x import y as z` is a use of `y`. Recording only the alias made
  `resolve_legacy_mcp_server_id` look dead while a command module imported it.
* A symbol used only inside its own file is not dead, it is file-private.
  `get_current_version` is called three times by its own module.
* Route handlers are referenced by name in a route table -- `Route("/",
  list_groups)` -- not by a decorator. Counting in-file uses catches those too;
  a decorator-only allowlist would not have.

`__all__` membership is NOT a use. A symbol exported and referenced nowhere is
reported separately: deleting it is an API change, which is a different
decision from deleting something private, so the two are counted apart.

Run `--update` to rewrite the baseline after a deliberate deletion.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "mcp_hangar"
TESTS = ROOT / "tests"
PYPROJECT = ROOT / "pyproject.toml"


def _parsed() -> dict[pathlib.Path, ast.Module]:
    out: dict[pathlib.Path, ast.Module] = {}
    for base in (SRC, TESTS):
        for path in base.rglob("*.py"):
            try:
                out[path] = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
    return out


def _referenced_names(trees: dict[pathlib.Path, ast.Module]) -> set[str]:
    """Every identifier used as an expression anywhere, excluding `__all__` entries."""
    used: set[str] = set()
    for tree in trees.values():
        exported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
            ):
                for item in ast.walk(node.value):
                    if isinstance(item, ast.Constant) and isinstance(item.value, str):
                        exported.add(item.value)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                used.add(node.attr)
            elif isinstance(node, ast.alias):
                # `from x import y as z` uses y; the alias is a local rebinding.
                used.add(node.name.split(".")[-1])
                if node.asname:
                    used.add(node.asname)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value not in exported:
                    used.add(node.value)
    return used


def _exported_names(trees: dict[pathlib.Path, ast.Module]) -> set[str]:
    out: set[str] = set()
    for path, tree in trees.items():
        if not str(path).startswith(str(SRC)):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
            ):
                for item in ast.walk(node.value):
                    if isinstance(item, ast.Constant) and isinstance(item.value, str):
                        out.add(item.value)
    return out


def scan() -> tuple[list[str], list[str]]:
    """Return (dead, exported_but_unreferenced), each as `path::symbol`."""
    trees = _parsed()
    used = _referenced_names(trees)
    exported = _exported_names(trees)

    dead: list[str] = []
    exported_unused: list[str] = []
    for path, tree in trees.items():
        if not str(path).startswith(str(SRC)):
            continue
        rel = path.relative_to(SRC).as_posix()
        for node in tree.body:
            if not isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if node.name.startswith("_") or node.name in used:
                continue
            if _framework_registered(node):
                continue
            (exported_unused if node.name in exported else dead).append(f"{rel}::{node.name}")
    return sorted(dead), sorted(exported_unused)


def _framework_registered(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether a framework holds the reference instead of the codebase.

    `@app.command("zsh")` hands the function to Typer at import; nothing in the
    tree names it again, and it is very much alive. Detected structurally rather
    than by an allowlist of decorator names: at module level, a decorator that is
    an attribute access on an object (`app.command`, `mcp.tool`, `router.get`) is
    a registrar. The plain decorators -- `@dataclass`, `@runtime_checkable`,
    `@property` -- are bare names, so the two do not overlap.
    """
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute):
            return True
    return False


def _baseline() -> tuple[set[str], set[str]]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    section = data.get("tool", {}).get("dead_symbols", {})
    return set(section.get("unreferenced", [])), set(section.get("exported_unreferenced", []))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="rewrite the baseline from the current scan")
    args = parser.parse_args()

    dead, exported_unused = scan()

    if args.update:
        _write_baseline(dead, exported_unused)
        print(f"baseline updated: {len(dead)} unreferenced, {len(exported_unused)} exported-unreferenced")
        return 0

    known_dead, known_exported = _baseline()
    new_dead = sorted(set(dead) - known_dead)
    new_exported = sorted(set(exported_unused) - known_exported)
    gone = sorted((known_dead - set(dead)) | (known_exported - set(exported_unused)))

    status = 0
    if new_dead or new_exported:
        status = 1
        print("New unreferenced public symbols. Either something is wired up wrong,")
        print("or the symbol is genuinely unused and should not have been added.\n")
        for entry in new_dead:
            print(f"  unreferenced          {entry}")
        for entry in new_exported:
            print(f"  exported, unreferenced {entry}")
        print("\nIf the addition is deliberate (a public API for embedders, say),")
        print("run `python scripts/check_dead_symbols.py --update` and say why in the PR.")

    if gone:
        print(f"\n{len(gone)} baselined symbol(s) are gone -- tighten the baseline:")
        for entry in gone[:10]:
            print(f"  removed  {entry}")
        print("Run `python scripts/check_dead_symbols.py --update` to lock the progress in.")
        status = status or 2

    if status == 0:
        print(f"dead-symbol baseline holds: {len(dead)} unreferenced, {len(exported_unused)} exported-unreferenced")
    return status


def _write_baseline(dead: list[str], exported_unused: list[str]) -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    block = ["[tool.dead_symbols]", "unreferenced = ["]
    block += [f'  "{e}",' for e in dead]
    block += ["]", "exported_unreferenced = ["]
    block += [f'  "{e}",' for e in exported_unused]
    block += ["]"]
    new = "\n".join(block) + "\n"
    marker = "[tool.dead_symbols]"
    if marker in text:
        start = text.index(marker)
        rest = text[start:]
        end = (
            rest.index("\n[", rest.index("exported_unreferenced"))
            if "\n[" in rest[rest.index("exported_unreferenced") :]
            else len(rest)
        )
        text = text[:start] + new + rest[end + 1 :]
    else:
        text = text.rstrip("\n") + "\n\n" + new
    PYPROJECT.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
