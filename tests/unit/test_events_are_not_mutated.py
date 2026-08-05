"""No handler edits the event it was handed.

`publish` gives every handler the *same* instance, in sequence, on one thread.
A handler that assigns to a field therefore changes what the handlers after it
see -- and, now that the same instance is also appended to a stream, what gets
persisted. Nothing enforces this: `DomainEvent` is a plain `@dataclass`.

The obvious fix is `frozen=True`, and it is not free. Python refuses a
non-frozen dataclass inheriting from a frozen one, so freezing the base freezes
**every** subclass -- 85 decorators in `domain/events/` alone, plus events
defined elsewhere, plus any downstream that subclasses one with a plain
`@dataclass`. That is a wide, breaking change to defend against a mutation that
does not currently happen.

So the invariant is tested instead of enforced by the type. This costs nobody
anything and fails the moment someone writes the line that would matter. If the
count here ever stops being zero for a legitimate reason, that is the argument
for paying the `frozen` price -- and this test is where the discussion starts.
"""

from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[2] / "src"

#: Parameter names that hold a domain event by convention in this codebase.
EVENT_PARAMS = {"event", "evt", "domain_event", "stored_event"}


def _mutations_of_event_parameters() -> list[str]:
    """Every `event.field = ...` inside a function that takes an event.

    Deliberately narrow: it matches the shape a handler would actually be
    written in. A mutation laundered through `setattr(e, name, v)` with a
    computed name would slip past, and pretending otherwise would be worse than
    admitting it -- this catches the line someone writes by habit, not an
    adversary.
    """
    found: list[str] = []
    for path in SRC.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            params = {a.arg for a in func.args.args} | {a.arg for a in func.args.kwonlyargs}
            held = params & EVENT_PARAMS
            if not held:
                continue
            for node in ast.walk(func):
                targets: list[ast.expr] = []
                if isinstance(node, ast.Assign):
                    targets = list(node.targets)
                elif isinstance(node, ast.AugAssign):
                    targets = [node.target]
                for target in targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id in held
                    ):
                        rel = path.relative_to(SRC.parent)
                        found.append(f"{rel}:{node.lineno} in {func.name}(): {target.value.id}.{target.attr} = ...")
    return sorted(found)


def test_no_handler_mutates_the_event_it_receives() -> None:
    mutations = _mutations_of_event_parameters()
    assert mutations == [], (
        "An event is handed to every handler as the same instance, and is now also "
        "appended to a stream. Mutating one changes what later handlers see and what "
        "gets persisted:\n  " + "\n  ".join(mutations)
    )


def test_the_scan_can_actually_see_a_mutation() -> None:
    """A gate that cannot fail is not a gate.

    Pins the detection itself against a synthetic handler, so a refactor that
    breaks the scan shows up here rather than as a permanently green check.
    """
    source = """
def handle(event):
    event.tool_name = "rewritten"
"""
    tree = ast.parse(source)
    func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
    params = {a.arg for a in func.args.args} & EVENT_PARAMS
    assert params == {"event"}

    mutated = [
        node
        for node in ast.walk(func)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Attribute)
        and isinstance(node.targets[0].value, ast.Name)
        and node.targets[0].value.id in params
    ]
    assert len(mutated) == 1
