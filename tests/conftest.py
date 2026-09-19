"""Suite-wide test configuration.

``otel_sdk`` marks a test that needs the OpenTelemetry SDK (the ``opentelemetry``
extra), not just the API that ``mcp`` pulls in. Under CI a missing SDK fails the
test: the core jobs install the extra, so its absence is a broken install, and a
skip there would report a tracing contract as green without running it. Locally
it skips with the install hint, so a ``.[dev]`` checkout still runs the suite.

Colour is also taken out of the environment here, before collection, because
that is the last moment early enough to matter. See ``pytest_configure``.
"""

import os

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "otel_sdk: needs the OpenTelemetry SDK; fails without it under CI")

    # `rich` decides whether to emit ANSI when a `Console` is CONSTRUCTED, and
    # the CLI constructs its consoles at module import -- which pytest does
    # during collection, before any fixture runs. So `monkeypatch.delenv` in a
    # fixture is too late: the console it means to affect already exists and
    # goes on colouring. This hook runs before collection, which is what makes
    # it the right place and the only one that works.
    #
    # Without this, a contributor whose shell exports FORCE_COLOR saw 11 tests
    # fail on plain-text assertions -- `drift math.add` arriving as
    # `\x1b[31mdrift\x1b[0m math.add` -- while every CI runner stayed green,
    # because GitHub sets no such variable (#1521).
    #
    # Subprocess tests inherit this environment, so the child CLIs they launch
    # come up unforced too. Production behaviour is untouched: the shipped CLI
    # still honours FORCE_COLOR and NO_COLOR for real output.
    for variable in ("FORCE_COLOR", "NO_COLOR"):
        os.environ.pop(variable, None)


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
