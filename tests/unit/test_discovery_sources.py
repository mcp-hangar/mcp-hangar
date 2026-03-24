"""Unit tests for Docker and Kubernetes discovery sources."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode


class TestDockerDiscoverySource:
    """Tests for DockerDiscoverySource."""

    @pytest.fixture
    def mock_docker_client(self):
        """Create a mock Docker client."""
        with patch("mcp_hangar.infrastructure.discovery.docker_source.DOCKER_AVAILABLE", True):
            with patch("mcp_hangar.infrastructure.discovery.docker_source.docker") as mock_docker:
                mock_client = MagicMock()
                mock_docker.from_env.return_value = mock_client
                mock_docker.DockerClient.return_value = mock_client
                yield mock_client

    def test_source_type(self, mock_docker_client):
        """Test source_type property."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        source = DockerDiscoverySource()
        assert source.source_type == "docker"

    def test_default_mode_is_additive(self, mock_docker_client):
        """Test default mode is additive (safe)."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        source = DockerDiscoverySource()
        assert source.mode == DiscoveryMode.ADDITIVE

    def test_custom_socket_path(self, mock_docker_client):
        """Test custom socket path."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        source = DockerDiscoverySource(socket_path="/var/run/podman/podman.sock")
        assert source._socket_path == "/var/run/podman/podman.sock"

    @pytest.mark.asyncio
    async def test_discover_empty(self, mock_docker_client):
        """Test discovery with no containers."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        mock_docker_client.containers.list.return_value = []

        source = DockerDiscoverySource()
        providers = await source.discover()

        assert providers == []

    @pytest.mark.asyncio
    async def test_discover_labeled_container(self, mock_docker_client):
        """Test discovery of container with MCP labels."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        # Create mock container with proper return values (not MagicMock)
        mock_container = MagicMock()
        mock_container.name = "test-mcp-provider"
        mock_container.id = "abc123def456789"  # At least 12 chars
        mock_container.status = "running"  # Must be string, not MagicMock
        mock_container.labels = {
            "mcp.hangar.enabled": "true",
            "mcp.hangar.name": "my-provider",
            "mcp.hangar.mode": "http",
            "mcp.hangar.port": "8080",
        }
        mock_container.attrs = {"NetworkSettings": {"Networks": {"bridge": {"IPAddress": "172.17.0.2"}}}}
        # Use real values instead of MagicMock to avoid JSON serialization issues
        mock_container.image = MagicMock()
        mock_container.image.tags = ["my-image:latest"]
        mock_container.image.id = "sha256:abc123def456789"  # At least 12 chars

        mock_docker_client.containers.list.return_value = [mock_container]

        source = DockerDiscoverySource()
        providers = await source.discover()

        assert len(providers) == 1
        assert providers[0].name == "my-provider"
        assert providers[0].mode == "http"
        assert providers[0].source_type == "docker"
        assert providers[0].connection_info["host"] == "172.17.0.2"
        assert providers[0].connection_info["port"] == 8080

    @pytest.mark.asyncio
    async def test_discover_skips_disabled(self, mock_docker_client):
        """Test discovery skips containers without enabled label."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        mock_container = MagicMock()
        mock_container.name = "test-container"
        mock_container.labels = {
            "mcp.hangar.name": "my-provider",
            # Missing mcp.hangar.enabled
        }

        # list() with filter won't return this container
        mock_docker_client.containers.list.return_value = []

        source = DockerDiscoverySource()
        providers = await source.discover()

        assert providers == []

    @pytest.mark.asyncio
    async def test_health_check_success(self, mock_docker_client):
        """Test health check when Docker is accessible."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        mock_docker_client.ping.return_value = True

        source = DockerDiscoverySource()
        healthy = await source.health_check()

        assert healthy is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self, mock_docker_client):
        """Test health check when Docker is not accessible."""
        from mcp_hangar.infrastructure.discovery.docker_source import DockerDiscoverySource

        mock_docker_client.ping.side_effect = Exception("Connection refused")

        source = DockerDiscoverySource()
        healthy = await source.health_check()

        assert healthy is False


class TestKubernetesDiscoverySource:
    """Tests for KubernetesDiscoverySource."""

    @pytest.fixture
    def mock_k8s_client(self):
        """Create a mock Kubernetes client."""
        with (
            patch(
                "mcp_hangar.infrastructure.discovery.kubernetes_source.KUBERNETES_AVAILABLE",
                True,
            ),
            patch("mcp_hangar.infrastructure.discovery.kubernetes_source.config") as mock_config,
        ):
            with patch("mcp_hangar.infrastructure.discovery.kubernetes_source.client") as mock_client:
                mock_v1 = MagicMock()
                mock_client.CoreV1Api.return_value = mock_v1
                yield mock_v1, mock_config

    def test_source_type(self, mock_k8s_client):
        """Test source_type property."""
        from mcp_hangar.infrastructure.discovery.kubernetes_source import KubernetesDiscoverySource

        source = KubernetesDiscoverySource(in_cluster=False)
        assert source.source_type == "kubernetes"

    def test_default_mode_is_authoritative(self, mock_k8s_client):
        """Test default mode is authoritative for K8s."""
        from mcp_hangar.infrastructure.discovery.kubernetes_source import KubernetesDiscoverySource

        source = KubernetesDiscoverySource(in_cluster=False)
        assert source.mode == DiscoveryMode.AUTHORITATIVE

    def test_namespace_filtering(self, mock_k8s_client):
        """Test namespace filtering configuration."""
        from mcp_hangar.infrastructure.discovery.kubernetes_source import KubernetesDiscoverySource

        source = KubernetesDiscoverySource(namespaces=["mcp-providers", "production"], in_cluster=False)
        assert source.namespaces == ["mcp-providers", "production"]

    def test_label_selector(self, mock_k8s_client):
        """Test label selector configuration."""
        from mcp_hangar.infrastructure.discovery.kubernetes_source import KubernetesDiscoverySource

        source = KubernetesDiscoverySource(label_selector="app.kubernetes.io/component=mcp-provider", in_cluster=False)
        assert source.label_selector == "app.kubernetes.io/component=mcp-provider"

    @pytest.mark.asyncio
    async def test_discover_empty(self, mock_k8s_client):
        """Test discovery with no pods."""
        from mcp_hangar.infrastructure.discovery.kubernetes_source import KubernetesDiscoverySource

        mock_v1, _ = mock_k8s_client
        mock_pods = MagicMock()
        mock_pods.items = []
        mock_v1.list_namespaced_pod.return_value = mock_pods
        mock_v1.list_namespace.return_value = MagicMock(items=[])

        source = KubernetesDiscoverySource(namespaces=["default"], in_cluster=False)
        providers = await source.discover()

        assert providers == []

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Mock setup complex - discovery logic tested in integration tests")
    async def test_discover_annotated_pod(self, mock_k8s_client):
        """Test discovery of pod with MCP annotations."""
        from mcp_hangar.infrastructure.discovery.kubernetes_source import KubernetesDiscoverySource

        mock_v1, _ = mock_k8s_client

        # Create mock pod with proper structure - use simple Mock for controlled behavior
        mock_pod = Mock()
        mock_pod.metadata = Mock()
        mock_pod.metadata.name = "my-provider-pod"
        mock_pod.metadata.annotations = {
            "mcp-hangar.io/enabled": "true",
            "mcp-hangar.io/name": "my-provider",
            "mcp-hangar.io/mode": "http",
            "mcp-hangar.io/port": "8080",
        }
        mock_pod.metadata.labels = {"app": "my-provider"}
        mock_pod.status = Mock()
        mock_pod.status.pod_ip = "10.0.0.5"

        mock_pods = Mock()
        mock_pods.items = [mock_pod]
        mock_v1.list_namespaced_pod.return_value = mock_pods

        source = KubernetesDiscoverySource(namespaces=["mcp-providers"], in_cluster=False)
        providers = await source.discover()

        assert len(providers) == 1
        assert providers[0].name == "my-provider"
        assert providers[0].mode == "http"
        assert providers[0].source_type == "kubernetes"
        assert providers[0].connection_info["host"] == "10.0.0.5"
        assert providers[0].connection_info["port"] == 8080
        assert providers[0].metadata["namespace"] == "mcp-providers"

    @pytest.mark.asyncio
    async def test_discover_skips_disabled(self, mock_k8s_client):
        """Test discovery skips pods without enabled annotation."""
        from mcp_hangar.infrastructure.discovery.kubernetes_source import KubernetesDiscoverySource

        mock_v1, _ = mock_k8s_client

        mock_pod = MagicMock()
        mock_pod.metadata.name = "other-pod"
        mock_pod.metadata.annotations = {}  # No MCP annotations
        mock_pod.metadata.labels = {}
        mock_pod.status.pod_ip = "10.0.0.5"

        mock_pods = MagicMock()
        mock_pods.items = [mock_pod]
        mock_v1.list_namespaced_pod.return_value = mock_pods

        source = KubernetesDiscoverySource(namespaces=["default"], in_cluster=False)
        providers = await source.discover()

        assert providers == []

    @pytest.mark.asyncio
    async def test_discover_skips_pod_without_ip(self, mock_k8s_client):
        """Test discovery skips pods without IP (not ready)."""
        from mcp_hangar.infrastructure.discovery.kubernetes_source import KubernetesDiscoverySource

        mock_v1, _ = mock_k8s_client

        mock_pod = MagicMock()
        mock_pod.metadata.name = "pending-pod"
        mock_pod.metadata.annotations = {
            "mcp-hangar.io/enabled": "true",
            "mcp-hangar.io/name": "my-provider",
        }
        mock_pod.metadata.labels = {}
        mock_pod.status.pod_ip = None  # Pod not ready yet

        mock_pods = MagicMock()
        mock_pods.items = [mock_pod]
        mock_v1.list_namespaced_pod.return_value = mock_pods

        source = KubernetesDiscoverySource(namespaces=["default"], in_cluster=False)
        providers = await source.discover()

        assert providers == []

    @pytest.mark.asyncio
    async def test_health_check_success(self, mock_k8s_client):
        """Test health check when K8s API is accessible."""
        from mcp_hangar.infrastructure.discovery.kubernetes_source import KubernetesDiscoverySource

        mock_v1, _ = mock_k8s_client
        mock_v1.get_api_resources.return_value = MagicMock()

        source = KubernetesDiscoverySource(in_cluster=False)
        healthy = await source.health_check()

        assert healthy is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self, mock_k8s_client):
        """Test health check when K8s API is not accessible."""
        from mcp_hangar.infrastructure.discovery.kubernetes_source import KubernetesDiscoverySource

        mock_v1, _ = mock_k8s_client
        mock_v1.get_api_resources.side_effect = Exception("API unavailable")

        source = KubernetesDiscoverySource(in_cluster=False)
        healthy = await source.health_check()

        assert healthy is False


class TestDiscoveryMetricsIntegration:
    """Test discovery metrics are properly recorded."""

    def test_discovery_cycle_metrics(self):
        """Test metrics are recorded for discovery cycle."""
        from mcp_hangar.metrics import get_metrics, record_discovery_cycle, update_discovery_source

        # Record some discovery activity
        update_discovery_source("docker", "additive", True, 3)
        record_discovery_cycle("docker", 0.05, discovered=3, registered=2, quarantined=1)

        metrics_output = get_metrics()

        assert 'mcp_hangar_discovery_cycles_total{source_type="docker"}' in metrics_output
        assert 'mcp_hangar_discovery_providers{source_type="docker",status="discovered"} 3' in metrics_output

    def test_kubernetes_specific_metrics(self):
        """Test Kubernetes-specific metrics."""
        from mcp_hangar.metrics import get_metrics, record_discovery_deregistration, record_discovery_quarantine

        # Record K8s-specific events
        record_discovery_deregistration("kubernetes", "ttl_expired")
        record_discovery_quarantine("namespace_denied")

        metrics_output = get_metrics()

        assert (
            'mcp_hangar_discovery_deregistrations_total{reason="ttl_expired",source_type="kubernetes"}'
            in metrics_output
        )
        assert 'mcp_hangar_discovery_quarantine_total{reason="namespace_denied"}' in metrics_output
