"""Tests for ProviderMode normalization - ensuring modes are properly converted to enums."""

import pytest

from mcp_hangar.domain.value_objects import ProviderMode


class TestProviderModeNormalization:
    """Tests for ProviderMode.normalize() method."""

    def test_container_string_normalizes_to_container(self):
        """Verify 'container' string normalizes to CONTAINER mode."""
        result = ProviderMode.normalize("container")
        assert result == ProviderMode.CONTAINER

    def test_docker_string_stays_docker(self):
        """Verify 'docker' string remains DOCKER mode."""
        result = ProviderMode.normalize("docker")
        assert result == ProviderMode.DOCKER

    def test_subprocess_string_stays_subprocess(self):
        """Verify 'subprocess' string remains SUBPROCESS mode."""
        result = ProviderMode.normalize("subprocess")
        assert result == ProviderMode.SUBPROCESS

    def test_remote_string_stays_remote(self):
        """Verify 'remote' string remains REMOTE mode."""
        result = ProviderMode.normalize("remote")
        assert result == ProviderMode.REMOTE

    def test_group_string_stays_group(self):
        """Verify 'group' string remains GROUP mode."""
        result = ProviderMode.normalize("group")
        assert result == ProviderMode.GROUP

    def test_container_enum_stays_container(self):
        """Verify CONTAINER enum remains CONTAINER mode."""
        result = ProviderMode.normalize(ProviderMode.CONTAINER)
        assert result == ProviderMode.CONTAINER

    def test_docker_enum_stays_docker(self):
        """Verify DOCKER enum remains DOCKER mode."""
        result = ProviderMode.normalize(ProviderMode.DOCKER)
        assert result == ProviderMode.DOCKER

    def test_subprocess_enum_stays_subprocess(self):
        """Verify SUBPROCESS enum remains SUBPROCESS mode."""
        result = ProviderMode.normalize(ProviderMode.SUBPROCESS)
        assert result == ProviderMode.SUBPROCESS

    def test_invalid_mode_raises_value_error(self):
        """Verify invalid mode raises ValueError."""
        with pytest.raises(ValueError):
            ProviderMode.normalize("invalid_mode")
