"""A strict evaluator for the PromQL the observability guide documents (#1369).

#1059: five metrics shipped with ready-made PromQL in the guide and returned
nothing on every deployment for seven versions. A query nobody runs drifts from
the exposition without a sound, so a documented query is run here, against two
scrapes of a running gateway.

There is no Prometheus in CI. This evaluates the subset the guide uses and
refuses everything else with :class:`PromQLError`. It is strict where drift
hides: a selector that matches no series is an error, where Prometheus would
draw an empty panel.

Supported:

* vector selectors with ``=`` and ``!=`` matchers, and range selectors (``[5m]``);
* ``rate`` and ``increase`` of a range selector;
* ``sum``, ``max`` and ``min``, with ``by (...)`` before or after the argument;
* ``histogram_quantile(q, ...)``;
* ``+ - * /`` between two vectors (one-to-one, on identical label sets) or a
  vector and a scalar;
* ``> < >= <= == !=`` of a vector against a scalar, as a filter.

Where it differs from Prometheus, and what a test may therefore not rely on:

* A range covers exactly the two scrapes, and must be at least as long as the
  gap between them. ``increase`` is the difference of the two samples and
  ``rate`` that over the gap: nothing is extrapolated to the window's edges and
  counter resets are refused. A test asserts what extrapolation cannot change:
  a sign, a ratio of two rates taken from the same scrapes, a bucket.
* A series present in only one scrape has no rate, as in Prometheus, which needs
  two samples.
* Results carry no ``__name__``.

The scrapes are parsed with ``prometheus_client``'s parser, so an exposition the
real parser rejects fails here first.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import math
import re

from prometheus_client.parser import text_string_to_metric_families

Labels = frozenset[tuple[str, str]]
Vector = dict[Labels, float]
Value = Vector | float


class PromQLError(Exception):
    """The query is outside the supported subset, or it no longer matches the exposition."""


@dataclass(frozen=True)
class Scrape:
    """One scrape: when it was taken, and every sample by its sample name."""

    at: float
    samples: Mapping[str, Vector]


def parse_scrape(text: str, at: float) -> Scrape:
    """Parse a ``/metrics`` body taken at *at* (Unix seconds)."""
    samples: dict[str, Vector] = {}
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            samples.setdefault(sample.name, {})[frozenset(sample.labels.items())] = float(sample.value)
    return Scrape(at=at, samples=samples)


# --- Tokens -------------------------------------------------------------------

_TOKENS = re.compile(
    r"""
    (?P<space>\s+)
  | (?P<duration>\d+[smhdwy](?![A-Za-z0-9_]))
  | (?P<number>\d+(?:\.\d+)?)
  | (?P<ident>[A-Za-z_:][A-Za-z0-9_:]*)
  | (?P<string>"(?:[^"\\]|\\.)*")
  | (?P<op>>=|<=|==|!=|=~|!~|[-+*/><=(){}\[\],])
    """,
    re.VERBOSE,
)

_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}
_AGGREGATIONS: dict[str, Callable[[list[float]], float]] = {"sum": sum, "max": max, "min": min}
_ARITHMETIC: dict[str, Callable[[float, float], float]] = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": lambda a, b: a / b if b else (math.nan if a == 0 else math.copysign(math.inf, a)),
}
_COMPARISONS: dict[str, Callable[[float, float], bool]] = {
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def _tokenize(query: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(query):
        match = _TOKENS.match(query, position)
        if match is None:
            raise PromQLError(f"unsupported syntax at {query[position:]!r}")
        position = match.end()
        kind = match.lastgroup or ""
        if kind != "space":
            tokens.append((kind, match.group()))
    return tokens


# --- Evaluation ----------------------------------------------------------------


@dataclass(frozen=True)
class _Range:
    """A range selector's samples: the earlier scrape, the later one, and the gap."""

    before: Vector
    after: Vector
    seconds: float


class _Evaluator:
    """Recursive descent over the tokens, evaluating as it goes."""

    def __init__(self, query: str, before: Scrape, after: Scrape) -> None:
        if after.at <= before.at:
            raise PromQLError("the second scrape must be taken after the first")
        self._tokens = _tokenize(query)
        self._position = 0
        self._before = before
        self._after = after

    # Token helpers.

    def _peek(self) -> tuple[str, str] | None:
        return self._tokens[self._position] if self._position < len(self._tokens) else None

    def _take(self, text: str | None = None, kind: str | None = None) -> str:
        token = self._peek()
        if token is None or (text is not None and token[1] != text) or (kind is not None and token[0] != kind):
            raise PromQLError(f"expected {text or kind!r}, got {token[1] if token else 'end of query'!r}")
        self._position += 1
        return token[1]

    def _at(self, text: str) -> bool:
        token = self._peek()
        return token is not None and token[1] == text

    # Grammar, lowest precedence first.

    def run(self) -> Value:
        value = self._comparison()
        if self._peek() is not None:
            raise PromQLError(f"unsupported syntax at {self._peek()!r}")
        return value

    def _comparison(self) -> Value:
        left = self._additive()
        token = self._peek()
        if token is None or token[1] not in _COMPARISONS:
            return left
        operator = self._take()
        right = self._additive()
        if not isinstance(left, dict) or isinstance(right, dict):
            raise PromQLError("only a vector compared with a scalar is supported")
        compare = _COMPARISONS[operator]
        return {labels: value for labels, value in left.items() if compare(value, right)}

    def _additive(self) -> Value:
        value = self._multiplicative()
        while self._at("+") or self._at("-"):
            operator = self._take()
            value = _combine(value, self._multiplicative(), operator)
        return value

    def _multiplicative(self) -> Value:
        value = self._primary()
        while self._at("*") or self._at("/"):
            operator = self._take()
            value = _combine(value, self._primary(), operator)
        return value

    def _primary(self) -> Value:
        token = self._peek()
        if token is None:
            raise PromQLError("unexpected end of query")
        kind, text = token
        if kind == "number":
            self._take()
            return float(text)
        if text == "(":
            self._take("(")
            value = self._comparison()
            self._take(")")
            return value
        if kind != "ident":
            raise PromQLError(f"unsupported syntax at {text!r}")
        if text in _AGGREGATIONS:
            return self._aggregation()
        if text in ("rate", "increase"):
            return self._range_function()
        if text == "histogram_quantile":
            return self._histogram_quantile()
        if self._position + 1 < len(self._tokens) and self._tokens[self._position + 1][1] == "(":
            raise PromQLError(f"unsupported function or keyword {text!r}")
        selected = self._selector()
        if isinstance(selected, _Range):
            raise PromQLError("a range selector is only supported inside rate() or increase()")
        return selected

    def _labels_list(self) -> tuple[str, ...]:
        self._take("(")
        names: list[str] = []
        while not self._at(")"):
            names.append(self._take(kind="ident"))
            if not self._at(")"):
                self._take(",")
        self._take(")")
        return tuple(names)

    def _aggregation(self) -> Vector:
        aggregate = _AGGREGATIONS[self._take()]
        by: tuple[str, ...] | None = None
        if self._at("by"):
            self._take("by")
            by = self._labels_list()
        self._take("(")
        inner = self._comparison()
        self._take(")")
        if self._at("by"):
            if by is not None:
                raise PromQLError("`by` given twice")
            self._take("by")
            by = self._labels_list()
        if not isinstance(inner, dict):
            raise PromQLError("an aggregation needs a vector")
        groups: dict[Labels, list[float]] = {}
        for labels, value in inner.items():
            key = frozenset((name, v) for name, v in labels if name in (by or ()))
            groups.setdefault(key, []).append(value)
        return {key: aggregate(values) for key, values in groups.items()}

    def _range_function(self) -> Vector:
        function = self._take()
        self._take("(")
        selected = self._selector()
        self._take(")")
        if not isinstance(selected, _Range):
            raise PromQLError(f"{function}() needs a range selector")
        result: Vector = {}
        for labels, now in selected.after.items():
            if labels not in selected.before:
                continue  # one sample in the window: no rate, as in Prometheus
            delta = now - selected.before[labels]
            if delta < 0:
                raise PromQLError("a counter reset is not supported")
            result[labels] = delta / selected.seconds if function == "rate" else delta
        return result

    def _histogram_quantile(self) -> Vector:
        self._take("histogram_quantile")
        self._take("(")
        quantile = float(self._take(kind="number"))
        self._take(",")
        buckets = self._comparison()
        self._take(")")
        if not isinstance(buckets, dict):
            raise PromQLError("histogram_quantile() needs a vector of buckets")
        groups: dict[Labels, list[tuple[float, float]]] = {}
        for labels, count in buckets.items():
            bounds = dict(labels)
            if "le" not in bounds:
                raise PromQLError("histogram_quantile() needs an `le` label on every series")
            key = frozenset(item for item in labels if item[0] != "le")
            groups.setdefault(key, []).append((float(bounds["le"]), count))
        return {key: _bucket_quantile(quantile, sorted(series)) for key, series in groups.items()}

    def _selector(self) -> Vector | _Range:
        name = self._take(kind="ident")
        matchers: list[tuple[str, str, str]] = []
        if self._at("{"):
            self._take("{")
            while not self._at("}"):
                label = self._take(kind="ident")
                operator = self._take()
                if operator not in ("=", "!="):
                    raise PromQLError(f"label matcher {operator!r} is not supported")
                matchers.append((label, operator, _unquote(self._take(kind="string"))))
                if not self._at("}"):
                    self._take(",")
            self._take("}")
        after = _select(self._after, name, matchers)
        if not after:
            raise PromQLError(
                f"{name}{_render(matchers)} matches no series in the scrape: "
                "the documented query has drifted from the exposition"
            )
        if not self._at("["):
            return after
        self._take("[")
        duration = self._take(kind="duration")
        self._take("]")
        gap = self._after.at - self._before.at
        if int(duration[:-1]) * _SECONDS[duration[-1]] < gap:
            raise PromQLError(f"[{duration}] is shorter than the {gap:.0f}s between the two scrapes")
        return _Range(before=_select(self._before, name, matchers), after=after, seconds=gap)


def _unquote(text: str) -> str:
    return re.sub(r"\\(.)", lambda escaped: {"n": "\n"}.get(escaped.group(1), escaped.group(1)), text[1:-1])


def _render(matchers: list[tuple[str, str, str]]) -> str:
    return (
        "{" + ",".join(f'{label}{operator}"{value}"' for label, operator, value in matchers) + "}" if matchers else ""
    )


def _select(scrape: Scrape, name: str, matchers: list[tuple[str, str, str]]) -> Vector:
    selected: Vector = {}
    for labels, value in scrape.samples.get(name, {}).items():
        present = dict(labels)
        if all((present.get(label, "") == expected) == (operator == "=") for label, operator, expected in matchers):
            selected[labels] = value
    return selected


def _combine(left: Value, right: Value, operator: str) -> Value:
    apply = _ARITHMETIC[operator]
    if isinstance(left, dict) and isinstance(right, dict):
        combined = {labels: apply(value, right[labels]) for labels, value in left.items() if labels in right}
        if left and right and not combined:
            raise PromQLError(f"no label set on the left of {operator!r} matches one on the right")
        return combined
    if isinstance(left, dict):
        return {labels: apply(value, float(right)) for labels, value in left.items()}  # type: ignore[arg-type]
    if isinstance(right, dict):
        return {labels: apply(left, value) for labels, value in right.items()}
    return apply(left, right)


def _bucket_quantile(quantile: float, buckets: list[tuple[float, float]]) -> float:
    """Prometheus's ``bucketQuantile``: linear interpolation inside the bucket the rank falls in."""
    if quantile < 0:
        return -math.inf
    if quantile > 1:
        return math.inf
    if len(buckets) < 2 or not math.isinf(buckets[-1][0]):
        return math.nan
    observations = buckets[-1][1]
    if observations == 0:
        return math.nan
    rank = quantile * observations
    index = next(i for i, (_, count) in enumerate(buckets) if count >= rank)
    if index == len(buckets) - 1:
        return buckets[-2][0]
    upper, count = buckets[index]
    if index == 0 and upper <= 0:
        return upper
    lower = 0.0
    if index > 0:
        lower, below = buckets[index - 1]
        count -= below
        rank -= below
    return lower + (upper - lower) * (rank / count)


def evaluate(query: str, before: Scrape, after: Scrape) -> Value:
    """Evaluate *query* at the time of *after*, with ranges spanning back to *before*."""
    return _Evaluator(query, before, after).run()


# --- What the guide documents ------------------------------------------------------
#
# Copied verbatim into `guides/OBSERVABILITY.md` in mcp-hangar/docs. Change a query
# here and there together: the test that runs these is the only thing keeping the
# guide honest about the exposition (#1059).

PROJECTION_QUERIES: dict[str, str] = {
    # Did the projection served to any caller change on this replica in the last hour?
    "changed": "increase(mcp_hangar_projection_changes_total[1h]) > 0",
    # Tools a client is handed per listing, by kind.
    "tools_per_listing": (
        "sum(rate(mcp_hangar_projected_tools_sum[5m])) by (kind)"
        " / sum(rate(mcp_hangar_projected_tools_count[5m])) by (kind)"
    ),
    # Bytes of tool definitions a client is handed per listing, by kind.
    "bytes_per_listing": (
        "sum(rate(mcp_hangar_projected_surface_bytes_sum[5m])) by (kind)"
        " / sum(rate(mcp_hangar_projected_surface_bytes_count[5m])) by (kind)"
    ),
    # 95th percentile of the governed surface one listing carries.
    "governed_bytes_p95": (
        'histogram_quantile(0.95, sum(rate(mcp_hangar_projected_surface_bytes_bucket{kind="governed"}[5m])) by (le))'
    ),
    # What the surface is made of: bytes each upstream adds to a listing that includes it.
    "bytes_per_upstream": (
        "sum(rate(mcp_hangar_projected_upstream_bytes_sum[5m])) by (mcp_server)"
        " / sum(rate(mcp_hangar_projected_upstream_bytes_count[5m])) by (mcp_server)"
    ),
}
