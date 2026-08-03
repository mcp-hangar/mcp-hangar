"""The pre-rename keyword aliases, pinned across every event that has one.

Two renames left callers spelling arguments the old way: `provider_id` ->
`mcp_server_id` (23 classes) and `provider_name` -> `mcp_server_name` (5). Each
class used to carry a hand-written `__init__` whose only job was that
translation, plus rejecting unknown keywords and assigning the fields -- roughly
200 lines of identical code, and before this file the behaviour it implements
was exercised by **three** assertions in the whole suite, none of which touched
an event class.

These are characterization tests: they describe what the code does, so the
boilerplate could be replaced by one mechanism without changing behaviour.
Written before the refactor deliberately -- a refactor whose safety net is
written afterwards proves only that the new code agrees with itself.

Discovery is dynamic. Hard-coding the list would let a class quietly drop out of
the alias contract without a test noticing, which is the same class of gap this
file exists to close.
"""

import dataclasses
import inspect
import re

import pytest

from mcp_hangar.domain import events as events_module
from mcp_hangar.domain.events import DomainEvent


def _alias_classes() -> list[type[DomainEvent]]:
    """Event classes whose constructor accepts the legacy `provider_id`."""
    found = []
    for _, obj in vars(events_module).items():
        if not (inspect.isclass(obj) and issubclass(obj, DomainEvent) and obj is not DomainEvent):
            continue
        try:
            params = inspect.signature(obj.__init__).parameters
        except (ValueError, TypeError):  # pragma: no cover - defensive
            continue
        takes_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
        # The decorator's wrapper is not introspectable -- functools.wraps makes
        # inspect.signature report the dataclass constructor, so the legacy
        # keyword is invisible. It publishes a marker mapping for exactly this.
        # The signature checks remain for any class that has not been converted.
        if (
            "provider_id" in getattr(obj, "__legacy_kwarg_aliases__", {})
            or "provider_id" in params
            or (takes_kwargs and "mcp_server_id" in params)
        ):
            found.append(obj)
    return sorted(found, key=lambda c: c.__name__)


ALIAS_CLASSES = _alias_classes()


def _minimal_kwargs(cls: type[DomainEvent], id_keyword: str) -> dict:
    """Build the smallest constructor payload, keying the server id as asked.

    `id_keyword` may be the legacy or the modern spelling, of either the id or
    the name alias; the field it resolves to is left for the caller to set.
    """
    modern = {"provider_id": "mcp_server_id", "provider_name": "mcp_server_name"}.get(id_keyword, id_keyword)
    kwargs = {id_keyword: "srv-1"}
    for field in dataclasses.fields(cls) if dataclasses.is_dataclass(cls) else []:
        if field.name in (modern, "event_id", "occurred_at", "schema_version"):
            continue
        has_default = field.default is not dataclasses.MISSING or field.default_factory is not dataclasses.MISSING
        if has_default:
            continue
        kwargs[field.name] = _sample_for(field.type)
    return kwargs


def _sample_for(annotation) -> object:
    text = str(annotation)
    if "int" in text and "float" not in text:
        return 1
    if "float" in text:
        return 1.0
    if "bool" in text:
        return True
    if "list" in text:
        return []
    if "dict" in text:
        return {}
    return "x"


class TestTheAliasIsDiscoverable:
    def test_a_meaningful_number_of_classes_carry_it(self):
        """If this collapses, either the refactor dropped the alias or discovery broke."""
        assert len(ALIAS_CLASSES) >= 20, (
            f"only {len(ALIAS_CLASSES)} event classes accept the legacy provider_id; the alias contract has shrunk"
        )


@pytest.mark.parametrize("cls", ALIAS_CLASSES, ids=lambda c: c.__name__)
class TestLegacyAliasContract:
    def test_modern_keyword_sets_the_id(self, cls):
        event = cls(**_minimal_kwargs(cls, "mcp_server_id"))
        assert event.mcp_server_id == "srv-1"

    def test_legacy_keyword_sets_the_same_field(self, cls):
        """The whole point: an old caller passing provider_id still works."""
        event = cls(**_minimal_kwargs(cls, "provider_id"))
        assert event.mcp_server_id == "srv-1"

    def test_unknown_keyword_is_rejected(self, cls):
        """Silently swallowing a typo'd field would drop data from the audit trail."""
        kwargs = _minimal_kwargs(cls, "mcp_server_id")
        kwargs["definitely_not_a_field"] = "x"
        with pytest.raises(TypeError):
            cls(**kwargs)

    def test_missing_server_id_is_rejected(self, cls):
        kwargs = _minimal_kwargs(cls, "mcp_server_id")
        del kwargs["mcp_server_id"]
        with pytest.raises(TypeError):
            cls(**kwargs)

    def test_identity_is_populated(self, cls):
        """Every event gets an id and a timestamp regardless of which keyword built it."""
        event = cls(**_minimal_kwargs(cls, "provider_id"))
        assert event.event_id
        assert event.occurred_at > 0

    def test_to_dict_carries_the_modern_field_name(self, cls):
        """The wire form must not leak the legacy spelling."""
        payload = cls(**_minimal_kwargs(cls, "provider_id")).to_dict()
        assert payload["mcp_server_id"] == "srv-1"
        assert "provider_id" not in payload


class TestUnknownKeywordMessageNamesTheOffender:
    """A TypeError that does not say which keyword was wrong is a bad error."""

    def test_message_mentions_the_bad_keyword(self):
        from mcp_hangar.domain.events import HealthCheckPassed

        with pytest.raises(TypeError) as excinfo:
            HealthCheckPassed(mcp_server_id="s", duration_ms=1.0, bogus_field=2)
        assert re.search(r"bogus_field", str(excinfo.value))


def _name_alias_classes() -> list[type[DomainEvent]]:
    """Event classes whose constructor accepts the legacy `provider_name`."""
    found = [
        obj
        for _, obj in vars(events_module).items()
        if inspect.isclass(obj)
        and issubclass(obj, DomainEvent)
        and "provider_name" in getattr(obj, "__legacy_kwarg_aliases__", {})
    ]
    return sorted(found, key=lambda c: c.__name__)


NAME_ALIAS_CLASSES = _name_alias_classes()


class TestTheNameAliasIsDiscoverable:
    def test_the_five_discovery_aliases_carry_it(self):
        assert len(NAME_ALIAS_CLASSES) == 5, (
            f"expected the five Provider* discovery aliases to accept the legacy "
            f"provider_name; found {[c.__name__ for c in NAME_ALIAS_CLASSES]}"
        )


@pytest.mark.parametrize("cls", NAME_ALIAS_CLASSES, ids=lambda c: c.__name__)
class TestLegacyNameAliasContract:
    def test_modern_keyword_sets_the_name(self, cls):
        assert cls(**_minimal_kwargs(cls, "mcp_server_name")).mcp_server_name == "srv-1"

    def test_legacy_keyword_sets_the_same_field(self, cls):
        assert cls(**_minimal_kwargs(cls, "provider_name")).mcp_server_name == "srv-1"

    def test_to_dict_carries_the_modern_field_name(self, cls):
        payload = cls(**_minimal_kwargs(cls, "provider_name")).to_dict()
        assert payload["mcp_server_name"] == "srv-1"
        assert "provider_name" not in payload


@pytest.mark.parametrize(
    ("classes", "legacy", "modern"),
    [(ALIAS_CLASSES, "provider_id", "mcp_server_id"), (NAME_ALIAS_CLASSES, "provider_name", "mcp_server_name")],
    ids=["provider_id", "provider_name"],
)
class TestPassingBothSpellingsIsAnError:
    """Two spellings of one field with different values is a caller bug.

    The hand-written constructors disagreed here: the `*_id` family raised, the
    `*_name` family silently kept the legacy value and discarded the modern one.
    Silently discarding an argument the caller explicitly passed is how a
    tenant id ends up on the wrong audit record, so the raising behaviour is
    the one that got kept -- for both families.
    """

    def test_conflicting_values_raise_and_name_both_keys(self, classes, legacy, modern):
        assert classes, "discovery found no classes; the contract cannot be checked"
        for cls in classes:
            kwargs = _minimal_kwargs(cls, modern)
            kwargs[modern] = "modern-value"
            kwargs[legacy] = "legacy-value"
            with pytest.raises(TypeError) as excinfo:
                cls(**kwargs)
            message = str(excinfo.value)
            assert legacy in message and modern in message, f"{cls.__name__}: unhelpful message {message!r}"

    def test_identical_values_are_accepted(self, classes, legacy, modern):
        """Nothing to disambiguate, so there is nothing to complain about."""
        for cls in classes:
            kwargs = _minimal_kwargs(cls, modern)
            kwargs[modern] = "same"
            kwargs[legacy] = "same"
            assert getattr(cls(**kwargs), modern) == "same"


@pytest.mark.parametrize("cls", NAME_ALIAS_CLASSES, ids=lambda c: c.__name__)
def test_an_alias_takes_the_same_positional_arguments_as_the_class_it_aliases(cls):
    """An alias whose positional order differs from its base silently misfiles data.

    Before these five became plain subclasses they each declared their own
    `__init__` starting with `provider_name`, one slot ahead of the base's
    parameters -- so `ProviderDiscovered(a, b, c, d)` and
    `McpServerDiscovered(a, b, c, d)` assigned `b`, `c` and `d` to different
    fields. No caller passed them positionally, which is the only reason it
    never bit.
    """
    base = cls.__mro__[1]
    assert [f.name for f in dataclasses.fields(cls)] == [f.name for f in dataclasses.fields(base)], (
        f"{cls.__name__} and {base.__name__} no longer agree on field order"
    )
