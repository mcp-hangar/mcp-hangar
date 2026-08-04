"""Secrets are scrubbed from logs without anything having been wired.

`logging_config` used to import `domain.security.redactor` -- the shared kernel
reaching up into the domain. The obvious alternative was a port with the
composition root injecting a redactor, which is how the metrics publisher and
the config loader were fixed.

That would be the wrong shape here, and this file says why in the form of a
test: an uninjected port means logging quietly stops redacting. For a function
whose entire job is keeping secrets out of logs, "silently does nothing when
unwired" is the worst available failure mode -- and two of this codebase's five
ports were, in fact, found unwired.

So the redactor moved down into the shared kernel instead, and log redaction
depends on nothing but the import. These tests exercise it in a bare process:
no bootstrap, no context, no composition root.
"""

from __future__ import annotations

import pytest

from mcp_hangar.logging_config import _redact_secret_values


@pytest.mark.parametrize(
    ("secret", "label"),
    [
        ("ghp_" + "a" * 36, "github token"),
        ("sk_live_" + "b" * 24, "stripe key"),
        ("xoxb-" + "1" * 12 + "-" + "2" * 24, "slack token"),
    ],
)
def test_a_token_in_a_message_is_scrubbed_with_nothing_configured(secret, label):
    """No bootstrap has run in this process. Redaction still happens."""
    scrubbed = _redact_secret_values(None, "info", {"event": f"upstream rejected {secret}"})
    assert secret not in scrubbed["event"], label


def test_a_token_nested_in_a_structure_is_scrubbed():
    secret = "ghp_" + "c" * 36
    scrubbed = _redact_secret_values(
        None, "info", {"event": "call failed", "detail": {"headers": [f"Bearer {secret}"]}}
    )
    assert secret not in str(scrubbed)


def test_ordinary_text_survives():
    """A redactor that eats normal log lines is its own kind of outage."""
    event = {"event": "mcp_server_started", "mcp_server_id": "math", "duration_ms": 12.5}
    assert _redact_secret_values(None, "info", dict(event)) == event


class TestTheKernelDoesNotReachIntoTheDomain:
    def test_logging_config_imports_no_domain_module(self):
        import pathlib

        import mcp_hangar.logging_config as module

        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        offenders = [
            line
            for line in source.splitlines()
            if line.strip().startswith(("from .domain", "from mcp_hangar.domain", "import mcp_hangar.domain"))
        ]
        assert offenders == [], f"logging_config reaches up into the domain again: {offenders}"

    def test_the_redactor_still_imports_nothing_but_the_standard_library(self):
        """What makes it shared-kernel material rather than a relabelled domain module."""
        import pathlib

        import mcp_hangar.redactor as module

        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        offenders = [line for line in source.splitlines() if line.strip().startswith(("from .", "from mcp_hangar"))]
        assert offenders == [], f"the redactor grew an internal dependency: {offenders}"

    def test_the_documented_surface_still_resolves(self):
        """`from mcp_hangar.domain.security import OutputRedactor` was public."""
        from mcp_hangar.domain.security import OutputRedactor, RedactionPattern
        from mcp_hangar.redactor import OutputRedactor as Moved

        assert OutputRedactor is Moved
        assert RedactionPattern is not None
