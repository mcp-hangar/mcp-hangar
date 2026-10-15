"""`http.retry_status_codes` reaches the client (#1684).

The launcher built `HttpClientConfig` from `http:` without it, so the client kept
its default (502, 503, 504) and a configured 500 was never retried.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from mcp_hangar.domain.exceptions import ValidationError
from mcp_hangar.infrastructure.launchers import HttpLauncher


def _codes(http: dict | None) -> tuple[int, ...]:
    with patch("mcp_hangar.http_client.HttpClient") as client:
        HttpLauncher().launch(endpoint="https://mcp.example.com", http_config=http)
    return client.call_args.kwargs["http_config"].retry_status_codes


def test_a_configured_code_is_retried():
    assert _codes({"retry_status_codes": [500, 503]}) == (500, 503)


def test_an_unconfigured_code_is_not():
    assert 500 not in _codes({"max_retries": 2})
    assert 500 not in _codes(None)


def test_the_default_stays_when_nothing_is_configured():
    assert _codes({"max_retries": 2}) == (502, 503, 504)


@pytest.mark.parametrize("bad", ["500", [99], [600], ["500"], [True], 500])
def test_a_value_that_is_not_a_list_of_status_codes_is_refused(bad):
    with pytest.raises(ValidationError, match="retry_status_codes"):
        _codes({"retry_status_codes": bad})
