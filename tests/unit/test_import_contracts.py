"""The import contract may tighten. It may not be gutted.

`lint-imports` proves the layering holds *given the contract file*. It cannot
notice that the contract itself was weakened — a deleted layer line, a widened
`ignore_imports`, or a contract removed outright all leave the check green. An
earlier attempt at this gate failed exactly there: deleting a whole contract
block changed nothing, so the check reported success while verifying less.

So these tests guard the file rather than the code:

* the layers are present, in order, and the two ends (`domain` above the shared
  kernel, `server` at the top) are pinned by name
* the `ignore_imports` ledger is capped, so a new violation cannot be waved
  through by appending a line
* `exhaustive = False` is a deliberate choice about component packages, not a
  silent escape hatch, so it is asserted with its reason recorded here

Shrinking `MAX_IGNORED_IMPORTS` is the unit of progress.
"""

import configparser
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = ROOT / ".importlinter"

# Lower as edges are removed. Never raise without a reason in review.
MAX_IGNORED_IMPORTS = 11

# Bottom-up. Each entry is a layer line; `:` separates independent siblings.
EXPECTED_LAYER_ORDER = [
    "server",
    "infrastructure",
    "application",
    "domain",
    "logging_config",
]


@pytest.fixture(scope="module")
def contract() -> configparser.ConfigParser:
    assert CONTRACT.exists(), ".importlinter is missing -- the layering gate is gone"
    parser = configparser.ConfigParser()
    parser.read(CONTRACT, encoding="utf-8")
    return parser


@pytest.fixture(scope="module")
def hexagon(contract: configparser.ConfigParser) -> dict[str, str]:
    section = "importlinter:contract:hexagon"
    assert contract.has_section(section), (
        "the hexagon contract is gone from .importlinter. lint-imports would still "
        "exit 0 with nothing to check, which is why this test exists."
    )
    return dict(contract[section])


def _layer_lines(hexagon: dict[str, str]) -> list[str]:
    return [line.strip() for line in hexagon["layers"].splitlines() if line.strip()]


class TestContractIsIntact:
    def test_it_is_a_layers_contract(self, hexagon):
        assert hexagon["type"] == "layers"

    def test_all_five_layers_are_present_in_order(self, hexagon):
        lines = _layer_lines(hexagon)
        assert len(lines) == len(EXPECTED_LAYER_ORDER), (
            f"expected {len(EXPECTED_LAYER_ORDER)} layers, found {len(lines)} -- "
            f"a deleted layer line silently stops constraining that layer"
        )
        for line, expected in zip(lines, EXPECTED_LAYER_ORDER, strict=True):
            assert expected in line, f"layer line {line!r} no longer contains {expected!r}"

    def test_domain_sits_above_the_shared_kernel(self, hexagon):
        """The direction that matters: domain may use the kernel, never the reverse."""
        lines = _layer_lines(hexagon)
        assert lines.index([line for line in lines if "domain" in line][0]) < lines.index(
            [line for line in lines if "logging_config" in line][0]
        )

    def test_adapters_are_not_in_the_shared_kernel(self, hexagon):
        """Folding metrics/http_client into the kernel would legalise the leaks.

        `domain -> metrics` and `domain.contracts.launcher -> http_client` (a port
        importing its own adapter) are the edges this contract exists to keep
        visible. They stop being violations the moment those modules move below
        domain.
        """
        kernel = [line for line in _layer_lines(hexagon) if "logging_config" in line][0]
        for adapter in ("metrics", "http_client", "stdio_client", "gc", "facade"):
            assert f"mcp_hangar.{adapter}" not in kernel, (
                f"{adapter} moved into the shared kernel, which legalises the very "
                f"leaks the contract was written to surface"
            )

    def test_component_packages_are_out_of_scope_deliberately(self, hexagon):
        """auth/approvals/compliance/integrations/bootstrap carry their own internal
        layering; exhaustive = False says so rather than pretending otherwise."""
        assert hexagon.get("exhaustive", "").strip().lower() == "false"


class TestLedgerCannotGrow:
    def _entries(self, hexagon) -> list[str]:
        raw = hexagon.get("ignore_imports", "")
        return [line.strip() for line in raw.splitlines() if "->" in line]

    def test_count_is_capped(self, hexagon):
        entries = self._entries(hexagon)
        assert len(entries) <= MAX_IGNORED_IMPORTS, (
            f"{len(entries)} ignored imports, cap is {MAX_IGNORED_IMPORTS}. Fix the "
            f"import instead of appending to the ledger; raising the cap is a "
            f"reviewable decision, not a drive-by edit."
        )

    def test_cap_is_not_stale(self, hexagon):
        entries = self._entries(hexagon)
        assert len(entries) >= MAX_IGNORED_IMPORTS - 2, (
            f"only {len(entries)} ignored imports remain but the cap is still "
            f"{MAX_IGNORED_IMPORTS} -- lower it to lock the progress in"
        )

    def test_every_entry_is_a_concrete_edge(self, hexagon):
        """No wildcards. `a.* -> b.*` would exempt whole subtrees invisibly."""
        bad = [e for e in self._entries(hexagon) if "*" in e]
        assert bad == [], f"wildcard entries hide unknown edges: {bad}"

    def test_entries_are_well_formed(self, hexagon):
        pattern = re.compile(r"^mcp_hangar[\w.]* -> mcp_hangar[\w.]*$")
        malformed = [e for e in self._entries(hexagon) if not pattern.match(e)]
        assert malformed == [], f"malformed ignore_imports entries: {malformed}"

    def test_the_launcher_shims_do_not_come_back(self, hexagon):
        """Eight entries cleared when the deprecated launcher surface was deleted.

        It had warned since v1.0.2 and survived the 2.0 major, which is how a
        shim becomes permanent. Re-adding a re-export of the concrete launchers
        to `domain.services` would put the domain back on the infrastructure
        launchers -- and put back the import cycle those entries sustained.

        Scoped to `domain.services`: the aggregate's own
        `domain.model.mcp_server -> infrastructure.launchers` is a separate
        leak, still on the ledger, and not what this guards.
        """
        offenders = [
            e
            for e in self._entries(hexagon)
            if e.startswith("mcp_hangar.domain.services") and "infrastructure.launchers" in e
        ]
        assert offenders == [], f"domain.services reaches for the concrete launchers again: {offenders}"


class TestEveryPackageHasAMarker:
    """A directory of modules with no `__init__.py` is invisible to import analysis.

    `application/mcp` and `bootstrap` shipped exactly that way: the modules were
    tracked, the marker was not, so the wheel carried them inside implicit
    namespace packages. Imports resolve either way -- which is why nothing broke
    and nobody noticed -- but grimp walks the package tree and skips a directory
    with no marker, so those modules were outside the contract entirely.

    This caught it: `lint-imports` in CI rejected an `ignore_imports` entry that
    matched nothing, because the edge it named lived in a module CI could not
    see.
    """

    def test_no_module_directory_lacks_an_init(self):
        src = ROOT / "src" / "mcp_hangar"
        missing = [
            str(d.relative_to(src))
            for d in sorted(src.rglob("*"))
            if d.is_dir()
            and d.name != "__pycache__"
            and any(f.suffix == ".py" and f.name != "__init__.py" for f in d.iterdir())
            and not (d / "__init__.py").exists()
        ]
        assert missing == [], (
            "these directories hold modules but no __init__.py, so import analysis "
            f"skips them and the wheel ships a namespace package: {missing}"
        )
