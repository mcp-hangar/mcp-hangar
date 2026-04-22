"""Unit tests for config serializer module.

Tests cover ProviderGroup.to_config_dict() and all functions in
mcp_hangar.server.config_serializer.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from mcp_hangar.domain.model.mcp_server_group import McpServerGroup
from mcp_hangar.domain.value_objects import LoadBalancerStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_provider(mcp_server_id: str, config_dict: dict[str, object] | None = None) -> MagicMock:
    """Create a mock Provider with to_config_dict support."""
    mock = MagicMock()
    mock.id = mcp_server_id
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
        group = McpServerGroup("g")
        assert group.to_config_dict()["mode"] == "group"

    def test_group_config_dict_has_strategy(self):
        """to_config_dict() should include strategy value."""
        group = McpServerGroup("g", strategy=LoadBalancerStrategy.ROUND_ROBIN)
        assert group.to_config_dict()["strategy"] == "round_robin"

    def test_group_config_dict_has_min_healthy(self):
        """to_config_dict() should include min_healthy."""
        group = McpServerGroup("g", min_healthy=2)
        assert group.to_config_dict()["min_healthy"] == 2

    def test_group_config_dict_has_members(self):
        """to_config_dict() should include members list after add_member()."""
        group = McpServerGroup("g", auto_start=False)
        provider = _make_mock_provider("p1")
        group.add_member(provider, weight=1, priority=1)
        result = group.to_config_dict()
        assert result["members"] == [{"id": "p1", "weight": 1, "priority": 1}]

    def test_description_included_when_set(self):
        """to_config_dict() should include description when set."""
        group = McpServerGroup("g", description="desc")
        assert group.to_config_dict()["description"] == "desc"

    def test_description_omitted_when_none(self):
        """to_config_dict() should not include description key when None."""
        group = McpServerGroup("g")
        assert "description" not in group.to_config_dict()

    def test_auto_start_included(self):
        """to_config_dict() should include auto_start."""
        group = McpServerGroup("g", auto_start=False)
        assert group.to_config_dict()["auto_start"] is False


# ---------------------------------------------------------------------------
# TestSerializeProviders
# ---------------------------------------------------------------------------


class TestSerializeProviders:
    """Tests for serialize_providers()."""

    def test_returns_empty_dict_when_no_providers(self):
        """serialize_providers() should return {} when repository is empty."""
        from mcp_hangar.server.config_serializer import serialize_mcp_servers

        mock_ctx = MagicMock()
        mock_ctx.repository.get_all.return_value = {}

        result = serialize_mcp_servers(mcp_servers={})
        assert result == {}

    def test_returns_provider_config_dicts(self):
        """serialize_providers() should return {id: to_config_dict()} for each provider."""
        from mcp_hangar.server.config_serializer import serialize_mcp_servers

        provider = _make_mock_provider("p", {"mode": "subprocess", "command": ["python", "-m", "server"]})
        result = serialize_mcp_servers(mcp_servers={"p": provider})
        assert result == {"p": {"mode": "subprocess", "command": ["python", "-m", "server"]}}

    def test_uses_to_config_dict(self):
        """serialize_providers() should call to_config_dict() for each provider."""
        from mcp_hangar.server.config_serializer import serialize_mcp_servers

        provider = _make_mock_provider("p")
        serialize_mcp_servers(mcp_servers={"p": provider})
        provider.to_config_dict.assert_called_once()

    def test_fetches_from_context_when_no_providers_arg(self):
        """serialize_providers() should call get_context() when providers arg is None."""
        from mcp_hangar.server.config_serializer import serialize_mcp_servers

        mock_ctx = MagicMock()
        provider = _make_mock_provider("ctx_p", {"mode": "subprocess"})
        mock_ctx.repository.get_all.return_value = {"ctx_p": provider}

        with patch("mcp_hangar.server.config_serializer.get_context", return_value=mock_ctx):
            result = serialize_mcp_servers()

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
        """serialize_full_config() result should have 'mcp_servers' key."""
        from mcp_hangar.server.config_serializer import serialize_full_config

        result = serialize_full_config(mcp_servers={}, groups={})
        assert "mcp_servers" in result

    def test_providers_and_groups_merged(self):
        """serialize_full_config() result['mcp_servers'] should contain both providers and groups."""
        from mcp_hangar.server.config_serializer import serialize_full_config

        provider = _make_mock_provider("p", {"mode": "subprocess"})
        mock_group = MagicMock()
        mock_group.to_config_dict.return_value = {"mode": "group", "members": []}

        result = serialize_full_config(mcp_servers={"p": provider}, groups={"g": mock_group})
        assert "p" in result["mcp_servers"]
        assert "g" in result["mcp_servers"]

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

        result = serialize_full_config(mcp_servers={"p": provider}, groups={"g": mock_group})
        dumped = yaml.safe_dump(result, default_flow_style=False, sort_keys=True, allow_unicode=True)
        assert "mcp_servers" in dumped

    def test_uses_context_when_no_args(self):
        """serialize_full_config() should call get_context() when no args given."""
        from mcp_hangar.server.config_serializer import serialize_full_config

        mock_ctx = MagicMock()
        mock_ctx.repository.get_all.return_value = {}
        mock_ctx.groups = {}

        with patch("mcp_hangar.server.config_serializer.get_context", return_value=mock_ctx):
            result = serialize_full_config()

        assert "mcp_servers" in result


# ---------------------------------------------------------------------------
# TestWriteConfigBackup
# ---------------------------------------------------------------------------


class TestWriteConfigBackup:
    """Tests for write_config_backup()."""

    def test_creates_bak1_file(self, tmp_path: Path):
        """write_config_backup() should create a .bak1 file."""
        from mcp_hangar.server.config_serializer import write_config_backup

        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("mcp_servers: {}")

        with patch("mcp_hangar.server.config_serializer.serialize_full_config", return_value={"mcp_servers": {}}):
            write_config_backup(str(config_file))

        assert (tmp_path / "test_config.yaml.bak1").exists()

    def test_bak1_contains_yaml(self, tmp_path: Path):
        """bak1 content should be parseable by yaml.safe_load()."""
        from mcp_hangar.server.config_serializer import write_config_backup

        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("mcp_servers: {}")

        with patch(
            "mcp_hangar.server.config_serializer.serialize_full_config",
            return_value={"mcp_servers": {"p": {"mode": "subprocess"}}},
        ):
            write_config_backup(str(config_file))

        bak1 = tmp_path / "test_config.yaml.bak1"
        parsed = yaml.safe_load(bak1.read_text())
        assert isinstance(parsed, dict)
        assert "mcp_servers" in parsed

    def test_rotation_shifts_bak1_to_bak2(self, tmp_path: Path):
        """If bak1 exists before backup, after backup bak2 should have old bak1 content."""
        from mcp_hangar.server.config_serializer import write_config_backup

        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("mcp_servers: {}")

        # Create a pre-existing bak1 with known content
        old_bak1 = tmp_path / "test_config.yaml.bak1"
        old_bak1.write_text("old_bak1_content: true")

        with patch("mcp_hangar.server.config_serializer.serialize_full_config", return_value={"mcp_servers": {}}):
            write_config_backup(str(config_file))

        bak2 = tmp_path / "test_config.yaml.bak2"
        assert bak2.exists()
        content = yaml.safe_load(bak2.read_text())
        assert content == {"old_bak1_content": True}

    def test_rotation_drops_bak5(self, tmp_path: Path):
        """If bak5 exists (full rotation), bak5 is overwritten with old bak4 content."""
        from mcp_hangar.server.config_serializer import write_config_backup

        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("mcp_servers: {}")

        # Set up full chain: bak1..bak5
        for i in range(1, 6):
            bak = tmp_path / f"test_config.yaml.bak{i}"
            bak.write_text(f"slot: bak{i}")

        with patch("mcp_hangar.server.config_serializer.serialize_full_config", return_value={"mcp_servers": {}}):
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
        config_file.write_text("mcp_servers: {}")

        with patch("mcp_hangar.server.config_serializer.serialize_full_config", return_value={"mcp_servers": {}}):
            result = write_config_backup(str(config_file))

        assert result.endswith(".bak1")
        assert Path(result).exists()


# ---------------------------------------------------------------------------
# TestSerializeExecutionConfig
# ---------------------------------------------------------------------------


class TestSerializeExecutionConfig:
    """Tests for serialize_execution_config()."""

    def test_returns_empty_dict_when_limits_are_zero(self):
        """serialize_execution_config() should return {} when all limits are 0 (unlimited)."""
        from mcp_hangar.server.config_serializer import serialize_execution_config

        mock_manager = MagicMock()
        mock_manager.global_limit = 0
        mock_manager.default_mcp_server_limit = 0

        with patch(
            "mcp_hangar.server.tools.batch.concurrency.get_concurrency_manager",
            return_value=mock_manager,
        ):
            result = serialize_execution_config()

        assert result == {}

    def test_includes_global_limit_when_nonzero(self):
        """serialize_execution_config() should include max_concurrency when > 0."""
        from mcp_hangar.server.config_serializer import serialize_execution_config

        mock_manager = MagicMock()
        mock_manager.global_limit = 100
        mock_manager.default_mcp_server_limit = 0

        with patch(
            "mcp_hangar.server.tools.batch.concurrency.get_concurrency_manager",
            return_value=mock_manager,
        ):
            result = serialize_execution_config()

        assert result["max_concurrency"] == 100
        assert "default_mcp_server_concurrency" not in result

    def test_includes_default_mcp_server_limit_when_nonzero(self):
        """serialize_execution_config() should include default_mcp_server_concurrency when > 0."""
        from mcp_hangar.server.config_serializer import serialize_execution_config

        mock_manager = MagicMock()
        mock_manager.global_limit = 0
        mock_manager.default_mcp_server_limit = 20

        with patch(
            "mcp_hangar.server.tools.batch.concurrency.get_concurrency_manager",
            return_value=mock_manager,
        ):
            result = serialize_execution_config()

        assert result["default_mcp_server_concurrency"] == 20
        assert "max_concurrency" not in result

    def test_returns_empty_dict_on_exception(self):
        """serialize_execution_config() should return {} if concurrency manager raises."""
        from mcp_hangar.server.config_serializer import serialize_execution_config

        with patch(
            "mcp_hangar.server.tools.batch.concurrency.get_concurrency_manager",
            side_effect=RuntimeError("unavailable"),
        ):
            result = serialize_execution_config()

        assert result == {}


class TestSerializeFullConfigExtended:
    """Tests for the extended serialize_full_config() with passthrough sections."""

    def test_execution_section_included_when_nonzero_limits(self):
        """serialize_full_config() should include execution section if limits are nonzero."""
        from mcp_hangar.server.config_serializer import serialize_full_config

        mock_manager = MagicMock()
        mock_manager.global_limit = 50
        mock_manager.default_mcp_server_limit = 10

        with patch(
            "mcp_hangar.server.tools.batch.concurrency.get_concurrency_manager",
            return_value=mock_manager,
        ):
            result = serialize_full_config(mcp_servers={}, groups={})

        assert "execution" in result
        assert result["execution"]["max_concurrency"] == 50
        assert result["execution"]["default_mcp_server_concurrency"] == 10

    def test_execution_section_omitted_when_zero_limits(self):
        """serialize_full_config() should omit execution section if all limits are 0."""
        from mcp_hangar.server.config_serializer import serialize_full_config

        mock_manager = MagicMock()
        mock_manager.global_limit = 0
        mock_manager.default_mcp_server_limit = 0

        with patch(
            "mcp_hangar.server.tools.batch.concurrency.get_concurrency_manager",
            return_value=mock_manager,
        ):
            result = serialize_full_config(mcp_servers={}, groups={})

        assert "execution" not in result

    def test_passthrough_sections_included_from_stored_full_config(self):
        """serialize_full_config() should include event_store/auth from stored full_config."""
        from mcp_hangar.server.config_serializer import serialize_full_config

        mock_ctx = MagicMock()
        mock_ctx.repository.get_all.return_value = {}
        mock_ctx.groups = {}
        mock_ctx.full_config = {
            "mcp_servers": {},
            "event_store": {"enabled": True, "driver": "sqlite", "path": "data/events.db"},
            "auth": {"enabled": True},
        }

        mock_manager = MagicMock()
        mock_manager.global_limit = 0
        mock_manager.default_mcp_server_limit = 0

        with (
            patch("mcp_hangar.server.config_serializer.get_context", return_value=mock_ctx),
            patch(
                "mcp_hangar.server.tools.batch.concurrency.get_concurrency_manager",
                return_value=mock_manager,
            ),
        ):
            result = serialize_full_config(mcp_servers={}, groups={})

        assert "event_store" in result
        assert result["event_store"]["driver"] == "sqlite"
        assert "auth" in result
        assert result["auth"]["enabled"] is True

    def test_passthrough_does_not_override_providers_section(self):
        """Passthrough from full_config must not overwrite the serialized providers key."""
        from mcp_hangar.server.config_serializer import serialize_full_config

        provider = _make_mock_provider("p", {"mode": "subprocess"})
        mock_ctx = MagicMock()
        mock_ctx.full_config = {"mcp_servers": {"old": {"mode": "subprocess"}}}

        mock_manager = MagicMock()
        mock_manager.global_limit = 0
        mock_manager.default_mcp_server_limit = 0

        with (
            patch("mcp_hangar.server.config_serializer.get_context", return_value=mock_ctx),
            patch(
                "mcp_hangar.server.tools.batch.concurrency.get_concurrency_manager",
                return_value=mock_manager,
            ),
        ):
            result = serialize_full_config(mcp_servers={"p": provider}, groups={})

        # "p" should be present (from explicit arg), "old" should NOT be overwritten back
        assert "p" in result["mcp_servers"]
        assert "old" not in result["mcp_servers"]

    def test_passthrough_ignores_unknown_sections(self):
        """Passthrough should only include known safe keys, not arbitrary config data."""
        from mcp_hangar.server.config_serializer import serialize_full_config

        mock_ctx = MagicMock()
        mock_ctx.full_config = {"mcp_servers": {}, "custom_plugin": {"secret": "val"}}

        mock_manager = MagicMock()
        mock_manager.global_limit = 0
        mock_manager.default_mcp_server_limit = 0

        with (
            patch("mcp_hangar.server.config_serializer.get_context", return_value=mock_ctx),
            patch(
                "mcp_hangar.server.tools.batch.concurrency.get_concurrency_manager",
                return_value=mock_manager,
            ),
        ):
            result = serialize_full_config(mcp_servers={}, groups={})

        assert "custom_plugin" not in result

    def test_context_failure_does_not_break_serialization(self):
        """Passthrough silently skips if get_context() raises."""
        from mcp_hangar.server.config_serializer import serialize_full_config

        mock_manager = MagicMock()
        mock_manager.global_limit = 0
        mock_manager.default_mcp_server_limit = 0

        with (
            patch(
                "mcp_hangar.server.config_serializer.get_context",
                side_effect=RuntimeError("no context"),
            ),
            patch(
                "mcp_hangar.server.tools.batch.concurrency.get_concurrency_manager",
                return_value=mock_manager,
            ),
        ):
            result = serialize_full_config(mcp_servers={}, groups={})

        assert "mcp_servers" in result


# ---------------------------------------------------------------------------
# TestWriteConfigBackupSnapshot (Plan 03)
# ---------------------------------------------------------------------------


class TestWriteConfigBackupSnapshot:
    """Tests for snapshot metadata embedded in write_config_backup() output."""

    def test_bak1_contains_snapshot_metadata_key(self, tmp_path: Path):
        """Backup file should contain __snapshot__ key with metadata."""
        from mcp_hangar.server.config_serializer import write_config_backup

        config_file = tmp_path / "cfg.yaml"
        config_file.write_text("mcp_servers: {}")

        with patch(
            "mcp_hangar.server.config_serializer.serialize_full_config",
            return_value={"mcp_servers": {}},
        ):
            write_config_backup(str(config_file))

        bak1 = tmp_path / "cfg.yaml.bak1"
        parsed = yaml.safe_load(bak1.read_text())
        assert "__snapshot__" in parsed

    def test_snapshot_metadata_has_timestamp(self, tmp_path: Path):
        """__snapshot__ block should contain an ISO 8601 timestamp string."""
        from mcp_hangar.server.config_serializer import write_config_backup

        config_file = tmp_path / "cfg.yaml"
        config_file.write_text("mcp_servers: {}")

        with patch(
            "mcp_hangar.server.config_serializer.serialize_full_config",
            return_value={"mcp_servers": {}},
        ):
            write_config_backup(str(config_file))

        bak1 = tmp_path / "cfg.yaml.bak1"
        parsed = yaml.safe_load(bak1.read_text())
        snapshot = parsed["__snapshot__"]
        assert "timestamp" in snapshot
        assert isinstance(snapshot["timestamp"], str)
        # ISO 8601 UTC strings always contain 'T' and end with '+00:00' or 'Z'
        assert "T" in snapshot["timestamp"]

    def test_snapshot_metadata_has_provider_count(self, tmp_path: Path):
        """__snapshot__ block should contain provider_count matching actual providers."""
        from mcp_hangar.server.config_serializer import write_config_backup

        config_file = tmp_path / "cfg.yaml"
        config_file.write_text("mcp_servers: {}")

        config_dict = {
            "mcp_servers": {
                "p1": {"mode": "subprocess", "command": ["python"]},
                "p2": {"mode": "docker", "image": "img"},
            }
        }

        with patch(
            "mcp_hangar.server.config_serializer.serialize_full_config",
            return_value=config_dict,
        ):
            write_config_backup(str(config_file))

        bak1 = tmp_path / "cfg.yaml.bak1"
        parsed = yaml.safe_load(bak1.read_text())
        assert parsed["__snapshot__"]["mcp_server_count"] == 2
        assert parsed["__snapshot__"]["group_count"] == 0

    def test_snapshot_metadata_counts_groups_separately(self, tmp_path: Path):
        """__snapshot__ block should count groups distinct from providers."""
        from mcp_hangar.server.config_serializer import write_config_backup

        config_file = tmp_path / "cfg.yaml"
        config_file.write_text("mcp_servers: {}")

        config_dict = {
            "mcp_servers": {
                "p1": {"mode": "subprocess", "command": ["python"]},
                "g1": {"mode": "group", "members": []},
            }
        }

        with patch(
            "mcp_hangar.server.config_serializer.serialize_full_config",
            return_value=config_dict,
        ):
            write_config_backup(str(config_file))

        bak1 = tmp_path / "cfg.yaml.bak1"
        parsed = yaml.safe_load(bak1.read_text())
        assert parsed["__snapshot__"]["mcp_server_count"] == 1
        assert parsed["__snapshot__"]["group_count"] == 1

    def test_full_rotation_chain_integrity(self, tmp_path: Path):
        """After 6 successive backups the chain should hold bak1=newest, bak5=5th oldest."""
        from mcp_hangar.server.config_serializer import write_config_backup

        config_file = tmp_path / "cfg.yaml"
        config_file.write_text("mcp_servers: {}")

        # Write 6 backups, each with a distinct sentinel value so we can trace provenance
        for i in range(1, 7):
            with patch(
                "mcp_hangar.server.config_serializer.serialize_full_config",
                return_value={"mcp_servers": {}, "_sentinel": i},
            ):
                write_config_backup(str(config_file))

        # After 6 writes:
        # bak1 = write 6 (newest)
        # bak2 = write 5
        # bak3 = write 4
        # bak4 = write 3
        # bak5 = write 2 (oldest retained; write 1 is dropped)
        for slot, expected_sentinel in [(1, 6), (2, 5), (3, 4), (4, 3), (5, 2)]:
            bak = tmp_path / f"cfg.yaml.bak{slot}"
            assert bak.exists(), f"bak{slot} should exist"
            parsed = yaml.safe_load(bak.read_text())
            assert parsed.get("_sentinel") == expected_sentinel, (
                f"bak{slot} should have sentinel {expected_sentinel}, got {parsed.get('_sentinel')}"
            )

    def test_rotation_never_creates_bak6(self, tmp_path: Path):
        """write_config_backup() should never create a .bak6 file."""
        from mcp_hangar.server.config_serializer import write_config_backup

        config_file = tmp_path / "cfg.yaml"
        config_file.write_text("mcp_servers: {}")

        for _ in range(10):
            with patch(
                "mcp_hangar.server.config_serializer.serialize_full_config",
                return_value={"mcp_servers": {}},
            ):
                write_config_backup(str(config_file))

        bak6 = tmp_path / "cfg.yaml.bak6"
        assert not bak6.exists(), "bak6 must never be created"

    def test_snapshot_key_absent_from_legacy_backup_promoted_to_bak2(self, tmp_path: Path):
        """Old bak1 (without __snapshot__) is correctly promoted to bak2 on next backup."""
        from mcp_hangar.server.config_serializer import write_config_backup

        config_file = tmp_path / "cfg.yaml"
        config_file.write_text("mcp_servers: {}")

        # Simulate a legacy backup without __snapshot__
        legacy_content = yaml.safe_dump({"mcp_servers": {"legacy": {"mode": "subprocess"}}})
        (tmp_path / "cfg.yaml.bak1").write_text(legacy_content)

        with patch(
            "mcp_hangar.server.config_serializer.serialize_full_config",
            return_value={"mcp_servers": {"new_p": {"mode": "subprocess"}}},
        ):
            write_config_backup(str(config_file))

        bak2 = tmp_path / "cfg.yaml.bak2"
        parsed = yaml.safe_load(bak2.read_text())
        assert "legacy" in parsed["mcp_servers"]
        # Legacy backup promoted to bak2 -- no __snapshot__ key expected there
        assert "__snapshot__" not in parsed
