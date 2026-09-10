"""Suite-wide test configuration.

``otel_sdk`` marks a test that needs the OpenTelemetry SDK (the ``opentelemetry``
extra), not just the API that ``mcp`` pulls in. Under CI a missing SDK fails the
test: the core jobs install the extra, so its absence is a broken install, and a
skip there would report a tracing contract as green without running it. Locally
it skips with the install hint, so a ``.[dev]`` checkout still runs the suite.
"""

import os

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "otel_sdk: needs the OpenTelemetry SDK; fails without it under CI")


@pytest.hookimpl(tryfirst=True)  # before fixture setup, which may import the SDK
def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker("otel_sdk") is None:
        return
    try:
        import opentelemetry.sdk.trace  # noqa: F401
    except ImportError:
        reason = 'needs the OpenTelemetry SDK: pip install -e ".[dev,opentelemetry]"'
        if os.environ.get("CI"):
            pytest.fail(reason, pytrace=False)
        pytest.skip(reason)
