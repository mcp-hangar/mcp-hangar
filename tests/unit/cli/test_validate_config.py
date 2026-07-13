"""Tests for scripts/validate_config.py.

The validator lives under scripts/ (not the installed package), so it is loaded
directly from the file path via importlib.
"""

import importlib.util
from pathlib import Path
import sys

import pytest
import yaml

# Load scripts/validate_config.py as a module.
_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "validate_config.py"
_spec = importlib.util.spec_from_file_location("validate_config", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
validate_config_mod = importlib.util.module_from_spec(_spec)
sys.modules["validate_config"] = validate_config_mod
_spec.loader.exec_module(validate_config_mod)

validate_config = validate_config_mod.validate_config


def _write_config(data: dict, tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f)
    return config_path


def _warnings_text(result) -> str:
    return "\n".join(result.warnings)


class TestReadOnlyWritableVolumeWarning:
    """The validator should warn when a read-only container has no writable volume."""

    def test_warns_when_read_only_and_no_writable_volume(self, tmp_path):
        """read_only defaulted true + no volumes -> warning about EROFS/writable mount."""
        config_path = _write_config(
            {"providers": {"svc": {"mode": "container", "image": "example/svc:latest"}}},
            tmp_path,
        )
        result = validate_config(config_path)
        text = _warnings_text(result)
        assert "writable" in text.lower()
        assert "svc" in text

    def test_warns_when_only_read_only_volume_present(self, tmp_path):
        """A ':ro' volume does not count as writable -> warning still fires."""
        config_path = _write_config(
            {
                "providers": {
                    "svc": {
                        "mode": "container",
                        "image": "example/svc:latest",
                        "volumes": ["/host/data:/data:ro"],
                    }
                }
            },
            tmp_path,
        )
        result = validate_config(config_path)
        assert "writable" in _warnings_text(result).lower()

    def test_no_warning_when_rw_volume_present(self, tmp_path):
        """An explicit ':rw' volume satisfies the writable-mount requirement."""
        config_path = _write_config(
            {
                "providers": {
                    "svc": {
                        "mode": "container",
                        "image": "example/svc:latest",
                        "volumes": ["/host/data:/data:rw"],
                    }
                }
            },
            tmp_path,
        )
        result = validate_config(config_path)
        assert "writable" not in _warnings_text(result).lower()

    def test_no_warning_when_volume_defaults_to_writable(self, tmp_path):
        """A volume with no mode suffix defaults to read-write in Docker."""
        config_path = _write_config(
            {
                "providers": {
                    "svc": {
                        "mode": "container",
                        "image": "example/svc:latest",
                        "volumes": ["/host/data:/data"],
                    }
                }
            },
            tmp_path,
        )
        result = validate_config(config_path)
        assert "writable" not in _warnings_text(result).lower()

    def test_no_warning_when_read_only_false(self, tmp_path):
        """An explicitly writable root filesystem needs no writable volume."""
        config_path = _write_config(
            {
                "providers": {
                    "svc": {
                        "mode": "container",
                        "image": "example/svc:latest",
                        "read_only": False,
                    }
                }
            },
            tmp_path,
        )
        result = validate_config(config_path)
        assert "writable" not in _warnings_text(result).lower()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
