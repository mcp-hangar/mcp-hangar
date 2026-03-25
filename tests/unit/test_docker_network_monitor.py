"""Tests for Docker network monitor: parsers, monitor, and container label injection.

Tests cover:
- parse_proc_net_tcp: hex IP decoding, port extraction, state filtering, loopback filtering
- parse_ss_output: ESTAB line extraction, IPv6 bracket notation, loopback filtering
- DockerNetworkMonitor: poll_connections, ss fallback, caching, error handling
- Container label injection: DockerLauncher and ContainerLauncher --label flags
"""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures: realistic /proc/net/tcp and ss output
# ---------------------------------------------------------------------------

PROC_NET_TCP_HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode"

# Fields: sl local_addr remote_addr state ...
# States: 01=ESTABLISHED, 0A=LISTEN, 06=TIME_WAIT
# IP format: little-endian hex. "0100007F" = 127.0.0.1, "2200A8C0" = 192.168.0.34
# Port format: hex. "01BB" = 443, "0050" = 80

PROC_NET_TCP_ESTABLISHED_TO_EXTERNAL = (
    "   0: 0200A8C0:C350 2234D85E:01BB 01 00000000:00000000 00:00000000 00000000"
    "     0        0 12345 1 0000000000000000 100 0 0 10 0"
)

PROC_NET_TCP_LISTEN = (
    "   1: 00000000:0050 00000000:0000 0A 00000000:00000000 00:00000000 00000000"
    "     0        0 12346 1 0000000000000000 100 0 0 10 0"
)

PROC_NET_TCP_TIME_WAIT = (
    "   2: 0200A8C0:C351 2234D85E:01BB 06 00000000:00000000 00:00000000 00000000"
    "     0        0 12347 1 0000000000000000 100 0 0 10 0"
)

PROC_NET_TCP_LOOPBACK = (
    "   3: 0100007F:C352 0100007F:0035 01 00000000:00000000 00:00000000 00000000"
    "     0        0 12348 1 0000000000000000 100 0 0 10 0"
)

# Second established connection to a different destination: 10.0.0.1:80
# 10.0.0.1 in little-endian = 0100000A
PROC_NET_TCP_ESTABLISHED_SECOND = (
    "   4: 0200A8C0:C353 0100000A:0050 01 00000000:00000000 00:00000000 00000000"
    "     0        0 12349 1 0000000000000000 100 0 0 10 0"
)


SS_OUTPUT_HEADER = "State    Recv-Q Send-Q  Local Address:Port   Peer Address:Port  Process"

SS_OUTPUT_ESTAB = 'ESTAB    0      0      172.17.0.2:45678    93.184.216.34:443   users:(("python",pid=1,fd=5))'

SS_OUTPUT_LISTEN = "LISTEN   0      128    0.0.0.0:8080         0.0.0.0:*"

SS_OUTPUT_LOOPBACK = 'ESTAB    0      0      127.0.0.1:45679     127.0.0.1:5432   users:(("python",pid=1,fd=6))'

SS_OUTPUT_IPV6_LOOPBACK = 'ESTAB    0      0      [::1]:45680         [::1]:5432   users:(("python",pid=1,fd=7))'

SS_OUTPUT_IPV6_MAPPED = (
    'ESTAB    0      0      [::ffff:172.17.0.2]:45681  [::ffff:1.2.3.4]:443   users:(("python",pid=1,fd=8))'
)

SS_OUTPUT_SECOND_ESTAB = 'ESTAB    0      0      172.17.0.2:45682    10.0.0.1:80   users:(("python",pid=1,fd=9))'


# ===========================================================================
# Tests for parse_proc_net_tcp
# ===========================================================================


class TestParseProcNetTcp:
    """Tests for parsing /proc/net/tcp content."""

    def test_established_connection_extracted_with_correct_ip_and_port(self):
        """ESTABLISHED (state 01) connections should be extracted with decoded IP:port."""
        from enterprise.behavioral.proc_net_parser import parse_proc_net_tcp

        content = PROC_NET_TCP_HEADER + "\n" + PROC_NET_TCP_ESTABLISHED_TO_EXTERNAL
        result = parse_proc_net_tcp(content)

        assert len(result) == 1
        host, port, protocol = result[0]
        # 2234D85E little-endian -> 94.216.52.34
        assert host == "94.216.52.34"
        assert port == 443
        assert protocol == "tcp"

    def test_listen_state_is_skipped(self):
        """LISTEN (state 0A) connections should be filtered out."""
        from enterprise.behavioral.proc_net_parser import parse_proc_net_tcp

        content = PROC_NET_TCP_HEADER + "\n" + PROC_NET_TCP_LISTEN
        result = parse_proc_net_tcp(content)

        assert result == []

    def test_time_wait_state_is_skipped(self):
        """TIME_WAIT (state 06) connections should be filtered out."""
        from enterprise.behavioral.proc_net_parser import parse_proc_net_tcp

        content = PROC_NET_TCP_HEADER + "\n" + PROC_NET_TCP_TIME_WAIT
        result = parse_proc_net_tcp(content)

        assert result == []

    def test_loopback_destination_is_filtered_out(self):
        """Connections to 127.x.x.x should be filtered out."""
        from enterprise.behavioral.proc_net_parser import parse_proc_net_tcp

        content = PROC_NET_TCP_HEADER + "\n" + PROC_NET_TCP_LOOPBACK
        result = parse_proc_net_tcp(content)

        assert result == []

    def test_little_endian_hex_ip_decoded_correctly(self):
        """Little-endian hex IP '2200A8C0' should decode to '192.168.0.34'."""
        from enterprise.behavioral.proc_net_parser import parse_proc_net_tcp

        # 2200A8C0 little-endian: bytes C0 A8 00 22 = 192.168.0.34
        # Make an established connection TO 192.168.0.34:80
        line = (
            "   5: 0100000A:C354 2200A8C0:0050 01 00000000:00000000 00:00000000 00000000"
            "     0        0 12350 1 0000000000000000 100 0 0 10 0"
        )
        content = PROC_NET_TCP_HEADER + "\n" + line
        result = parse_proc_net_tcp(content)

        assert len(result) == 1
        host, port, protocol = result[0]
        assert host == "192.168.0.34"
        assert port == 80

    def test_hex_port_decoded_correctly(self):
        """Hex port '01BB' should decode to 443."""
        from enterprise.behavioral.proc_net_parser import parse_proc_net_tcp

        content = PROC_NET_TCP_HEADER + "\n" + PROC_NET_TCP_ESTABLISHED_TO_EXTERNAL
        result = parse_proc_net_tcp(content)

        assert result[0][1] == 443

    def test_empty_input_returns_empty_list(self):
        """Empty string input should return an empty list."""
        from enterprise.behavioral.proc_net_parser import parse_proc_net_tcp

        result = parse_proc_net_tcp("")
        assert result == []

    def test_header_only_input_returns_empty_list(self):
        """Input with only the header line should return an empty list."""
        from enterprise.behavioral.proc_net_parser import parse_proc_net_tcp

        result = parse_proc_net_tcp(PROC_NET_TCP_HEADER)
        assert result == []

    def test_multiple_established_connections(self):
        """Multiple ESTABLISHED connections to non-loopback destinations are all returned."""
        from enterprise.behavioral.proc_net_parser import parse_proc_net_tcp

        content = "\n".join(
            [
                PROC_NET_TCP_HEADER,
                PROC_NET_TCP_ESTABLISHED_TO_EXTERNAL,
                PROC_NET_TCP_LISTEN,
                PROC_NET_TCP_TIME_WAIT,
                PROC_NET_TCP_LOOPBACK,
                PROC_NET_TCP_ESTABLISHED_SECOND,
            ]
        )
        result = parse_proc_net_tcp(content)

        assert len(result) == 2
        hosts = {r[0] for r in result}
        assert "94.216.52.34" in hosts
        assert "10.0.0.1" in hosts


# ===========================================================================
# Tests for parse_ss_output
# ===========================================================================


class TestParseSsOutput:
    """Tests for parsing ss -tnp output."""

    def test_estab_line_extracted_with_correct_host_and_port(self):
        """ESTAB lines should be extracted with correct host:port."""
        from enterprise.behavioral.proc_net_parser import parse_ss_output

        content = SS_OUTPUT_HEADER + "\n" + SS_OUTPUT_ESTAB
        result = parse_ss_output(content)

        assert len(result) == 1
        host, port, protocol = result[0]
        assert host == "93.184.216.34"
        assert port == 443
        assert protocol == "tcp"

    def test_non_estab_lines_are_skipped(self):
        """Non-ESTAB lines (header, LISTEN, etc.) should be filtered out."""
        from enterprise.behavioral.proc_net_parser import parse_ss_output

        content = SS_OUTPUT_HEADER + "\n" + SS_OUTPUT_LISTEN
        result = parse_ss_output(content)

        assert result == []

    def test_loopback_ipv4_destination_filtered_out(self):
        """Connections to 127.0.0.1 should be filtered out."""
        from enterprise.behavioral.proc_net_parser import parse_ss_output

        content = SS_OUTPUT_HEADER + "\n" + SS_OUTPUT_LOOPBACK
        result = parse_ss_output(content)

        assert result == []

    def test_loopback_ipv6_destination_filtered_out(self):
        """Connections to [::1] should be filtered out."""
        from enterprise.behavioral.proc_net_parser import parse_ss_output

        content = SS_OUTPUT_HEADER + "\n" + SS_OUTPUT_IPV6_LOOPBACK
        result = parse_ss_output(content)

        assert result == []

    def test_ipv6_bracket_notation_handled(self):
        """IPv6 bracket notation [::ffff:1.2.3.4]:443 should be parsed correctly."""
        from enterprise.behavioral.proc_net_parser import parse_ss_output

        content = SS_OUTPUT_HEADER + "\n" + SS_OUTPUT_IPV6_MAPPED
        result = parse_ss_output(content)

        assert len(result) == 1
        host, port, protocol = result[0]
        assert host == "::ffff:1.2.3.4"
        assert port == 443

    def test_empty_input_returns_empty_list(self):
        """Empty string input should return an empty list."""
        from enterprise.behavioral.proc_net_parser import parse_ss_output

        result = parse_ss_output("")
        assert result == []

    def test_multiple_estab_connections(self):
        """Multiple ESTAB lines to non-loopback destinations are all returned."""
        from enterprise.behavioral.proc_net_parser import parse_ss_output

        content = "\n".join(
            [
                SS_OUTPUT_HEADER,
                SS_OUTPUT_ESTAB,
                SS_OUTPUT_LISTEN,
                SS_OUTPUT_LOOPBACK,
                SS_OUTPUT_SECOND_ESTAB,
            ]
        )
        result = parse_ss_output(content)

        assert len(result) == 2
        hosts = {r[0] for r in result}
        assert "93.184.216.34" in hosts
        assert "10.0.0.1" in hosts


# ===========================================================================
# Tests for DockerNetworkMonitor
# ===========================================================================


class TestDockerNetworkMonitorPollConnections:
    """Tests for DockerNetworkMonitor.poll_connections."""

    def test_poll_connections_returns_network_observations(self):
        """poll_connections should return NetworkObservation records from container ss output."""
        from enterprise.behavioral.docker_network_monitor import DockerNetworkMonitor

        mock_client = MagicMock()
        monitor = DockerNetworkMonitor.__new__(DockerNetworkMonitor)
        monitor._client = mock_client
        monitor._ss_available = {}

        mock_container = MagicMock()
        mock_container.short_id = "abc123"
        mock_client.containers.list.return_value = [mock_container]

        ss_output = (SS_OUTPUT_HEADER + "\n" + SS_OUTPUT_ESTAB).encode("utf-8")
        mock_container.exec_run.return_value = (0, (ss_output, None))

        results = monitor.poll_connections("my-provider")

        assert len(results) == 1
        obs = results[0]
        assert obs.provider_id == "my-provider"
        assert obs.destination_host == "93.184.216.34"
        assert obs.destination_port == 443
        assert obs.protocol == "tcp"
        assert obs.direction == "outbound"

    def test_poll_connections_finds_container_by_label(self):
        """poll_connections should filter containers by mcp-hangar.provider-id label."""
        from enterprise.behavioral.docker_network_monitor import DockerNetworkMonitor

        mock_client = MagicMock()
        monitor = DockerNetworkMonitor.__new__(DockerNetworkMonitor)
        monitor._client = mock_client
        monitor._ss_available = {}

        mock_client.containers.list.return_value = []

        results = monitor.poll_connections("my-provider")

        mock_client.containers.list.assert_called_once_with(
            filters={"label": "mcp-hangar.provider-id=my-provider", "status": "running"}
        )
        assert results == []

    def test_poll_connections_returns_empty_list_when_no_container(self):
        """poll_connections should return empty list when no container matches."""
        from enterprise.behavioral.docker_network_monitor import DockerNetworkMonitor

        mock_client = MagicMock()
        monitor = DockerNetworkMonitor.__new__(DockerNetworkMonitor)
        monitor._client = mock_client
        monitor._ss_available = {}

        mock_client.containers.list.return_value = []

        results = monitor.poll_connections("missing-provider")
        assert results == []

    def test_poll_connections_returns_empty_list_when_exec_fails(self):
        """poll_connections should return empty list when both ss and /proc/net/tcp exec fail."""
        from enterprise.behavioral.docker_network_monitor import DockerNetworkMonitor

        mock_client = MagicMock()
        monitor = DockerNetworkMonitor.__new__(DockerNetworkMonitor)
        monitor._client = mock_client
        monitor._ss_available = {}

        mock_container = MagicMock()
        mock_container.short_id = "abc123"
        mock_client.containers.list.return_value = [mock_container]

        # Both exec_run calls raise an exception
        mock_container.exec_run.side_effect = Exception("exec failed")

        results = monitor.poll_connections("my-provider")
        assert results == []


class TestDockerNetworkMonitorSsFallback:
    """Tests for ss-to-/proc/net/tcp fallback behavior."""

    def test_falls_back_to_proc_net_tcp_when_ss_fails(self):
        """When ss returns non-zero exit, should fall back to /proc/net/tcp."""
        from enterprise.behavioral.docker_network_monitor import DockerNetworkMonitor

        mock_client = MagicMock()
        monitor = DockerNetworkMonitor.__new__(DockerNetworkMonitor)
        monitor._client = mock_client
        monitor._ss_available = {}

        mock_container = MagicMock()
        mock_container.short_id = "abc123"
        mock_client.containers.list.return_value = [mock_container]

        proc_content = (PROC_NET_TCP_HEADER + "\n" + PROC_NET_TCP_ESTABLISHED_TO_EXTERNAL).encode("utf-8")

        # ss fails with non-zero exit, /proc/net/tcp succeeds
        mock_container.exec_run.side_effect = [
            (1, (None, None)),  # ss fails
            (0, (proc_content, None)),  # /proc/net/tcp succeeds
        ]

        results = monitor.poll_connections("my-provider")

        assert len(results) == 1
        assert results[0].destination_host == "94.216.52.34"
        assert results[0].destination_port == 443

    def test_caches_ss_unavailability_per_container(self):
        """After ss fails once, subsequent polls should skip ss for that container."""
        from enterprise.behavioral.docker_network_monitor import DockerNetworkMonitor

        mock_client = MagicMock()
        monitor = DockerNetworkMonitor.__new__(DockerNetworkMonitor)
        monitor._client = mock_client
        monitor._ss_available = {}

        mock_container = MagicMock()
        mock_container.short_id = "abc123"
        mock_client.containers.list.return_value = [mock_container]

        proc_content = (PROC_NET_TCP_HEADER + "\n" + PROC_NET_TCP_ESTABLISHED_TO_EXTERNAL).encode("utf-8")

        # First call: ss fails, fallback to /proc/net/tcp
        mock_container.exec_run.side_effect = [
            (1, (None, None)),  # ss fails
            (0, (proc_content, None)),  # /proc/net/tcp
        ]
        monitor.poll_connections("my-provider")

        # ss should now be cached as unavailable for this container
        assert monitor._ss_available.get("abc123") is False

        # Second call: should skip ss entirely and go directly to /proc/net/tcp
        mock_container.exec_run.reset_mock()
        mock_container.exec_run.side_effect = [
            (0, (proc_content, None)),  # Only /proc/net/tcp
        ]
        results = monitor.poll_connections("my-provider")

        assert len(results) == 1
        # Should have called exec_run only once (skipping ss)
        assert mock_container.exec_run.call_count == 1

    def test_caches_ss_availability_when_ss_succeeds(self):
        """When ss succeeds, the cache should mark it as available."""
        from enterprise.behavioral.docker_network_monitor import DockerNetworkMonitor

        mock_client = MagicMock()
        monitor = DockerNetworkMonitor.__new__(DockerNetworkMonitor)
        monitor._client = mock_client
        monitor._ss_available = {}

        mock_container = MagicMock()
        mock_container.short_id = "abc123"
        mock_client.containers.list.return_value = [mock_container]

        ss_output = (SS_OUTPUT_HEADER + "\n" + SS_OUTPUT_ESTAB).encode("utf-8")
        mock_container.exec_run.return_value = (0, (ss_output, None))

        monitor.poll_connections("my-provider")

        assert monitor._ss_available.get("abc123") is True


# ===========================================================================
# Tests for container label injection
# ===========================================================================


class TestDockerLauncherLabelInjection:
    """Tests for DockerLauncher --label mcp-hangar.provider-id injection."""

    def test_build_docker_command_includes_provider_id_label(self):
        """_build_docker_command should include --label mcp-hangar.provider-id=X."""
        from src.mcp_hangar.domain.services.provider_launcher.docker import DockerLauncher

        launcher = DockerLauncher(runtime="docker")
        cmd = launcher._build_docker_command("myimage:latest", provider_id="test-provider")

        assert "--label" in cmd
        label_idx = cmd.index("--label")
        assert cmd[label_idx + 1] == "mcp-hangar.provider-id=test-provider"

    def test_build_docker_command_omits_label_when_no_provider_id(self):
        """_build_docker_command should NOT add --label when provider_id is None."""
        from src.mcp_hangar.domain.services.provider_launcher.docker import DockerLauncher

        launcher = DockerLauncher(runtime="docker")
        cmd = launcher._build_docker_command("myimage:latest")

        label_values = [cmd[i + 1] for i, v in enumerate(cmd) if v == "--label"]
        provider_labels = [lv for lv in label_values if "mcp-hangar.provider-id" in lv]
        assert provider_labels == []

    def test_label_appears_before_image_in_command(self):
        """The --label flag should appear before the image name in the command."""
        from src.mcp_hangar.domain.services.provider_launcher.docker import DockerLauncher

        launcher = DockerLauncher(runtime="docker")
        cmd = launcher._build_docker_command("myimage:latest", provider_id="test-provider")

        label_idx = cmd.index("--label")
        image_idx = cmd.index("myimage:latest")
        assert label_idx < image_idx


class TestContainerLauncherLabelInjection:
    """Tests for ContainerLauncher --label mcp-hangar.provider-id injection."""

    def test_build_command_includes_provider_id_label(self):
        """_build_command should include --label mcp-hangar.provider-id=X."""
        from src.mcp_hangar.domain.services.provider_launcher.container import (
            ContainerConfig,
            ContainerLauncher,
        )

        launcher = ContainerLauncher.__new__(ContainerLauncher)
        launcher._runtime = "docker"
        launcher._sanitizer = MagicMock()
        launcher._sanitizer.sanitize_environment_value.side_effect = lambda v: v

        config = ContainerConfig(image="myimage:latest", provider_id="test-provider")
        cmd = launcher._build_command(config)

        assert "--label" in cmd
        label_idx = cmd.index("--label")
        assert cmd[label_idx + 1] == "mcp-hangar.provider-id=test-provider"

    def test_build_command_omits_label_when_no_provider_id(self):
        """_build_command should NOT add --label when provider_id is None."""
        from src.mcp_hangar.domain.services.provider_launcher.container import (
            ContainerConfig,
            ContainerLauncher,
        )

        launcher = ContainerLauncher.__new__(ContainerLauncher)
        launcher._runtime = "docker"
        launcher._sanitizer = MagicMock()
        launcher._sanitizer.sanitize_environment_value.side_effect = lambda v: v

        config = ContainerConfig(image="myimage:latest")
        cmd = launcher._build_command(config)

        label_values = [cmd[i + 1] for i, v in enumerate(cmd) if v == "--label"]
        provider_labels = [lv for lv in label_values if "mcp-hangar.provider-id" in lv]
        assert provider_labels == []

    def test_label_appears_before_image_in_command(self):
        """The --label flag should appear before the image name in the command."""
        from src.mcp_hangar.domain.services.provider_launcher.container import (
            ContainerConfig,
            ContainerLauncher,
        )

        launcher = ContainerLauncher.__new__(ContainerLauncher)
        launcher._runtime = "docker"
        launcher._sanitizer = MagicMock()
        launcher._sanitizer.sanitize_environment_value.side_effect = lambda v: v

        config = ContainerConfig(image="myimage:latest", provider_id="test-provider")
        cmd = launcher._build_command(config)

        label_idx = cmd.index("--label")
        image_idx = cmd.index("myimage:latest")
        assert label_idx < image_idx


class TestProviderLaunchConfigIncludesProviderId:
    """Tests for Provider._get_launch_config including provider_id."""

    def test_docker_mode_includes_provider_id(self):
        """_get_launch_config for DOCKER mode should include provider_id."""
        from src.mcp_hangar.domain.model.provider import Provider

        provider = Provider(
            provider_id="my-docker-provider",
            mode="docker",
            image="myimage:latest",
        )

        config = provider._get_launch_config()
        assert config.get("provider_id") == "my-docker-provider"

    def test_container_mode_includes_provider_id(self):
        """_get_launch_config for container mode should include provider_id."""
        from src.mcp_hangar.domain.model.provider import Provider

        provider = Provider(
            provider_id="my-container-provider",
            mode="container",
            image="myimage:latest",
        )

        config = provider._get_launch_config()
        assert config.get("provider_id") == "my-container-provider"
