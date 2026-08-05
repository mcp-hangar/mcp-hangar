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

SRC = pathlib.Path(__file__).resolve().parents[2] / "src"

#: Events a handler listens for that nothing emits yet, with the reason.
#:
#: These are not mistakes to delete: the handlers are real, tested code for a
#: capability that is deliberately not shipped. What was a mistake is that
#: nothing said so, so a reader of `bootstrap/event_handlers.py` saw an
#: enforcement path and had no way to learn it can never fire.
RESERVED_WITHOUT_PRODUCER = {
    "DetectionRuleMatched": "anomaly detection is deliberately unshipped; the enforcement handler is built and waiting",
    "BehavioralDeviationDetected": "same feature: risk scoring consumes it, nothing produces it yet",
    "EgressBlocked": "the metrics handler counts it; L7 egress enforcement emits the observed variant instead",
    "TaskInputRequired": "the synchronous mid-flight consent flow that emitted it was removed with SEP-2663",
}

#: Events with neither a producer nor a consumer. Public API, so removing them
#: is a release decision rather than a cleanup -- but nothing can have received
#: one, because nothing has ever emitted one.
UNUSED_EVENTS = {
    "BehavioralModeChanged",
    "CapabilityDeclarationMissing",
    "CatalogItemApproved",
    "CatalogItemDeprecated",
    "CatalogItemPublished",
    "CatalogItemRejected",
    "DiscoveryCycleCompleted",
    "DiscoverySourceHealthChanged",
    "McpServerApproved",
    "McpServerCapabilityQuarantineReleased",
    "McpServerCapabilityQuarantined",
    "McpServerDiscovered",
    "McpServerDiscoveryConfigChanged",
    "McpServerDiscoveryLost",
    "McpServerQuarantined",
    "PolicyPushRejected",
    "ToolSchemaChanged",
    "ToolSchemaDriftDetected",
}


def _name_of(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


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

    # The deprecated `Provider*` classes subclass their `McpServer*`
    # counterparts. They are compatibility surface that nothing is meant to
    # emit -- counting them as unused vocabulary would be counting the
    # deprecation itself as debt.
    aliases = {cls for cls, parents in bases.items() if cls in events and any(p in events for p in parents)}

    produced: set[str] = set()
    consumed: set[str] = set()
    for _path, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                called = _name_of(node.func)
                # No need to exclude the defining module: `class X(Y):` is a
                # ClassDef, not a Call. Excluding it by file was wrong and hid
                # every `Group*` event, which the group aggregate defines and
                # emits in the same module.
                if called in events:
                    produced.add(called)
                if called in {"subscribe", "publish_hook"}:
                    for arg in node.args:
                        if (arg_name := _name_of(arg)) in events:
                            consumed.add(arg_name)
                if called == "isinstance" and len(node.args) > 1:
                    targets = node.args[1].elts if isinstance(node.args[1], ast.Tuple | ast.List) else [node.args[1]]
                    consumed.update(n for t in targets if (n := _name_of(t)) in events)
            # Dispatch tables keyed on the event class -- how the metrics,
            # security and replay handlers select their branch.
            if isinstance(node, ast.Dict):
                consumed.update(n for k in node.keys if k is not None and (n := _name_of(k)) in events)
            # `A | B` in an isinstance or a match, as the logging handler writes it.
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
                for side in (node.left, node.right):
                    if (n := _name_of(side)) in events:
                        consumed.add(n)
    return events - aliases, produced, consumed


def test_every_consumed_event_has_a_producer_or_a_declared_reason() -> None:
    events, produced, consumed = _scan()
    orphaned = {e for e in consumed & events if e not in produced}

    undeclared = orphaned - set(RESERVED_WITHOUT_PRODUCER)
    assert not undeclared, (
        f"{sorted(undeclared)} have a handler and no emitter. Either emit them, or add them to "
        "RESERVED_WITHOUT_PRODUCER with the reason -- a handler waiting on an event nothing "
        "sends is invisible in every other check."
    )

    stale = set(RESERVED_WITHOUT_PRODUCER) & produced
    assert not stale, (
        f"{sorted(stale)} are declared as having no producer, but something now emits them. "
        "Remove them from RESERVED_WITHOUT_PRODUCER: the list is a statement about today."
    )


def test_the_unused_event_list_only_shrinks() -> None:
    events, produced, consumed = _scan()
    unused = {e for e in events if e not in produced and e not in consumed}

    new = unused - UNUSED_EVENTS
    assert not new, (
        f"{sorted(new)} are new event classes that nothing emits and nothing handles. "
        "Vocabulary for a feature that does not exist is how the previous 22 accumulated."
    )

    revived = UNUSED_EVENTS - unused
    assert not revived, (
        f"{sorted(revived)} are no longer unused -- remove them from UNUSED_EVENTS so the "
        "baseline keeps meaning what it says."
    )


def test_the_reserved_list_names_only_real_events() -> None:
    events, _, _ = _scan()
    unknown = (set(RESERVED_WITHOUT_PRODUCER) | UNUSED_EVENTS) - events
    assert not unknown, f"{sorted(unknown)} are not event classes; a renamed or deleted entry left a stale name here"
