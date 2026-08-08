"""A read-only method must not hand a pooled connection back "idle in transaction".

`PostgresConnectionFactory.get_connection` returns a borrowed connection to the
pool in a bare `finally`, regardless of transaction state. A method that runs a
`SELECT` and returns without ending the transaction it opened leaves that
connection *idle in transaction* for whoever borrows it next. In a single
in-process pool psycopg2's `putconn` rolls it back, so this is defensive hygiene
rather than a live bug -- but an external transaction-pooler (pgbouncer in
transaction mode) would keep such a transaction open server-side, pinning a
pgbouncer server connection and holding back `VACUUM`. See the "PostgreSQL
Connection Pooling" section of cookbook 23.

The same idiom already guards `PostgresEventStore` and
`PostgresMetricsHistoryStore`: after the `SELECT`, `commit()` (or `rollback()`)
closes the implicit transaction before the connection is yielded back.

This is a source-structural test, in the style of
`test_role_scope_is_validated.py`: a read method added later without closing its
transaction is a silent regression, so assert it per method rather than trusting
review to notice.
"""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

MODULE_PATH = "mcp_hangar.auth.infrastructure.postgres_store"


def _iter_methods() -> list[tuple[str, ast.FunctionDef, str]]:
    """Yield (qualified_name, node, source_segment) for every method in the module."""
    source = pathlib.Path(importlib.import_module(MODULE_PATH).__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    methods: list[tuple[str, ast.FunctionDef, str]] = []
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for node in cls.body:
            if isinstance(node, ast.FunctionDef):
                segment = ast.get_source_segment(source, node) or ""
                methods.append((f"{cls.name}.{node.name}", node, segment))
    return methods


def _executes_a_select(node: ast.FunctionDef) -> bool:
    """True if the method issues a `cur.execute(...)` whose SQL contains SELECT.

    Reads the string argument out of the AST (not the raw source) so a `SELECT`
    that appears only in a comment or a docstring does not count.
    """
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if not (isinstance(func, ast.Attribute) and func.attr == "execute"):
            continue
        if not call.args:
            continue
        arg = call.args[0]
        parts: list[str] = []
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            parts.append(arg.value)
        elif isinstance(arg, ast.JoinedStr):  # f-string
            parts.extend(v.value for v in arg.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
        if any("SELECT" in p.upper() for p in parts):
            return True
    return False


def _closes_its_transaction(segment: str) -> bool:
    return ".commit(" in segment or ".rollback(" in segment


_READ_METHODS = [(name, segment) for name, node, segment in _iter_methods() if _executes_a_select(node)]


def test_some_read_methods_were_discovered():
    """Guard the guard: if the parse finds nothing, the assertion below is vacuous."""
    names = {name for name, _ in _READ_METHODS}
    # These are the read paths the hardening review named.
    assert {
        "PostgresApiKeyStore.get_principal_for_key",
        "PostgresApiKeyStore.is_initial_admin_bootstrapped",
        "PostgresApiKeyStore.list_keys",
        "PostgresApiKeyStore.count_keys",
        "PostgresRoleStore.get_role",
        "PostgresRoleStore.get_roles_for_principal",
    } <= names, f"expected read paths not found among SELECT-issuing methods: {sorted(names)}"


@pytest.mark.parametrize("name, segment", _READ_METHODS, ids=[n for n, _ in _READ_METHODS])
def test_a_select_method_closes_its_transaction(name: str, segment: str):
    assert _closes_its_transaction(segment), (
        f"{name} runs a SELECT but never commits/rolls back its get_connection() block; "
        "a pooled connection would be returned 'idle in transaction' "
        "(see cookbook 23, PostgreSQL Connection Pooling)"
    )
