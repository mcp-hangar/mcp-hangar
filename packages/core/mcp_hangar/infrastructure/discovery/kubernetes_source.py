"""Kubernetes Discovery Source.

Discovers MCP providers from Kubernetes pods/services using annotations.
Supports both in-cluster and out-of-cluster configuration.

Annotation Prefix: mcp-hangar.io/*

Example Pod Annotations:
    mcp-hangar.io/enabled: "true"
    mcp-hangar.io/name: "my-provider"
    mcp-hangar.io/mode: "http"
    mcp-hangar.io/port: "8080"
    mcp-hangar.io/group: "data-team"
    mcp-hangar.io/health-path: "/health"
"""

from mcp_hangar.domain.discovery.discovered_provider import DiscoveredProvider
from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode, DiscoverySource

from ...logging_config import get_logger

logger = get_logger(__name__)

# Optional Kubernetes dependency
try:
    from kubernetes import client, config
    from kubernetes.client.rest import ApiException

    KUBERNETES_AVAILABLE = True
except ImportError:
    KUBERNETES_AVAILABLE = False
    ApiException = Exception  # Fallback for type hints
    client = None
    config = None
    logger.debug("kubernetes package not installed, KubernetesDiscoverySource unavailable")


class KubernetesDiscoverySource(DiscoverySource):
    """Discover MCP providers from Kubernetes pods/services.

    Uses pod annotations to discover and configure MCP providers.
    Supports namespace filtering and label selectors.

    Attributes:
        ANNOTATION_PREFIX: Prefix for all MCP annotations
    """

    ANNOTATION_PREFIX = "mcp-hangar.io/"

    def __init__(
        self,
        mode: DiscoveryMode = DiscoveryMode.AUTHORITATIVE,
        namespaces: list[str] | None = None,
        label_selector: str | None = None,
        in_cluster: bool = True,
        kubeconfig_path: str | None = None,
        default_ttl: int = 90,
    ):
        """Initialize Kubernetes discovery source.

        Args:
            mode: Discovery mode (default: authoritative for K8s)
            namespaces: List of namespaces to watch (None = all)
            label_selector: Kubernetes label selector
            in_cluster: Whether running inside cluster
            kubeconfig_path: Path to kubeconfig (for out-of-cluster)
            default_ttl: Default TTL for discovered providers
        """
        super().__init__(mode)

        if not KUBERNETES_AVAILABLE:
            raise ImportError(
                "kubernetes package is required for KubernetesDiscoverySource. Install with: pip install kubernetes"
            )

        self.namespaces = namespaces or []
        self.label_selector = label_selector
        self.in_cluster = in_cluster
        self.kubeconfig_path = kubeconfig_path
        self.default_ttl = default_ttl

        self._v1: client.CoreV1Api | None = None
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """Ensure Kubernetes client is initialized."""
        if self._initialized:
            return

        try:
            if self.in_cluster:
                config.load_incluster_config()
            else:
                config.load_kube_config(config_file=self.kubeconfig_path)

            self._v1 = client.CoreV1Api()
            self._initialized = True
            logger.info(
                f"Kubernetes discovery initialized "
                f"(in_cluster={self.in_cluster}, namespaces={self.namespaces or 'all'})"
            )
        except Exception as e:
            logger.error(f"Failed to initialize Kubernetes client: {e}")
            raise

    @property
    def source_type(self) -> str:
        return "kubernetes"

    async def discover(self) -> list[DiscoveredProvider]:
        """Discover providers from pod annotations.

        Returns:
            List of discovered providers
        """
        self._ensure_initialized()
        providers = []

        namespaces = self.namespaces or await self._get_all_namespaces()

        for namespace in namespaces:
            try:
                pods = self._v1.list_namespaced_pod(namespace=namespace, label_selector=self.label_selector)

                for pod in pods.items:
                    provider = self._parse_pod(pod, namespace)
                    if provider:
                        providers.append(provider)
                        await self.on_provider_discovered(provider)

            except ApiException as e:
                logger.warning(f"Failed to list pods in {namespace}: {e.reason}")
            except Exception as e:
                logger.error(f"Error discovering in namespace {namespace}: {e}")

        logger.debug(f"Kubernetes discovery found {len(providers)} providers")
        return providers

    def _parse_pod(self, pod, namespace: str) -> DiscoveredProvider | None:
        """Parse pod annotations into DiscoveredProvider.

        Args:
            pod: Kubernetes pod object
            namespace: Pod namespace

        Returns:
            DiscoveredProvider or None if not MCP-enabled
        """
        annotations = pod.metadata.annotations or {}

        # Check if MCP discovery is enabled
        enabled = annotations.get(f"{self.ANNOTATION_PREFIX}enabled", "false")
        if enabled.lower() != "true":
            return None

        # Extract provider config
        name = annotations.get(f"{self.ANNOTATION_PREFIX}name", pod.metadata.name)
        mode = annotations.get(f"{self.ANNOTATION_PREFIX}mode", "http")
        port = annotations.get(f"{self.ANNOTATION_PREFIX}port", "8080")
        group = annotations.get(f"{self.ANNOTATION_PREFIX}group")
        health_path = annotations.get(f"{self.ANNOTATION_PREFIX}health-path", "/health")
        ttl = int(annotations.get(f"{self.ANNOTATION_PREFIX}ttl", str(self.default_ttl)))

        # Get pod IP
        pod_ip = pod.status.pod_ip if pod.status else None
        if not pod_ip:
            logger.debug(f"Pod {pod.metadata.name} has no IP, skipping")
            return None

        # Check pod phase
        phase = pod.status.phase if pod.status else "Unknown"
        if phase != "Running":
            logger.debug(f"Pod {pod.metadata.name} not running (phase={phase}), skipping")
            return None

        # Build connection info
        connection_info = {
            "host": pod_ip,
            "port": int(port),
            "health_path": health_path,
        }

        # Handle subprocess mode
        if mode == "subprocess" or mode == "stdio":
            command = annotations.get(f"{self.ANNOTATION_PREFIX}command")
            if command:
                connection_info["command"] = command.split()

        metadata = {
            "namespace": namespace,
            "pod_name": pod.metadata.name,
            "pod_uid": pod.metadata.uid,
            "group": group,
            "labels": pod.metadata.labels or {},
            "annotations": {k: v for k, v in annotations.items() if k.startswith(self.ANNOTATION_PREFIX)},
            "node_name": pod.spec.node_name if pod.spec else None,
            "phase": phase,
        }

        return DiscoveredProvider.create(
            name=name,
            source_type=self.source_type,
            mode=mode,
            connection_info=connection_info,
            metadata=metadata,
            ttl_seconds=ttl,
        )

    async def health_check(self) -> bool:
        """Check Kubernetes API availability.

        Returns:
            True if API is accessible
        """
        try:
            self._ensure_initialized()
            self._v1.get_api_resources()
            return True
        except Exception as e:
            logger.warning(f"Kubernetes health check failed: {e}")
            return False

    async def _get_all_namespaces(self) -> list[str]:
        """Get all namespace names.

        Returns:
            List of namespace names
        """
        try:
            namespaces = self._v1.list_namespace()
            return [ns.metadata.name for ns in namespaces.items]
        except ApiException as e:
            logger.error(f"Failed to list namespaces: {e.reason}")
            return []

    async def start(self) -> None:
        """Start the Kubernetes discovery source."""
        self._ensure_initialized()
        logger.info("Kubernetes discovery source started")

    async def stop(self) -> None:
        """Stop the Kubernetes discovery source."""
        self._initialized = False
        self._v1 = None
        logger.info("Kubernetes discovery source stopped")
