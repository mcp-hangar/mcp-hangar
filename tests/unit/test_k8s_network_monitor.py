"""Unit tests for K8sNetworkMonitor -- BSL 1.1 licensed.

Tests cover:
- Pod discovery by mcp-hangar.provider-id label
- Audit event parsing for NetworkPolicy reasons
- Pod exec fallback via /proc/net/tcp parsing
- Graceful handling when kubernetes is not importable
- Empty list when no pods found or exec fails
"""

import time
from unittest.mock import MagicMock, Mock, patch

import pytest

from mcp_hangar.domain.value_objects.behavioral import NetworkObservation

# All tests that construct K8sNetworkMonitor need K8S_AVAILABLE=True
# and the kubernetes SDK mocked, since the real package may not be installed.
_K8S_GUARD_PATCH = patch("enterprise.behavioral.k8s_network_monitor.K8S_AVAILABLE", True)


def _make_monitor(mock_client_mod: MagicMock) -> "K8sNetworkMonitor":
    """Helper to construct a K8sNetworkMonitor with mocked kubernetes."""
    from enterprise.behavioral.k8s_network_monitor import K8sNetworkMonitor

    mock_api = MagicMock()
    mock_client_mod.CoreV1Api.return_value = mock_api
    monitor = K8sNetworkMonitor(namespace="default", use_in_cluster=False)
    return monitor


class TestK8sNetworkMonitorImportGuard:
    """Tests for the K8S_AVAILABLE import guard."""

    def test_constructor_raises_import_error_when_kubernetes_unavailable(self):
        """K8sNetworkMonitor raises ImportError when kubernetes SDK not installed."""
        from enterprise.behavioral.k8s_network_monitor import K8sNetworkMonitor

        with patch("enterprise.behavioral.k8s_network_monitor.K8S_AVAILABLE", False):
            with pytest.raises(ImportError, match="kubernetes package required"):
                K8sNetworkMonitor(namespace="test", use_in_cluster=False)


class TestFindProviderPods:
    """Tests for _find_provider_pods filtering by label and Running status."""

    def test_find_provider_pods_filters_by_label_and_running_status(self):
        """Only Running pods with matching label are returned."""
        from enterprise.behavioral.k8s_network_monitor import K8sNetworkMonitor

        with _K8S_GUARD_PATCH:
            with patch("enterprise.behavioral.k8s_network_monitor.k8s_config"):
                with patch("enterprise.behavioral.k8s_network_monitor.k8s_client") as mock_client_mod:
                    mock_api = MagicMock()
                    mock_client_mod.CoreV1Api.return_value = mock_api

                    running_pod = MagicMock()
                    running_pod.status.phase = "Running"
                    running_pod.metadata.name = "provider-abc-1"

                    pending_pod = MagicMock()
                    pending_pod.status.phase = "Pending"
                    pending_pod.metadata.name = "provider-abc-2"

                    mock_api.list_namespaced_pod.return_value.items = [running_pod, pending_pod]

                    monitor = K8sNetworkMonitor(namespace="default", use_in_cluster=False)
                    pods = monitor._find_provider_pods("my-provider", "default")

                    assert len(pods) == 1
                    assert pods[0].metadata.name == "provider-abc-1"
                    mock_api.list_namespaced_pod.assert_called_once_with(
                        "default",
                        label_selector="mcp-hangar.provider-id=my-provider",
                    )

    def test_find_provider_pods_returns_empty_when_no_pods(self):
        """Returns empty list when no pods match the label."""
        from enterprise.behavioral.k8s_network_monitor import K8sNetworkMonitor

        with _K8S_GUARD_PATCH:
            with patch("enterprise.behavioral.k8s_network_monitor.k8s_config"):
                with patch("enterprise.behavioral.k8s_network_monitor.k8s_client") as mock_client_mod:
                    mock_api = MagicMock()
                    mock_client_mod.CoreV1Api.return_value = mock_api
                    mock_api.list_namespaced_pod.return_value.items = []

                    monitor = K8sNetworkMonitor(namespace="default", use_in_cluster=False)
                    pods = monitor._find_provider_pods("nonexistent", "default")

                    assert pods == []


class TestPollPodExec:
    """Tests for _poll_pod_exec fallback path."""

    def test_poll_pod_exec_parses_proc_net_tcp_output(self):
        """Pod exec reads /proc/net/tcp and parses into connection tuples."""
        from enterprise.behavioral.k8s_network_monitor import K8sNetworkMonitor

        with _K8S_GUARD_PATCH:
            with patch("enterprise.behavioral.k8s_network_monitor.k8s_config"):
                with patch("enterprise.behavioral.k8s_network_monitor.k8s_client") as mock_client_mod:
                    mock_api = MagicMock()
                    mock_client_mod.CoreV1Api.return_value = mock_api

                    running_pod = MagicMock()
                    running_pod.status.phase = "Running"
                    running_pod.metadata.name = "provider-pod-1"
                    running_pod.metadata.namespace = "default"
                    mock_api.list_namespaced_pod.return_value.items = [running_pod]

                    proc_net_content = (
                        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid\n"
                        "   0: 0100007F:0050 0100A8C0:01BB 01 00000000:00000000 00:00000000 00000000     0\n"
                    )

                    monitor = K8sNetworkMonitor(namespace="default", use_in_cluster=False)

                    mock_parser = MagicMock()
                    mock_parser.return_value = [("192.168.0.1", 443, "tcp")]

                    with patch("enterprise.behavioral.k8s_network_monitor.stream") as mock_stream:
                        mock_stream.stream.return_value = proc_net_content
                        with patch(
                            "enterprise.behavioral.k8s_network_monitor._parse_proc_net_fallback",
                            mock_parser,
                        ):
                            results = monitor._poll_pod_exec("my-provider", "default")

                    assert len(results) == 1
                    assert results[0] == ("192.168.0.1", 443, "tcp")

    def test_poll_pod_exec_returns_empty_on_exec_failure(self):
        """Returns empty list when pod exec fails (per-pod fault barrier)."""
        from enterprise.behavioral.k8s_network_monitor import K8sNetworkMonitor

        with _K8S_GUARD_PATCH:
            with patch("enterprise.behavioral.k8s_network_monitor.k8s_config"):
                with patch("enterprise.behavioral.k8s_network_monitor.k8s_client") as mock_client_mod:
                    mock_api = MagicMock()
                    mock_client_mod.CoreV1Api.return_value = mock_api

                    running_pod = MagicMock()
                    running_pod.status.phase = "Running"
                    running_pod.metadata.name = "provider-pod-1"
                    running_pod.metadata.namespace = "default"
                    mock_api.list_namespaced_pod.return_value.items = [running_pod]

                    monitor = K8sNetworkMonitor(namespace="default", use_in_cluster=False)

                    with patch("enterprise.behavioral.k8s_network_monitor.stream") as mock_stream:
                        mock_stream.stream.side_effect = Exception("exec failed")
                        results = monitor._poll_pod_exec("my-provider", "default")

                    assert results == []

    def test_poll_pod_exec_returns_empty_when_no_pods(self):
        """Returns empty list when no running pods found."""
        from enterprise.behavioral.k8s_network_monitor import K8sNetworkMonitor

        with _K8S_GUARD_PATCH:
            with patch("enterprise.behavioral.k8s_network_monitor.k8s_config"):
                with patch("enterprise.behavioral.k8s_network_monitor.k8s_client") as mock_client_mod:
                    mock_api = MagicMock()
                    mock_client_mod.CoreV1Api.return_value = mock_api
                    mock_api.list_namespaced_pod.return_value.items = []

                    monitor = K8sNetworkMonitor(namespace="default", use_in_cluster=False)
                    results = monitor._poll_pod_exec("my-provider", "default")

                    assert results == []


class TestPollAuditEvents:
    """Tests for _poll_audit_events audit log path."""

    def test_audit_event_parsing_extracts_network_policy_events(self):
        """Parses K8s events with NetworkPolicy reason into connection tuples."""
        from enterprise.behavioral.k8s_network_monitor import K8sNetworkMonitor

        with _K8S_GUARD_PATCH:
            with patch("enterprise.behavioral.k8s_network_monitor.k8s_config"):
                with patch("enterprise.behavioral.k8s_network_monitor.k8s_client") as mock_client_mod:
                    mock_api = MagicMock()
                    mock_client_mod.CoreV1Api.return_value = mock_api

                    event = MagicMock()
                    event.reason = "NetworkPolicyDrop"
                    event.message = "Denied connection to 10.0.0.5:8080/tcp"
                    event.involved_object.name = "provider-pod-1"
                    event.involved_object.kind = "Pod"

                    running_pod = MagicMock()
                    running_pod.status.phase = "Running"
                    running_pod.metadata.name = "provider-pod-1"
                    mock_api.list_namespaced_pod.return_value.items = [running_pod]

                    mock_api.list_namespaced_event.return_value.items = [event]

                    monitor = K8sNetworkMonitor(namespace="default", use_in_cluster=False)
                    results = monitor._poll_audit_events("my-provider", "default")

                    assert len(results) == 1
                    assert results[0] == ("10.0.0.5", 8080, "tcp")

    def test_audit_events_sets_unavailable_when_no_events(self):
        """Sets _audit_available to False when no NetworkPolicy events found."""
        from enterprise.behavioral.k8s_network_monitor import K8sNetworkMonitor

        with _K8S_GUARD_PATCH:
            with patch("enterprise.behavioral.k8s_network_monitor.k8s_config"):
                with patch("enterprise.behavioral.k8s_network_monitor.k8s_client") as mock_client_mod:
                    mock_api = MagicMock()
                    mock_client_mod.CoreV1Api.return_value = mock_api

                    mock_api.list_namespaced_event.return_value.items = []
                    mock_api.list_namespaced_pod.return_value.items = []

                    monitor = K8sNetworkMonitor(namespace="default", use_in_cluster=False)
                    assert monitor._audit_available is None

                    results = monitor._poll_audit_events("my-provider", "default")

                    assert results == []
                    assert monitor._audit_available is False


class TestPollConnections:
    """Tests for the top-level poll_connections method."""

    def test_poll_connections_returns_network_observations_from_pod_exec(self):
        """poll_connections returns NetworkObservation records from pod exec fallback."""
        from enterprise.behavioral.k8s_network_monitor import K8sNetworkMonitor

        with _K8S_GUARD_PATCH:
            with patch("enterprise.behavioral.k8s_network_monitor.k8s_config"):
                with patch("enterprise.behavioral.k8s_network_monitor.k8s_client") as mock_client_mod:
                    mock_api = MagicMock()
                    mock_client_mod.CoreV1Api.return_value = mock_api

                    monitor = K8sNetworkMonitor(namespace="default", use_in_cluster=False)
                    monitor._audit_available = False

                    with patch.object(
                        monitor,
                        "_poll_pod_exec",
                        return_value=[("10.0.0.1", 443, "tcp")],
                    ):
                        observations = monitor.poll_connections("my-provider")

                    assert len(observations) == 1
                    obs = observations[0]
                    assert isinstance(obs, NetworkObservation)
                    assert obs.provider_id == "my-provider"
                    assert obs.destination_host == "10.0.0.1"
                    assert obs.destination_port == 443
                    assert obs.protocol == "tcp"
                    assert obs.direction == "outbound"

    def test_poll_connections_returns_empty_when_both_paths_fail(self):
        """Returns empty list when both audit events and pod exec yield nothing."""
        from enterprise.behavioral.k8s_network_monitor import K8sNetworkMonitor

        with _K8S_GUARD_PATCH:
            with patch("enterprise.behavioral.k8s_network_monitor.k8s_config"):
                with patch("enterprise.behavioral.k8s_network_monitor.k8s_client") as mock_client_mod:
                    mock_api = MagicMock()
                    mock_client_mod.CoreV1Api.return_value = mock_api

                    monitor = K8sNetworkMonitor(namespace="default", use_in_cluster=False)

                    with patch.object(monitor, "_poll_audit_events", return_value=[]):
                        with patch.object(monitor, "_poll_pod_exec", return_value=[]):
                            observations = monitor.poll_connections("my-provider")

                    assert observations == []

    def test_poll_connections_uses_custom_namespace(self):
        """poll_connections passes namespace override to internal methods."""
        from enterprise.behavioral.k8s_network_monitor import K8sNetworkMonitor

        with _K8S_GUARD_PATCH:
            with patch("enterprise.behavioral.k8s_network_monitor.k8s_config"):
                with patch("enterprise.behavioral.k8s_network_monitor.k8s_client") as mock_client_mod:
                    mock_api = MagicMock()
                    mock_client_mod.CoreV1Api.return_value = mock_api

                    monitor = K8sNetworkMonitor(namespace="default", use_in_cluster=False)
                    monitor._audit_available = False

                    with patch.object(monitor, "_poll_pod_exec", return_value=[]) as mock_exec:
                        monitor.poll_connections("my-provider", namespace="custom-ns")
                        mock_exec.assert_called_once_with("my-provider", "custom-ns")
