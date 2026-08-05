"""Every event with a handler has something that emits it, or says why not.

Two gaps went unnoticed for months because nothing looked for either.

`DetectionEnforcementHandler` -- which suspends sessions and stops servers -- is
subscribed to `DetectionRuleMatched` in bootstrap. Nothing constructs that event
anywhere in `src/`. The enforcement path is wired end to end except for the one
step that would ever start it, and no test, type check or lint could see that:
each half is correct on its own.

In the other direction, 22 event classes have no producer at all while being
exported as public API, which is how a codebase accumulates vocabulary for
features nobody built.

Both facts are now declared and ratcheted. The lists may shrink -- by emitting
the event or by deleting the class -- and may not grow.
"""

from __future__ import annotations

import ast
import pathlib
import tomllib

SRC = pathlib.Path(__file__).resolve().parents[2] / "src"


def _baselines() -> tuple[dict[str, str], set[str]]:
    """The two lists, read from pyproject rather than written here as literals.

    The dead-symbol scanner counts a name appearing in Python source as a
    reference. An inventory of dead vocabulary written as string literals would
    therefore mark every symbol it inventories as used -- this gate would have
    hidden exactly what the other one exists to find. TOML is inert to it, and
    `[tool.dead_symbols]` already sets the precedent for baselines living here.
    """
    data = tomllib.loads((SRC.parent / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["event_contracts"]
    reserved = dict(entry.split(":", 1) for entry in data["reserved_without_producer"])
    return reserved, set(data["unused_events"])


def _name_of(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _event_classes(trees: dict) -> tuple[set[str], set[str]]:
    """(every DomainEvent subclass, those that are deprecated aliases).

    An alias subclasses another event rather than DomainEvent directly. It is
    compatibility surface nothing is meant to emit, so counting it as unused
    vocabulary would count the deprecation itself as debt.
    """
    bases: dict[str, list[str]] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases[node.name] = [b for b in (_name_of(base) for base in node.bases) if b]

    events: set[str] = set()
    changed = True
    while changed:
        changed = False
        for cls, parents in bases.items():
            if cls not in events and any(p == "DomainEvent" or p in events for p in parents):
                events.add(cls)
                changed = True

    aliases = {cls for cls, parents in bases.items() if cls in events and any(p in events for p in parents)}
    return events, aliases


def _consumed_in(node: ast.AST, events: set[str]) -> set[str]:
    """Event classes this node listens for: subscribe arg, dict key, isinstance target."""
    found: set[str] = set()
    if isinstance(node, ast.Call):
        called = _name_of(node.func)
        if called in {"subscribe", "publish_hook"}:
            found.update(n for a in node.args if (n := _name_of(a)) in events)
        elif called == "isinstance" and len(node.args) > 1:
            targets = node.args[1].elts if isinstance(node.args[1], ast.Tuple | ast.List) else [node.args[1]]
            found.update(n for t in targets if (n := _name_of(t)) in events)
    elif isinstance(node, ast.Dict):
        found.update(n for k in node.keys if k is not None and (n := _name_of(k)) in events)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        found.update(n for side in (node.left, node.right) if (n := _name_of(side)) in events)
    return found


def _scan() -> tuple[set[str], set[str], set[str]]:
    """(all event classes, those constructed, those a handler listens for).

    Reads the AST rather than grepping: the legacy `Provider*` aliases are
    spelled through `"".join((...))` on purpose, so a text search reports zero
    usage on code that is very much alive.
    """
    trees = {}
    for path in SRC.rglob("*.py"):
        try:
            trees[path] = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a parse failure is a bigger problem
            continue

    events, aliases = _event_classes(trees)

    produced: set[str] = set()
    consumed: set[str] = set()
    for _path, tree in trees.items():
        for node in ast.walk(tree):
            # `class X(Y):` is a ClassDef, not a Call, so a construction found
            # anywhere is a real emit. An earlier version excluded the defining
            # module and thereby hid every `Group*` event, which the group
            # aggregate defines and emits in one file.
            if isinstance(node, ast.Call) and (called := _name_of(node.func)) in events:
                produced.add(called)
            consumed.update(_consumed_in(node, events))
    return events - aliases, produced, consumed


def test_every_consumed_event_has_a_producer_or_a_declared_reason() -> None:
    reserved, unused_baseline = _baselines()
    events, produced, consumed = _scan()
    orphaned = {e for e in consumed & events if e not in produced}

    undeclared = orphaned - set(reserved)
    assert not undeclared, (
        f"{sorted(undeclared)} have a handler and no emitter. Either emit them, or add them to "
        "RESERVED_WITHOUT_PRODUCER with the reason -- a handler waiting on an event nothing "
        "sends is invisible in every other check."
    )

    stale = set(reserved) & produced
    assert not stale, (
        f"{sorted(stale)} are declared as having no producer, but something now emits them. "
        "Remove them from RESERVED_WITHOUT_PRODUCER: the list is a statement about today."
    )


def test_the_unused_event_list_only_shrinks() -> None:
    reserved, unused_baseline = _baselines()
    events, produced, consumed = _scan()
    unused = {e for e in events if e not in produced and e not in consumed}

    new = unused - unused_baseline
    assert not new, (
        f"{sorted(new)} are new event classes that nothing emits and nothing handles. "
        "Vocabulary for a feature that does not exist is how the previous 22 accumulated."
    )

    revived = unused_baseline - unused
    assert not revived, (
        f"{sorted(revived)} are no longer unused -- remove them from unused_baseline so the "
        "baseline keeps meaning what it says."
    )


def test_the_reserved_list_names_only_real_events() -> None:
    reserved, unused_baseline = _baselines()
    events, _, _ = _scan()
    unknown = (set(reserved) | unused_baseline) - events
    assert not unknown, f"{sorted(unknown)} are not event classes; a renamed or deleted entry left a stale name here"
