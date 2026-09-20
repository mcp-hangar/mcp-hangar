"""Every tool call runs through the one executor that carries the configured interceptors (#1425).

``configure_interceptors`` builds a ``BatchExecutor`` with the configured
validator pipeline and makes it the one ``configured_executor()`` returns. An
executor built anywhere else has an empty pipeline, so a call path that builds
its own runs no validator and nothing fails: that is how the front door's flat
``tools/call`` ran without them. So the only place in ``src/`` that may build
one is the module that configures it.

This is the static half. The served half is the reachability test
(``test_security_predicates_are_reachable.py``), which holds the configured
validator to ``hangar_call`` and to the front door's flat call.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

import mcp_hangar
import mcp_hangar.server.tools.batch as batch
from mcp_hangar.domain.contracts.validator import ValidationContext

SRC = Path(mcp_hangar.__file__).resolve().parent
#: The module that configures the interceptors, and so the one that builds the executor.
OWNER = "server/tools/batch/__init__.py"
_NAME = "BatchExecutor"


def _sites() -> tuple[list[str], list[str]]:
    """Each ``BatchExecutor(...)`` call and each renaming import of it in ``src/``, as ``<file>:<line>``."""
    built: list[str] = []
    renamed: list[str] = []
    for source in sorted(SRC.rglob("*.py")):
        where = source.relative_to(SRC).as_posix()
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name == _NAME:
                    built.append(f"{where}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                if any(alias.name == _NAME and alias.asname not in (None, _NAME) for alias in node.names):
                    renamed.append(f"{where}:{node.lineno}")
    return built, renamed


class TestOnlyTheConfiguringModuleBuildsAnExecutor:
    def test_no_other_module_builds_one(self) -> None:
        built, _ = _sites()

        elsewhere = [site for site in built if not site.startswith(f"{OWNER}:")]

        assert not elsewhere, (
            f"BatchExecutor() is built outside {OWNER}: {elsewhere}. An executor built there has an empty "
            "interceptor pipeline, so calls through it skip the configured validators. Dispatch through "
            "configured_executor() instead."
        )

    def test_no_module_imports_it_under_another_name(self) -> None:
        _, renamed = _sites()

        assert not renamed, f"BatchExecutor is imported under another name, which the scan above cannot see: {renamed}"

    def test_the_scan_finds_the_executor_the_interceptors_configure(self) -> None:
        # A scan that finds nothing would pass the first test for any source tree.
        built, _ = _sites()

        assert any(site.startswith(f"{OWNER}:") for site in built), f"no BatchExecutor() found in {OWNER}"


@pytest.fixture
def _unconfigured() -> Iterator[None]:
    yield
    batch.configure_interceptors(None)


def _refused(payload: dict[str, object]) -> bool:
    context = ValidationContext(method="tools/call", direction="request", payload=payload, correlation_id="c-1")
    return not batch.configured_executor()._validator_pipeline.execute(context).allowed


@pytest.mark.usefixtures("_unconfigured")
class TestTheConfiguredExecutorIsReadPerCall:
    def test_it_carries_the_interceptors_configured_after_import(self) -> None:
        batch.configure_interceptors([{"type": "payload_size", "max_bytes": 50}])

        assert _refused({"name": "t", "arguments": {"blob": "a" * 500}})
        assert not _refused({"name": "t", "arguments": {"x": 1}})

    def test_it_is_the_executor_hangar_call_runs(self) -> None:
        batch.configure_interceptors([{"type": "payload_size", "max_bytes": 50}])

        assert batch.configured_executor() is batch._executor
