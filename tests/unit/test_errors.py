"""is_retryable pattern matching.

The HangarError/Rich* hierarchy these tests used to cover was deleted in
#970 (the #969 sweep) -- nothing in src/ raised it. What remains is the
string-pattern heuristic retry.py actually calls.
"""

from mcp_hangar.errors import is_retryable


class TestIsRetryable:
    def test_timeout_pattern_is_retryable(self):
        assert is_retryable(Exception("Operation timed out")) is True

    def test_connection_pattern_is_retryable(self):
        assert is_retryable(Exception("Connection refused")) is True

    def test_type_name_pattern_is_retryable(self):
        class SomethingTransientError(Exception):
            pass

        assert is_retryable(SomethingTransientError("x")) is True

    def test_json_and_malformed_are_retryable(self):
        assert is_retryable(Exception("malformed JSON body")) is True

    def test_plain_failure_is_not_retryable(self):
        assert is_retryable(Exception("permission denied")) is False

    def test_config_style_error_is_not_retryable(self):
        class ConfigurationProblem(Exception):
            pass

        assert is_retryable(ConfigurationProblem("missing key")) is False
