"""The evaluator that runs the guide's PromQL computes what Prometheus does, and refuses the rest (#1369).

`tests/_promql.py` is what certifies the documented queries against a scrape of
a running gateway. A certifier that answered a query it did not understand, or
answered an empty selector with an empty result, would pass a guide that had
drifted, which is the failure #1059 found. These pin both halves.
"""

from __future__ import annotations

import math

import pytest

from tests._promql import PromQLError, Scrape, evaluate, parse_scrape

_BEFORE = """\
# HELP demo_requests_total Requests
# TYPE demo_requests_total counter
demo_requests_total{route="a"} 10.0
demo_requests_total{route="b"} 4.0
# HELP demo_size Sizes
# TYPE demo_size histogram
demo_size_bucket{kind="x",le="0"} 0
demo_size_bucket{kind="x",le="10"} 1
demo_size_bucket{kind="x",le="100"} 1
demo_size_bucket{kind="x",le="+Inf"} 1
demo_size_sum{kind="x"} 5.0
demo_size_count{kind="x"} 1
"""

_AFTER = """\
# HELP demo_requests_total Requests
# TYPE demo_requests_total counter
demo_requests_total{route="a"} 30.0
demo_requests_total{route="b"} 4.0
demo_requests_total{route="c"} 1.0
# HELP demo_size Sizes
# TYPE demo_size histogram
demo_size_bucket{kind="x",le="0"} 0
demo_size_bucket{kind="x",le="10"} 1
demo_size_bucket{kind="x",le="100"} 5
demo_size_bucket{kind="x",le="+Inf"} 5
demo_size_sum{kind="x"} 205.0
demo_size_count{kind="x"} 5
"""


def _run(query: str) -> object:
    return evaluate(query, parse_scrape(_BEFORE, at=0.0), parse_scrape(_AFTER, at=10.0))


def _series(**labels: str) -> frozenset[tuple[str, str]]:
    return frozenset(labels.items())


class TestWhatItComputes:
    def test_increase_is_the_difference_and_a_series_seen_once_has_none(self) -> None:
        assert _run("increase(demo_requests_total[1m])") == {_series(route="a"): 20.0, _series(route="b"): 0.0}

    def test_rate_is_the_difference_over_the_gap(self) -> None:
        assert _run("rate(demo_requests_total[1m])") == {_series(route="a"): 2.0, _series(route="b"): 0.0}

    def test_sum_with_by_before_or_after_the_argument(self) -> None:
        assert _run("sum(rate(demo_requests_total[1m]))") == {_series(): 2.0}
        assert _run("sum(rate(demo_requests_total[1m])) by (route)") == _run(
            "sum by (route) (rate(demo_requests_total[1m]))"
        )

    def test_a_comparison_with_a_scalar_filters(self) -> None:
        assert _run("increase(demo_requests_total[1m]) > 0") == {_series(route="a"): 20.0}

    def test_a_ratio_of_two_rates_matches_on_labels(self) -> None:
        query = "sum(rate(demo_size_sum[1m])) by (kind) / sum(rate(demo_size_count[1m])) by (kind)"
        assert _run(query) == {_series(kind="x"): 50.0}

    def test_histogram_quantile_interpolates_inside_the_bucket_as_prometheus_does(self) -> None:
        # Four new observations, all in (10, 100]: the median is halfway through it.
        assert _run("histogram_quantile(0.5, sum(rate(demo_size_bucket[1m])) by (le))") == {_series(): 55.0}

    def test_a_matcher_selects(self) -> None:
        assert _run('demo_requests_total{route!="a"}') == {_series(route="b"): 4.0, _series(route="c"): 1.0}

    def test_division_by_zero_is_what_prometheus_answers(self) -> None:
        result = _run('rate(demo_requests_total{route="b"}[1m]) / 0')
        assert isinstance(result, dict) and math.isnan(next(iter(result.values())))


class TestWhatItRefuses:
    @pytest.mark.parametrize(
        ("query", "why"),
        [
            pytest.param("demo_request_total", "matches no series", id="a misspelled metric"),
            pytest.param('demo_requests_total{route="z"}', "matches no series", id="a label value that is not there"),
            pytest.param('sum(rate(demo_size_sum{kind="y"}[1m]))', "matches no series", id="an empty selector inside"),
            pytest.param("irate(demo_requests_total[1m])", "unsupported function", id="an unsupported function"),
            pytest.param("sum without (route) (demo_requests_total)", "expected", id="without"),
            pytest.param('demo_requests_total{route=~"a"}', "not supported", id="a regex matcher"),
            pytest.param("rate(demo_requests_total[5s])", "shorter than", id="a range shorter than the gap"),
            pytest.param("demo_requests_total[1m]", "only supported inside", id="a bare range selector"),
            pytest.param("demo_requests_total offset 5m", "unsupported syntax", id="offset"),
            pytest.param("rate(demo_requests_total[1m]", "expected", id="an unclosed call"),
            pytest.param("demo_requests_total > demo_requests_total", "scalar", id="vector against vector"),
        ],
    )
    def test_a_query_it_cannot_run_faithfully(self, query: str, why: str) -> None:
        with pytest.raises(PromQLError, match=why):
            _run(query)

    def test_a_counter_reset(self) -> None:
        reset = _AFTER.replace('demo_requests_total{route="a"} 30.0', 'demo_requests_total{route="a"} 1.0')
        with pytest.raises(PromQLError, match="reset"):
            evaluate("increase(demo_requests_total[1m])", parse_scrape(_BEFORE, at=0.0), parse_scrape(reset, at=10.0))

    def test_scrapes_out_of_order(self) -> None:
        with pytest.raises(PromQLError, match="after"):
            evaluate("demo_requests_total", parse_scrape(_AFTER, at=10.0), parse_scrape(_BEFORE, at=0.0))

    def test_an_exposition_the_real_parser_rejects(self) -> None:
        with pytest.raises(ValueError):
            parse_scrape('demo_requests_total{route="a" 1\n', at=0.0)

    def test_a_scrape_is_what_was_parsed(self) -> None:
        scrape = parse_scrape(_BEFORE, at=0.0)
        assert isinstance(scrape, Scrape)
        assert scrape.samples["demo_size_count"] == {_series(kind="x"): 1.0}
