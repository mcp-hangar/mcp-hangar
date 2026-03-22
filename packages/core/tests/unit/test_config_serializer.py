"""Unit tests for config serializer module.

Tests cover ProviderGroup.to_config_dict() and all functions in
mcp_hangar.server.config_serializer.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from mcp_hangar.domain.model.provider_group import ProviderGroup
from mcp_hangar.domain.value_objects import LoadBalancerStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_provider(provider_id: str, config_dict: dict | None = None) -> MagicMock:
    """Create a mock Provider with to_config_dict support."""
    mock = MagicMock()
    mock.id = provider_id
    mock.to_config_dict = MagicMock(
        return_value=config_dict or {"mode": "subprocess", "command": ["python", "-m", "server"]}
    )
    return mock


# ---------------------------------------------------------------------------
# TestProviderGroupToConfigDict
# ---------------------------------------------------------------------------


class TestProviderGroupToConfigDict:
    """Tests for ProviderGroup.to_config_dict()."""

    def test_group_config_dict_has_mode_group(self):
        """to_config_dict() should have mode == 'group'."""
        group = ProviderGroup("g")
        assert group.to_config_dict()["mode"] == "group"

    def test_group_config_dict_has_strategy(self):
        """to_config_dict() should include strategy value."""
        group = ProviderGroup("g", strategy=LoadBalancerStrategy.ROUND_ROBIN)
        assert group.to_config_dict()["strategy"] == "round_robin"

    def test_group_config_dict_has_min_healthy(self):
        """to_config_dict() should include min_healthy."""
        group = ProviderGroup("g", min_healthy=2)
        assert group.to_config_dict()["min_healthy"] == 2

    def test_group_config_dict_has_members(self):
        """to_config_dict() should include members list after add_member()."""
        group = ProviderGroup("g", auto_start=False)
        provider = _make_mock_provider("p1")
        group.add_member(provider, weight=1, priority=1)
        result = group.to_config_dict()
        assert result["members"] == [{"id": "p1", "weight": 1, "priority": 1}]

    def test_description_included_when_set(self):
        """to_config_dict() should include description when set."""
        group = ProviderGroup("g", description="desc")
        assert group.to_config_dict()["description"] == "desc"

    def test_description_omitted_when_none(self):
        """to_config_dict() should not include description key when None."""
        group = ProviderGroup("g")
        assert "description" not in group.to_config_dict()

    def test_auto_start_included(self):
        """to_config_dict() should include auto_start."""
        group = ProviderGroup("g", auto_start=False)
        assert group.to_config_dict()["auto_start"] is False


# ---------------------------------------------------------------------------
# TestSerializeProviders
# ---------------------------------------------------------------------------


class TestSerializeProviders:
    """Tests for serialize_providers()."""

    def test_returns_empty_dict_when_no_providers(self):
        """serialize_providers() should return {} when repository is empty."""
        from mcp_hangar.server.config_serializer import serialize_providers

        mock_ctx = MagicMock()
        mock_ctx.repository.get_all.return_value = {}

        result = serialize_providers(providers={})
        assert result == {}

    def test_returns_provider_config_dicts(self):
        """serialize_providers() should return {id: to_config_dict()} for each provider."""
        from mcp_hangar.server.config_serializer import serialize_providers

        provider = _make_mock_provider("p", {"mode": "subprocess", "command": ["python", "-m", "server"]})
        result = serialize_providers(providers={"p": provider})
        assert result == {"p": {"mode": "subprocess", "command": ["python", "-m", "server"]}}

    def test_uses_to_config_dict(self):
        """serialize_providers() should call to_config_dict() for each provider."""
        from mcp_hangar.server.config_serializer import serialize_providers

        provider = _make_mock_provider("p")
        serialize_providers(providers={"p": provider})
        provider.to_config_dict.assert_called_once()

    def test_fetches_from_context_when_no_providers_arg(self):
        """serialize_providers() should call get_context() when providers arg is None."""
        from mcp_hangar.server.config_serializer import serialize_providers

        mock_ctx = MagicMock()
        provider = _make_mock_provider("ctx_p", {"mode": "subprocess"})
        mock_ctx.repository.get_all.return_value = {"ctx_p": provider}

        with patch("mcp_hangar.server.config_serializer.get_context", return_value=mock_ctx):
            result = serialize_providers()

        assert "ctx_p" in result
        provider.to_config_dict.assert_called_once()


# ---------------------------------------------------------------------------
# TestSerializeGroups
# ---------------------------------------------------------------------------


class TestSerializeGroups:
    """Tests for serialize_groups()."""

    def test_returns_empty_dict_when_no_groups(self):
        """serialize_groups() should return {} when groups dict is empty."""
        from mcp_hangar.server.config_serializer import serialize_groups

        result = serialize_groups(groups={})
        assert result == {}

    def test_returns_group_config_dicts(self):
        """serialize_groups() should return {id: to_config_dict()} for each group."""
        from mcp_hangar.server.config_serializer import serialize_groups

        mock_group = MagicMock()
        mock_group.to_config_dict.return_value = {
            "mode": "group",
            "strategy": "round_robin",
            "min_healthy": 1,
            "members": [],
        }
        result = serialize_groups(groups={"g": mock_group})
        assert result == {"g": {"mode": "group", "strategy": "round_robin", "min_healthy": 1, "members": []}}

    def test_uses_to_config_dict_on_groups(self):
        """serialize_groups() should call to_config_dict() on each group."""
        from mcp_hangar.server.config_serializer import serialize_groups

        mock_group = MagicMock()
        mock_group.to_config_dict.return_value = {"mode": "group", "members": []}
        serialize_groups(groups={"g": mock_group})
        mock_group.to_config_dict.assert_called_once()

    def test_fetches_from_context_when_no_groups_arg(self):
        """serialize_groups() should call get_context() when groups arg is None."""
        from mcp_hangar.server.config_serializer import serialize_groups

        mock_ctx = MagicMock()
        mock_group = MagicMock()
        mock_group.to_config_dict.return_value = {"mode": "group", "members": []}
        mock_ctx.groups = {"ctx_g": mock_group}

        with patch("mcp_hangar.server.config_serializer.get_context", return_value=mock_ctx):
            result = serialize_groups()

        assert "ctx_g" in result
        mock_group.to_config_dict.assert_called_once()


# ---------------------------------------------------------------------------
# TestSerializeFullConfig
# ---------------------------------------------------------------------------


class TestSerializeFullConfig:
    """Tests for serialize_full_config()."""

    def test_has_providers_key(self):
        """serialize_full_config() result should have 'providers' key."""
        from mcp_hangar.server.config_serializer import serialize_full_config

        result = serialize_full_config(providers={}, groups={})
        assert "providers" in result

    def test_providers_and_groups_merged(self):
        """serialize_full_config() result['providers'] should contain both providers and groups."""
        from mcp_hangar.server.config_serializer import serialize_full_config

        provider = _make_mock_provider("p", {"mode": "subprocess"})
        mock_group = MagicMock()
        mock_group.to_config_dict.return_value = {"mode": "group", "members": []}

        result = serialize_full_config(providers={"p": provider}, groups={"g": mock_group})
        assert "p" in result["providers"]
        assert "g" in result["providers"]

    def test_returns_serializable_dict(self):
        """yaml.safe_dump(serialize_full_config()) should succeed without error."""
        from mcp_hangar.server.config_serializer import serialize_full_config

        provider = _make_mock_provider("p", {"mode": "subprocess", "command": ["python"]})
        mock_group = MagicMock()
        mock_group.to_config_dict.return_value = {
            "mode": "group",
            "strategy": "round_robin",
            "min_healthy": 1,
            "members": [],
        }

        result = serialize_full_config(providers={"p": provider}, groups={"g": mock_group})
        dumped = yaml.safe_dump(result, default_flow_style=False, sort_keys=True, allow_unicode=True)
        assert "providers" in dumped

    def test_uses_context_when_no_args(self):
        """serialize_full_config() should call get_context() when no args given."""
        from mcp_hangar.server.config_serializer import serialize_full_config

        mock_ctx = MagicMock()
        mock_ctx.repository.get_all.return_value = {}
        mock_ctx.groups = {}

        with patch("mcp_hangar.server.config_serializer.get_context", return_value=mock_ctx):
            result = serialize_full_config()

        assert "providers" in result


# ---------------------------------------------------------------------------
# TestWriteConfigBackup
# ---------------------------------------------------------------------------


class TestWriteConfigBackup:
    """Tests for write_config_backup()."""

    def test_creates_bak1_file(self, tmp_path: Path):
        """write_config_backup() should create a .bak1 file."""
        from mcp_hangar.server.config_serializer import write_config_backup

        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("providers: {}")

        with patch("mcp_hangar.server.config_serializer.serialize_full_config", return_value={"providers": {}}):
            write_config_backup(str(config_file))

        assert (tmp_path / "test_config.yaml.bak1").exists()

    def test_bak1_contains_yaml(self, tmp_path: Path):
        """bak1 content should be parseable by yaml.safe_load()."""
        from mcp_hangar.server.config_serializer import write_config_backup

        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("providers: {}")

        with patch(
            "mcp_hangar.server.config_serializer.serialize_full_config",
            return_value={"providers": {"p": {"mode": "subprocess"}}},
        ):
            write_config_backup(str(config_file))

        bak1 = tmp_path / "test_config.yaml.bak1"
        parsed = yaml.safe_load(bak1.read_text())
        assert isinstance(parsed, dict)
        assert "providers" in parsed

    def test_rotation_shifts_bak1_to_bak2(self, tmp_path: Path):
        """If bak1 exists before backup, after backup bak2 should have old bak1 content."""
        from mcp_hangar.server.config_serializer import write_config_backup

        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("providers: {}")

        # Create a pre-existing bak1 with known content
        old_bak1 = tmp_path / "test_config.yaml.bak1"
        old_bak1.write_text("old_bak1_content: true")

        with patch("mcp_hangar.server.config_serializer.serialize_full_config", return_value={"providers": {}}):
            write_config_backup(str(config_file))

        bak2 = tmp_path / "test_config.yaml.bak2"
        assert bak2.exists()
        content = yaml.safe_load(bak2.read_text())
        assert content == {"old_bak1_content": True}

    def test_rotation_drops_bak5(self, tmp_path: Path):
        """If bak5 exists (full rotation), bak5 is overwritten with old bak4 content."""
        from mcp_hangar.server.config_serializer import write_config_backup

        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("providers: {}")

        # Set up full chain: bak1..bak5
        for i in range(1, 6):
            bak = tmp_path / f"test_config.yaml.bak{i}"
            bak.write_text(f"slot: bak{i}")

        with patch("mcp_hangar.server.config_serializer.serialize_full_config", return_value={"providers": {}}):
            write_config_backup(str(config_file))

        # bak5 should now contain old bak4 content
        bak5 = tmp_path / "test_config.yaml.bak5"
        assert bak5.exists()
        content = yaml.safe_load(bak5.read_text())
        assert content == {"slot": "bak4"}

    def test_returns_bak1_path(self, tmp_path: Path):
        """write_config_backup() should return str path ending in '.bak1'."""
        from mcp_hangar.server.config_serializer import write_config_backup

        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("providers: {}")

        with patch("mcp_hangar.server.config_serializer.serialize_full_config", return_value={"providers": {}}):
            result = write_config_backup(str(config_file))

        assert result.endswith(".bak1")
        assert Path(result).exists()
