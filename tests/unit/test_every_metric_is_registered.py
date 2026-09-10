"""A metric defined but never registered is invisible (#1059).

`CollectorRegistry.collect()` walks only what was registered, so a collector
that `_register_all_metrics()` forgets accumulates in process memory and never
reaches a scrape. From outside that is indistinguishable from a feature nobody
built -- and it is worse than an absent metric when the docs promise a query
against it.

Four had been forgotten: the three approval-gate counters (dead since 2.10.0,
with three PromQL queries in `guides/OBSERVABILITY.md` that could never return
a row) and the Audit-mode egress observation counter, which is the signal
ADR-013 calls the safe adoption path for an egress policy.

This test is the fix. The named cases below would each have caught one; the
sweep catches the next one, which is the point.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from mcp_hangar import metrics as prometheus_metrics
from mcp_hangar.metrics import Counter, Gauge, Histogram, REGISTRY


def _module_level_metrics() -> dict[str, Counter | Gauge | Histogram]:
    return {
        name: value
        for name, value in vars(prometheus_metrics).items()
        if isinstance(value, (Counter, Gauge, Histogram))
    }


def test_every_module_level_metric_is_registered() -> None:
    """Walks the module rather than naming metrics, so the next one is covered too."""
    registered = {collector.name for collector in REGISTRY._collectors.values()}

    unregistered = sorted(
        f"{name} ({metric.name})" for name, metric in _module_level_metrics().items() if metric.name not in registered
    )

    assert not unregistered, (
        "defined but never registered, so absent from /metrics: "
        + ", ".join(unregistered)
        + " -- add them to _register_all_metrics()"
    )


def test_the_sweep_actually_sees_the_metrics() -> None:
    """A guard that walked an empty set would pass forever."""
    assert len(_module_level_metrics()) > 50


@pytest.mark.parametrize(
    "metric_name",
    [
        "mcp_hangar_approval_requests",
        "mcp_hangar_approval_deliveries",
        "mcp_hangar_approval_decisions",
        "mcp_hangar_egress_policy_violations_observed",
    ],
)
def test_a_previously_dead_metric_reaches_the_exposition(metric_name: str) -> None:
    """The four from #1059, named so a revert is loud about which one it broke."""
    assert REGISTRY.get(metric_name) is not None


def test_an_incremented_approval_counter_is_scrapable() -> None:
    """End to end through the exposition, not just the registry index."""
    prometheus_metrics.APPROVAL_DECISIONS_TOTAL.inc(channel="slack", decision="granted")

    exposition = prometheus_metrics.get_metrics().splitlines()
    assert any(line.startswith("mcp_hangar_approval_decisions_total{") for line in exposition)


def _absolute(module: str | None, level: int, package: str) -> str:
    """Resolve `from <dots><module> import ...` as written inside `package`."""
    if level == 0:
        return module or ""
    parts = package.split(".")
    base = parts[: len(parts) - (level - 1)]
    return ".".join([*base, module] if module else base)


def _reads_inside_the_metrics_module(metrics_file: pathlib.Path, names: set[str]) -> set[str]:
    """Loads in every top-level statement but the registration list.

    Walks `module.body`, not `ast.walk(module)`: the walk yields the module node
    first, and walking that covers the whole file, so a `continue` on
    `_register_all_metrics` never kept its list out and every registered metric
    counted as read (#1259).
    """
    read: set[str] = set()
    for statement in ast.parse(metrics_file.read_text(encoding="utf-8")).body:
        if isinstance(statement, ast.FunctionDef) and statement.name == "_register_all_metrics":
            continue
        read |= {
            node.id
            for node in ast.walk(statement)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in names
        }
    return read


def _reads_through_an_import(
    path: pathlib.Path, src_root: pathlib.Path, metrics_module: str, names: set[str]
) -> set[str]:
    """Loads in `path` that provably reach the metrics module.

    `alias.NAME` where `alias` is bound to the module, or `NAME` imported from
    it. A bare mention of the name is not a read: a class of string constants
    sharing four dead metrics' names was once taken for their writer (#1259).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = ".".join(path.relative_to(src_root).parts[:-1])
    module_aliases: set[str] = set()
    imported: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            source = _absolute(node.module, node.level, package)
            for alias in node.names:
                if source == metrics_module and alias.name in names:
                    imported[alias.asname or alias.name] = alias.name
                elif f"{source}.{alias.name}" == metrics_module:
                    module_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            module_aliases |= {alias.asname for alias in node.names if alias.name == metrics_module and alias.asname}

    read: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in module_aliases and node.attr in names:
                read.add(node.attr)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in imported:
            read.add(imported[node.id])
    return read


def _metric_reads(metrics_file: pathlib.Path, names: set[str]) -> set[str]:
    """The metric names something in the package reads, the registration list aside."""
    package_root = metrics_file.parent
    metrics_module = f"{package_root.name}.{metrics_file.stem}"
    read = _reads_inside_the_metrics_module(metrics_file, names)
    for path in package_root.rglob("*.py"):
        if path != metrics_file:
            read |= _reads_through_an_import(path, package_root.parent, metrics_module, names)
    return read


def _metrics_nothing_ever_writes_to() -> list[str]:
    """Module-level metrics referenced nowhere but their own definition.

    Registration was only half the question (#1059). A metric that is
    registered and never incremented is exposed as a TYPE header with no
    sample, which reads to an operator, a dashboard and a docs table exactly
    like a metric that is working and quiet -- `mcp_hangar_http_retries_total`
    shipped a Grafana panel that could never draw a line (#1163).

    A read from anywhere but the registration list -- including the `record_*`
    helpers in `metrics.py` itself -- counts as an emitter.
    """
    names = set(_module_level_metrics()) | {
        name for name, value in vars(prometheus_metrics).items() if isinstance(value, prometheus_metrics.Info)
    }
    return sorted(names - _metric_reads(pathlib.Path(prometheus_metrics.__file__), names))


def test_every_registered_metric_has_something_that_writes_to_it() -> None:
    assert _metrics_nothing_ever_writes_to() == [], (
        "registered but never written to, so /metrics carries a TYPE header and no sample: "
        + ", ".join(_metrics_nothing_ever_writes_to())
        + " -- emit it, or delete it and whatever promises it"
    )


_TOY_METRICS = {"HELPED", "ALIASED", "IMPORTED", "REGISTERED_ONLY", "SHADOWED"}


@pytest.fixture
def toy_metrics_file(tmp_path: pathlib.Path) -> pathlib.Path:
    """A package shaped like `mcp_hangar`, small enough to know the answer for."""
    package = tmp_path / "toy"
    (package / "sub").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "sub" / "__init__.py").write_text("")
    (package / "metrics.py").write_text(
        "HELPED = Counter()\n"
        "ALIASED = Counter()\n"
        "IMPORTED = Counter()\n"
        "REGISTERED_ONLY = Counter()\n"
        "SHADOWED = Counter()\n"
        "\n"
        "def record_helped():\n"
        "    HELPED.inc()\n"
        "\n"
        "def _register_all_metrics():\n"
        "    return [HELPED, ALIASED, IMPORTED, REGISTERED_ONLY, SHADOWED]\n"
    )
    (package / "sub" / "user.py").write_text(
        "from .. import metrics as m\n"
        "from ..metrics import IMPORTED as renamed\n"
        "\n"
        "class Names:\n"
        '    SHADOWED = "toy_shadowed_total"\n'
        "\n"
        "def emit():\n"
        "    m.ALIASED.inc()\n"
        "    renamed.inc()\n"
    )
    return package / "metrics.py"


def test_the_sweep_counts_a_helper_an_alias_and_an_import(toy_metrics_file: pathlib.Path) -> None:
    """Each way `src/` actually writes a metric, so tightening the sweep cannot drop one."""
    assert _metric_reads(toy_metrics_file, _TOY_METRICS) == {"HELPED", "ALIASED", "IMPORTED"}


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("REGISTERED_ONLY", id="the registration list"),
        pytest.param("SHADOWED", id="a same-named constant elsewhere"),
    ],
)
def test_the_sweep_is_not_fooled_by(toy_metrics_file: pathlib.Path, name: str) -> None:
    """The two holes that together hid four dead metrics (#1259); either alone kept the guard green."""
    assert name not in _metric_reads(toy_metrics_file, _TOY_METRICS)
