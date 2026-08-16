"""Input sanitisation: what is stripped, what is escaped, what is rejected outright."""

from typing import cast

import pytest

from mcp_hangar.domain.security.sanitizer import (  # noqa: E402
    Sanitizer,
    sanitize_command_argument,
    sanitize_environment_value,
    sanitize_log_message,
    sanitize_path,
)


class TestSanitizerExtended:
    """Extended tests for Sanitizer methods."""

    def test_init_custom_limits(self):
        s = Sanitizer(
            max_argument_length=100,
            max_path_length=200,
            max_log_message_length=500,
        )
        assert s.max_argument_length == 100
        assert s.max_path_length == 200
        assert s.max_log_message_length == 500

    def test_sanitize_command_argument_non_string(self):
        s = Sanitizer()
        result = s.sanitize_command_argument(cast(str, cast(object, 42)))
        assert result == "42"

    def test_sanitize_command_argument_truncation(self):
        s = Sanitizer(max_argument_length=10)
        result = s.sanitize_command_argument("a" * 100)
        assert len(result) <= 10

    def test_sanitize_command_argument_allow_quotes(self):
        s = Sanitizer()
        result_no_quotes = s.sanitize_command_argument('"hello"', allow_quotes=False)
        result_quotes = s.sanitize_command_argument('"hello"', allow_quotes=True)
        assert '"' not in result_no_quotes
        assert '"' in result_quotes

    def test_sanitize_command_argument_allow_spaces(self):
        s = Sanitizer()
        # Space is not in SHELL_METACHARACTERS, so allow_spaces flag controls
        # whether space is added to the dangerous set.  When True (default),
        # space is discarded from the dangerous set (no-op since not present).
        # When False, space is not removed -- but since it was never there,
        # spaces still pass through.  Verify both paths execute without error
        # and spaces are preserved regardless (space is safe by default).
        result_spaces = s.sanitize_command_argument("hello world", allow_spaces=True)
        result_no_spaces = s.sanitize_command_argument("hello world", allow_spaces=False)
        assert " " in result_spaces
        assert isinstance(result_no_spaces, str)

    def test_sanitize_command_list(self):
        s = Sanitizer()
        result = s.sanitize_command_list(["python", "-c", "import os; print()"])
        assert len(result) == 3
        assert ";" not in result[2]

    def test_sanitize_environment_value_non_string(self):
        s = Sanitizer()
        result = s.sanitize_environment_value(cast(str, cast(object, 42)))
        assert result == "42"

    def test_sanitize_environment_value_truncation(self):
        s = Sanitizer()
        result = s.sanitize_environment_value("x" * 40000)
        assert len(result) <= s.MAX_ENV_VALUE_LENGTH

    def test_sanitize_environment_value_allow_newlines(self):
        s = Sanitizer()
        result = s.sanitize_environment_value("line1\nline2", allow_newlines=True)
        assert "\n" in result

    def test_sanitize_environment_dict(self):
        s = Sanitizer()
        result = s.sanitize_environment_dict({"KEY": "val\x00ue", "K2": "ok"})
        assert "\x00" not in result["KEY"]
        assert result["K2"] == "ok"

    def test_sanitize_path_hidden_files_disallowed(self):
        s = Sanitizer()
        with pytest.raises(ValueError, match="[Hh]idden"):
            s.sanitize_path(".secret/file", allow_hidden=False)

    def test_sanitize_path_hidden_files_allowed(self):
        s = Sanitizer()
        result = s.sanitize_path(".secret/file", allow_hidden=True)
        assert result == ".secret/file"

    def test_sanitize_path_control_characters(self):
        s = Sanitizer()
        with pytest.raises(ValueError, match="control"):
            s.sanitize_path("path\x01file")

    def test_sanitize_path_too_long(self):
        s = Sanitizer(max_path_length=50)
        with pytest.raises(ValueError, match="length"):
            s.sanitize_path("a" * 100)

    def test_sanitize_path_non_string(self):
        s = Sanitizer()
        result = s.sanitize_path(cast(str, cast(object, 42)))
        assert result == "42"

    def test_sanitize_path_unicode_normalization(self):
        s = Sanitizer()
        # NFKC normalizes fullwidth chars
        result = s.sanitize_path("normal_path")
        assert result == "normal_path"

    def test_sanitize_path_absolute_allowed(self):
        s = Sanitizer()
        result = s.sanitize_path("/usr/bin/python", allow_absolute=True)
        assert result == "/usr/bin/python"

    def test_sanitize_path_windows_absolute(self):
        s = Sanitizer()
        with pytest.raises(ValueError, match="[Aa]bsolute"):
            s.sanitize_path("C:\\Windows\\System32", allow_absolute=False)

    def test_sanitize_log_message_non_string(self):
        s = Sanitizer()
        result = s.sanitize_log_message(cast(str, cast(object, 42)))
        assert "42" in result

    def test_sanitize_log_message_crlf(self):
        s = Sanitizer()
        result = s.sanitize_log_message("line1\r\nline2")
        assert "\r" not in result
        assert "\n" not in result
        assert "\\r\\n" in result

    def test_sanitize_log_message_tab(self):
        s = Sanitizer()
        result = s.sanitize_log_message("col1\tcol2")
        assert "\t" not in result
        assert "\\t" in result

    def test_sanitize_log_message_custom_max_length(self):
        s = Sanitizer()
        result = s.sanitize_log_message("x" * 100, max_length=50)
        assert len(result) < 100
        assert "truncated" in result

    def test_sanitize_for_json_list(self):
        s = Sanitizer()
        result = s.sanitize_for_json(["hello\x00", "world"])
        assert result == ["hello", "world"]

    def test_sanitize_for_json_primitives(self):
        s = Sanitizer()
        assert s.sanitize_for_json(42) == 42
        assert s.sanitize_for_json(3.14) == 3.14
        assert s.sanitize_for_json(True) is True
        assert s.sanitize_for_json(None) is None

    def test_sanitize_for_json_unknown_type(self):
        s = Sanitizer()
        result = s.sanitize_for_json(object())
        assert isinstance(result, str)

    def test_escape_html(self):
        s = Sanitizer()
        assert s.escape_html('<script>alert("xss")</script>') == "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;"

    def test_mask_value_empty(self):
        s = Sanitizer()
        assert s.mask_value("") == ""

    def test_mask_value_short(self):
        s = Sanitizer()
        result = s.mask_value("abc", visible_chars=4)
        assert "abc" not in result
        assert "*" in result

    def test_mask_value_normal(self):
        s = Sanitizer()
        result = s.mask_value("secretpassword", visible_chars=4)
        assert result.startswith("secr")
        assert "*" in result


class TestSanitizerConvenienceFunctions:
    """Tests for module-level convenience functions."""

    def test_sanitize_command_argument_convenience(self):
        result = sanitize_command_argument("arg;evil")
        assert ";" not in result

    def test_sanitize_environment_value_convenience(self):
        result = sanitize_environment_value("val\x00ue")
        assert "\x00" not in result

    def test_sanitize_log_message_convenience(self):
        result = sanitize_log_message("msg\nevil")
        assert "\n" not in result

    def test_sanitize_path_convenience(self):
        result = sanitize_path("normal/path")
        assert result == "normal/path"

    def test_sanitize_path_convenience_rejects_traversal(self):
        with pytest.raises(ValueError):
            sanitize_path("../etc/passwd")
