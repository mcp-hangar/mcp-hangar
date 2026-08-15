"""Command, path and identifier validation: what `InputValidator` accepts and refuses."""

import pytest

from mcp_hangar.domain.security.input_validator import (
    ALLOWED_COMMANDS,
    InputValidator,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)


def test_default_allowed_commands_include_safe_mcp_runtimes():
    expected = {"uvx", "npx", "node", "python", "python3", "uv", "docker", "podman", "bun", "deno"}

    assert expected.issubset(ALLOWED_COMMANDS)


def test_validate_command_allows_known_safe_command():
    validator = InputValidator()

    result = validator.validate_command(["uvx", "mcp-server"])

    assert result.valid


def test_validate_command_blocks_unknown_command():
    validator = InputValidator()

    with pytest.raises(ValueError, match="not in the allowed command list"):
        _ = validator.validate_command(["ruby", "server.rb"])


def test_validate_command_blocks_empty_command():
    validator = InputValidator()

    result = validator.validate_command([])

    assert not result.valid


def test_allowed_commands_can_be_overridden_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_ALLOWED_COMMANDS", "custom-runner,python")

    validator = InputValidator()

    assert validator.validate_command(["custom-runner", "start"]).valid
    with pytest.raises(ValueError, match="not in the allowed command list"):
        _ = validator.validate_command(["uvx", "mcp-server"])


class TestValidationIssue:
    """Tests for ValidationIssue dataclass."""

    def test_to_dict_without_value(self):
        issue = ValidationIssue(field="f", message="m", severity=ValidationSeverity.ERROR)
        d = issue.to_dict()
        assert d == {"field": "f", "message": "m", "severity": "error"}
        assert "value" not in d

    def test_to_dict_with_short_value(self):
        issue = ValidationIssue(field="f", message="m", value="short")
        d = issue.to_dict()
        assert d["value"] == "short"

    def test_to_dict_truncates_long_value(self):
        long_val = "x" * 200
        issue = ValidationIssue(field="f", message="m", value=long_val)
        d = issue.to_dict()
        assert len(d["value"]) == 103  # 100 chars + "..."
        assert d["value"].endswith("...")

    def test_severity_values(self):
        assert ValidationSeverity.ERROR.value == "error"
        assert ValidationSeverity.WARNING.value == "warning"
        assert ValidationSeverity.INFO.value == "info"


class TestValidationResult:
    """Tests for ValidationResult methods."""

    def test_add_error_sets_valid_false(self):
        result = ValidationResult(valid=True)
        result.add_error("field", "error msg", value=42)
        assert not result.valid
        assert len(result.issues) == 1
        assert result.issues[0].severity == ValidationSeverity.ERROR

    def test_add_warning_does_not_change_valid(self):
        result = ValidationResult(valid=True)
        result.add_warning("field", "warning msg", value="v")
        assert result.valid
        assert len(result.issues) == 1
        assert result.issues[0].severity == ValidationSeverity.WARNING

    def test_merge_propagates_invalid(self):
        r1 = ValidationResult(valid=True)
        r2 = ValidationResult(valid=True)
        r2.add_error("x", "fail")
        r1.merge(r2)
        assert not r1.valid
        assert len(r1.issues) == 1

    def test_merge_valid_into_valid(self):
        r1 = ValidationResult(valid=True)
        r2 = ValidationResult(valid=True)
        r2.add_warning("x", "warn")
        r1.merge(r2)
        assert r1.valid
        assert len(r1.issues) == 1

    def test_errors_property_filters(self):
        r = ValidationResult(valid=True)
        r.add_error("a", "err")
        r.add_warning("b", "warn")
        assert len(r.errors) == 1
        assert r.errors[0].field == "a"

    def test_warnings_property_filters(self):
        r = ValidationResult(valid=True)
        r.add_error("a", "err")
        r.add_warning("b", "warn")
        assert len(r.warnings) == 1
        assert r.warnings[0].field == "b"

    def test_to_dict(self):
        r = ValidationResult(valid=True)
        r.add_error("a", "err")
        r.add_warning("b", "warn")
        d = r.to_dict()
        assert d["valid"] is False
        assert d["error_count"] == 1
        assert d["warning_count"] == 1
        assert len(d["issues"]) == 2


class TestInputValidatorExtended:
    """Extended coverage for InputValidator methods."""

    def test_init_with_custom_config(self):
        v = InputValidator(
            allow_absolute_paths=True,
            allowed_commands=["python", "node"],
            blocked_commands=["rm"],
        )
        assert v.allow_absolute_paths is True
        assert v.allowed_commands == {"python", "node"}
        assert v.blocked_commands == {"rm"}

    def test_mcp_server_id_non_string_type(self):
        v = InputValidator()
        result = v.validate_mcp_server_id(123)
        assert not result.valid
        assert any("string" in i.message.lower() for i in result.errors)

    def test_tool_name_non_string_type(self):
        v = InputValidator()
        result = v.validate_tool_name(999)
        assert not result.valid

    def test_tool_name_too_long(self):
        v = InputValidator()
        result = v.validate_tool_name("a" * 200)
        assert not result.valid
        assert any("length" in i.message.lower() for i in result.errors)

    def test_tool_name_path_traversal(self):
        v = InputValidator()
        result = v.validate_tool_name("tool..name")
        assert not result.valid
        assert any("traversal" in i.message.lower() for i in result.errors)

    def test_arguments_none_is_valid(self):
        v = InputValidator()
        result = v.validate_arguments(None)
        assert result.valid

    def test_arguments_non_serializable(self):
        v = InputValidator()
        result = v.validate_arguments({"fn": lambda: None})
        assert not result.valid
        assert any("serializable" in i.message.lower() for i in result.errors)

    def test_arguments_non_string_key(self):
        v = InputValidator()
        result = v.validate_arguments({123: "val"})
        assert result.valid is False or len(result.issues) > 0

    def test_arguments_empty_key(self):
        v = InputValidator()
        result = v.validate_arguments({"": "val"})
        assert any("empty" in i.message.lower() for i in result.issues)

    def test_arguments_very_long_string_value(self):
        v = InputValidator()
        result = v.validate_arguments({"key": "x" * 1_100_000})
        assert not result.valid

    def test_arguments_nested_list(self):
        v = InputValidator()
        result = v.validate_arguments({"items": [1, "two", {"nested": True}]})
        assert result.valid

    def test_timeout_none_is_valid(self):
        v = InputValidator()
        result = v.validate_timeout(None)
        assert result.valid

    def test_timeout_non_number(self):
        v = InputValidator()
        result = v.validate_timeout("fast")
        assert not result.valid

    def test_command_none(self):
        v = InputValidator()
        result = v.validate_command(None)
        assert not result.valid

    def test_command_not_list(self):
        v = InputValidator()
        result = v.validate_command("python script.py")
        assert not result.valid

    def test_command_empty_list(self):
        v = InputValidator()
        result = v.validate_command([])
        assert not result.valid

    def test_command_too_many_args(self):
        v = InputValidator()
        result = v.validate_command(["python"] + ["arg"] * 150)
        assert not result.valid

    def test_command_non_string_element(self):
        v = InputValidator()
        result = v.validate_command(["python", 42, "script.py"])
        assert not result.valid or len(result.issues) > 0

    def test_command_allowed_commands_whitelist(self):
        v = InputValidator(allowed_commands=["python"])
        with pytest.raises(ValueError, match="allowed command list"):
            v.validate_command(["node", "app.js"])

    def test_command_absolute_path_warning(self):
        v = InputValidator(allow_absolute_paths=False)
        result = v.validate_command(["/usr/bin/python", "script.py"])
        assert len(result.warnings) > 0
        assert any("absolute" in w.message.lower() for w in result.warnings)

    def test_command_absolute_path_no_warning_when_allowed(self):
        v = InputValidator(allow_absolute_paths=True)
        result = v.validate_command(["/usr/bin/python", "script.py"])
        assert len(result.warnings) == 0

    def test_docker_image_none(self):
        v = InputValidator()
        result = v.validate_docker_image(None)
        assert not result.valid

    def test_docker_image_non_string(self):
        v = InputValidator()
        result = v.validate_docker_image(42)
        assert not result.valid

    def test_docker_image_empty(self):
        v = InputValidator()
        result = v.validate_docker_image("")
        assert not result.valid

    def test_docker_image_too_long(self):
        v = InputValidator()
        result = v.validate_docker_image("a" * 300)
        assert not result.valid

    def test_docker_image_lenient_format(self):
        """Images that fail strict DOCKER_IMAGE_PATTERN but pass lenient check."""
        v = InputValidator()
        result = v.validate_docker_image("UPPER_CASE:tag")
        # Lenient regex allows \w.\-/:@ so this should pass
        assert result.valid

    def test_env_vars_none_is_valid(self):
        v = InputValidator()
        result = v.validate_environment_variables(None)
        assert result.valid

    def test_env_vars_not_dict(self):
        v = InputValidator()
        result = v.validate_environment_variables("not a dict")
        assert not result.valid

    def test_env_vars_non_string_key(self):
        v = InputValidator()
        result = v.validate_environment_variables({123: "val"})
        assert not result.valid or len(result.issues) > 0

    def test_env_vars_empty_key(self):
        v = InputValidator()
        result = v.validate_environment_variables({"": "val"})
        assert any("empty" in i.message.lower() for i in result.issues)

    def test_env_vars_long_key(self):
        v = InputValidator()
        result = v.validate_environment_variables({"K" * 300: "val"})
        assert any("length" in i.message.lower() for i in result.issues)

    def test_env_vars_invalid_key_format(self):
        v = InputValidator()
        result = v.validate_environment_variables({"123-bad": "val"})
        assert not result.valid

    def test_env_vars_non_string_value(self):
        v = InputValidator()
        result = v.validate_environment_variables({"KEY": 42})
        assert not result.valid or len(result.issues) > 0

    def test_env_vars_long_value(self):
        v = InputValidator()
        result = v.validate_environment_variables({"KEY": "x" * 40000})
        assert any("length" in i.message.lower() for i in result.issues)

    def test_env_vars_dangerous_value_warning(self):
        v = InputValidator()
        result = v.validate_environment_variables({"KEY": "value; rm -rf /"})
        # Should have a warning about dangerous chars
        assert len(result.warnings) > 0

    def test_validate_all_combines_results(self):
        v = InputValidator()
        result = v.validate_all(
            mcp_server_id="valid_id",
            tool_name="valid_tool",
            timeout=30.0,
        )
        assert result.valid

    def test_validate_all_with_invalid_inputs(self):
        v = InputValidator()
        result = v.validate_all(
            mcp_server_id="",
            tool_name="",
            timeout=-1,
        )
        assert not result.valid
        assert len(result.errors) >= 3
