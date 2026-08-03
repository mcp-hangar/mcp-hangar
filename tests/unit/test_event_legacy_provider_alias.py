"""The `provider_id` → `mcp_server_id` alias, pinned across every event that has it.

23 event classes carry a hand-written `__init__` whose only job is to accept the
pre-rename `provider_id` keyword, reject unknown keywords, and assign the
fields. That is roughly 200 lines of identical code, and before this file the
behaviour it implements was exercised by **three** assertions in the whole
suite, none of which touched an event class.

These are characterization tests: they describe what the code does today, so the
boilerplate can be replaced by one mechanism without changing behaviour. Written
before the refactor deliberately -- a refactor whose safety net is written
afterwards proves only that the new code agrees with itself.

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
        # Two shapes carry the alias: a hand-written __init__ that names
        # provider_id, and the decorator, whose wrapper is not introspectable --
        # functools.wraps makes inspect.signature report the dataclass
        # constructor instead. The decorator sets a marker for exactly this.
        if (
            getattr(obj, "__accepts_legacy_provider_id__", False)
            or "provider_id" in params
            or (takes_kwargs and "mcp_server_id" in params)
        ):
            found.append(obj)
    return sorted(found, key=lambda c: c.__name__)


ALIAS_CLASSES = _alias_classes()


def _minimal_kwargs(cls: type[DomainEvent], id_keyword: str) -> dict:
    """Build the smallest constructor payload, keying the server id as asked."""
    kwargs = {id_keyword: "srv-1"}
    for field in dataclasses.fields(cls) if dataclasses.is_dataclass(cls) else []:
        if field.name in ("mcp_server_id", "event_id", "occurred_at", "schema_version"):
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
