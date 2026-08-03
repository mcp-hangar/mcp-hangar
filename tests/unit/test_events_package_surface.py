"""`domain.events` is a package now; its import surface must not shrink by accident.

141 places import from `mcp_hangar.domain.events`, and the event serializer looks
classes up by name. Splitting the 2197-line module into thirteen files kept every
name re-exported from `__init__`, but nothing stops a later edit from adding a
class to a submodule and forgetting the re-export -- at which point
`from mcp_hangar.domain.events import X` fails at import time for some callers and
the serializer's class map silently loses a type.

So this asserts the package's `__init__` covers every event class defined
anywhere under it, and that `__all__` matches what is actually exported. Both
failures are otherwise found by a stack trace in production rather than by CI.
"""

import ast
import inspect
import pathlib

from mcp_hangar.domain import events as events_pkg
from mcp_hangar.domain.events import DomainEvent

PKG_DIR = pathlib.Path(events_pkg.__file__).parent


def _classes_defined_in_submodules() -> set[str]:
    """Event classes defined in the package's modules, read from source."""
    found: set[str] = set()
    for path in sorted(PKG_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.ClassDef):
                found.add(node.name)
    return found


def _exported_event_classes() -> set[str]:
    return {name for name, obj in vars(events_pkg).items() if inspect.isclass(obj) and issubclass(obj, DomainEvent)}


class TestEveryEventIsReachableFromThePackage:
    def test_no_class_is_defined_but_unexported(self):
        missing = sorted(_classes_defined_in_submodules() - _exported_event_classes())
        assert missing == [], (
            "these event classes are defined in a submodule but not re-exported from "
            f"domain/events/__init__.py, so `from mcp_hangar.domain.events import X` "
            f"fails and the serializer cannot resolve them: {missing}"
        )

    def test_all_matches_what_is_exported(self):
        declared = set(events_pkg.__all__)
        actual = _exported_event_classes()
        # __all__ also carries the four legacy assignment aliases, which are not
        # classes of their own -- they are names bound to existing classes.
        alias_names = {"ProviderHotLoaded", "ProviderHotUnloaded", "ProviderLoadAttempted", "ProviderLoadFailed"}
        assert actual - declared == set(), f"exported but absent from __all__: {sorted(actual - declared)}"
        assert declared - actual - alias_names == set(), (
            f"declared in __all__ but not exported: {sorted(declared - actual - alias_names)}"
        )

    def test_the_legacy_assignment_aliases_still_resolve(self):
        """Four renames kept as plain assignments; they must still point somewhere."""
        for name in ("ProviderLoadAttempted", "ProviderHotLoaded", "ProviderLoadFailed", "ProviderHotUnloaded"):
            obj = getattr(events_pkg, name)
            assert inspect.isclass(obj) and issubclass(obj, DomainEvent), f"{name} no longer resolves"


class TestModuleOrderStaysAcyclic:
    """A submodule may only import from one defined earlier.

    The `Provider*` aliases subclass their `McpServer*` counterparts, so the
    order in `__init__` is load-bearing rather than cosmetic. A cycle would
    surface as an ImportError at first use, which is a bad place to find out.
    """

    ORDER = [
        "base",
        "lifecycle",
        "invocation",
        "tasks",
        "health",
        "discovery",
        "auth",
        "operations",
        "administration",
        "enforcement",
        "analysis",
        "approvals",
        "interceptors",
        "aliases",
    ]

    def test_no_submodule_imports_from_a_later_one(self):
        rank = {name: i for i, name in enumerate(self.ORDER)}
        offences = []
        for name in self.ORDER:
            path = PKG_DIR / f"{name}.py"
            if not path.exists():
                continue
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module in rank:
                    if rank[node.module] >= rank[name]:
                        offences.append(f"{name} -> {node.module}")
        assert offences == [], f"submodule imports break the definition order: {offences}"

    def test_the_order_list_covers_every_module(self):
        on_disk = {p.stem for p in PKG_DIR.glob("*.py") if p.name != "__init__.py"}
        assert on_disk == set(self.ORDER), (
            f"ORDER is stale: on disk but unlisted {sorted(on_disk - set(self.ORDER))}, "
            f"listed but absent {sorted(set(self.ORDER) - on_disk)}"
        )
