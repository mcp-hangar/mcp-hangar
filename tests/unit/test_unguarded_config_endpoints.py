"""A config-file `remote` upstream is outside the SSRF policy, and says so (#903).

`enforce_ssrf` is set only by the command handler behind the REST API and
discovery, so a `remote` server declared in `config.yaml` gets neither the
registration check nor the connect-time re-resolution. That exclusion is
deliberate and argued in `HttpClientConfig`. These tests pin the part that was
missing: that it is stated out loud, per server, at boot.

`capture_logs` rather than `caplog` because the project logs through structlog,
which the bootstrap layer configures with a PrintLoggerFactory writing to
stderr -- nothing reaches the stdlib records `caplog` collects.
"""

from structlog.testing import capture_logs

from mcp_hangar.server.bootstrap.unguarded_endpoints import (
    warn_about_endpoints_the_ssrf_policy_does_not_cover,
)

_EVENT = "ssrf_policy_not_applied_to_config_file_endpoint"


def _warnings(logs: list[dict]) -> list[dict]:
    return [entry for entry in logs if entry.get("log_level") == "warning" and entry.get("event") == _EVENT]


def _run(servers: dict) -> tuple[int, list[dict]]:
    with capture_logs() as logs:
        count = warn_about_endpoints_the_ssrf_policy_does_not_cover({"mcp_servers": servers})
    return count, _warnings(logs)


class TestWhatGetsWarnedAbout:
    def test_a_private_literal_is_named_as_one_the_api_would_refuse(self):
        count, warnings = _run({"internal": {"mode": "remote", "endpoint": "http://10.0.0.5:8080/mcp"}})

        assert count == 1
        assert len(warnings) == 1
        entry = warnings[0]
        assert entry["mcp_server_id"] == "internal"
        assert entry["endpoint"] == "http://10.0.0.5:8080/mcp"
        # The specific difference, not a generic caution.
        assert "ssrf_blocked" in entry["detail"]

    def test_a_metadata_address_is_covered_by_the_same_branch(self):
        _, warnings = _run({"meta": {"mode": "remote", "endpoint": "http://169.254.169.254/latest/meta-data/"}})

        assert "ssrf_blocked" in warnings[0]["detail"]

    def test_a_public_hostname_is_warned_about_for_the_rebinding_half(self):
        """The half an operator is least likely to have thought about."""
        count, warnings = _run({"payments": {"mode": "remote", "endpoint": "https://payments.example.com/mcp"}})

        assert count == 1
        entry = warnings[0]
        assert entry["mcp_server_id"] == "payments"
        assert "re-pointed" in entry["detail"]
        # Not the API-would-refuse wording: this one would have been accepted.
        assert "ssrf_blocked" not in entry["detail"]

    def test_every_offender_is_named_not_just_the_first(self):
        count, warnings = _run(
            {
                "one": {"mode": "remote", "endpoint": "https://a.example.com/mcp"},
                "two": {"mode": "remote", "endpoint": "http://10.0.0.5/mcp"},
            }
        )

        assert count == 2
        assert {entry["mcp_server_id"] for entry in warnings} == {"one", "two"}


class TestWhatIsLeftAlone:
    def test_a_subprocess_server_has_no_endpoint_to_dial(self):
        count, warnings = _run({"local": {"mode": "subprocess", "command": ["python", "-m", "x"]}})

        assert count == 0
        assert warnings == []

    def test_a_docker_server_is_not_a_remote_endpoint(self):
        count, warnings = _run({"boxed": {"mode": "docker", "image": "example/mcp:1"}})

        assert count == 0
        assert warnings == []

    def test_a_remote_server_with_no_endpoint_is_skipped(self):
        count, warnings = _run({"broken": {"mode": "remote"}})

        assert count == 0
        assert warnings == []

    def test_an_empty_or_absent_section_says_nothing(self):
        with capture_logs() as logs:
            assert warn_about_endpoints_the_ssrf_policy_does_not_cover({}) == 0
            assert warn_about_endpoints_the_ssrf_policy_does_not_cover(None) == 0
            assert warn_about_endpoints_the_ssrf_policy_does_not_cover({"mcp_servers": {}}) == 0

        assert _warnings(logs) == []
