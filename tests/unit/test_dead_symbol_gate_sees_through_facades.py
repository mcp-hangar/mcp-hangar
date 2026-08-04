"""A package facade must not make an unused symbol look used.

`_referenced_names` counted every `ast.alias` as a use, so this shape --

    # package/__init__.py
    from .module import Thing
    __all__ = ["Thing"]

-- marked `Thing` referenced even when nothing in `src/` or `tests/` imported it.
The scanner already excluded `__all__` STRING entries, so half the problem was
seen; the import that feeds them was not.

Found while measuring the security event handler: `CallbackSecuritySink` and
`CompositeSecuritySink` had zero references anywhere, tests included, and the
gate reported a clean baseline. The gate exists to find code that cannot run,
and a blind spot shaped like "everything re-exported by a package" is a large
thing for it not to see.

`__all__` is the right marker to key on rather than "any import in an
`__init__.py`": ruff uses the same one to decide an `__init__.py` import is a
deliberate re-export instead of an F401, so the two tools agree on what a facade
is.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_dead_symbols.py"


def _load_scanner():
    spec = importlib.util.spec_from_file_location("_dead_symbols_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def scanner():
    return _load_scanner()


def _facade(module_src: str, init_src: str, tmp_path: pathlib.Path) -> pathlib.Path:
    pkg = tmp_path / "src" / "mcp_hangar" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "module.py").write_text(module_src, encoding="utf-8")
    (pkg / "__init__.py").write_text(init_src, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    return tmp_path


class TestAReExportIsNotAUse:
    def test_a_symbol_only_re_exported_is_reported(self, scanner, tmp_path, monkeypatch):
        root = _facade(
            "class Thing:\n    pass\n",
            'from .module import Thing\n\n__all__ = ["Thing"]\n',
            tmp_path,
        )
        monkeypatch.setattr(scanner, "SRC", root / "src" / "mcp_hangar")
        monkeypatch.setattr(scanner, "TESTS", root / "tests")

        dead, exported_unused = scanner.scan()

        assert any("Thing" in entry for entry in exported_unused), (
            "a class whose only mention is a package re-export is reported as used; "
            f"dead={dead} exported={exported_unused}"
        )

    def test_a_symbol_an_actual_consumer_imports_is_not_reported(self, scanner, tmp_path, monkeypatch):
        """The discrimination that matters: re-export does not count, consumption does."""
        root = _facade(
            "class Thing:\n    pass\n",
            'from .module import Thing\n\n__all__ = ["Thing"]\n',
            tmp_path,
        )
        (root / "src" / "mcp_hangar" / "consumer.py").write_text(
            "from .pkg import Thing\n\n\ndef use() -> Thing:\n    return Thing()\n", encoding="utf-8"
        )
        monkeypatch.setattr(scanner, "SRC", root / "src" / "mcp_hangar")
        monkeypatch.setattr(scanner, "TESTS", root / "tests")

        dead, exported_unused = scanner.scan()

        assert not any("Thing" in entry for entry in dead + exported_unused), (
            "a symbol a real consumer imports is reported as dead; the fix went too far"
        )

    def test_a_facade_that_also_uses_the_symbol_still_counts_that_use(self, scanner, tmp_path, monkeypatch):
        """Re-exporting and using are independent; the use is an `ast.Name`."""
        root = _facade(
            "class Thing:\n    pass\n",
            'from .module import Thing\n\n__all__ = ["Thing"]\n\ndefault = Thing()\n',
            tmp_path,
        )
        monkeypatch.setattr(scanner, "SRC", root / "src" / "mcp_hangar")
        monkeypatch.setattr(scanner, "TESTS", root / "tests")

        dead, exported_unused = scanner.scan()

        assert not any("Thing" in entry for entry in dead + exported_unused)


class TestTheLessonsAlreadyBakedInStillHold:
    """The scanner's docstring records three false positives its first version produced.

    The facade fix touches the same function those lessons live in, so they are
    re-asserted here rather than trusted.
    """

    def test_an_aliased_import_is_still_a_use_of_the_original(self, scanner, tmp_path, monkeypatch):
        """`from x import y as z` uses y -- this one made a live function look dead."""
        root = _facade("def thing():\n    pass\n", "", tmp_path)
        (root / "src" / "mcp_hangar" / "consumer.py").write_text(
            "from .pkg.module import thing as renamed\n\n\ndef use():\n    return renamed()\n", encoding="utf-8"
        )
        monkeypatch.setattr(scanner, "SRC", root / "src" / "mcp_hangar")
        monkeypatch.setattr(scanner, "TESTS", root / "tests")

        dead, exported_unused = scanner.scan()

        assert not any("thing" in entry for entry in dead + exported_unused)

    def test_a_symbol_used_only_in_its_own_file_is_not_dead(self, scanner, tmp_path, monkeypatch):
        """File-private, not dead. `get_current_version` is called by its own module."""
        root = _facade("def helper():\n    pass\n\n\ndef caller():\n    return helper()\n", "", tmp_path)
        monkeypatch.setattr(scanner, "SRC", root / "src" / "mcp_hangar")
        monkeypatch.setattr(scanner, "TESTS", root / "tests")

        dead, _ = scanner.scan()

        assert not any("helper" in entry for entry in dead)


def test_the_scanner_still_excludes_all_string_entries():
    """The half of the problem that was already handled must not regress."""
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_referenced_names")
    body = ast.unparse(fn)
    assert "exported" in body and "__all__" in body, (
        "_referenced_names no longer consults __all__; both the string-constant and "
        "the import-alias exclusions depend on it"
    )
