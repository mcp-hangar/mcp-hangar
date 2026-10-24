"""Labels and annotations cannot choose a host process or host mounts for Hangar.

A pod annotated `mcp-hangar.io/mode: subprocess` used to register a subprocess
server whose command came from `mcp-hangar.io/command` and ran on the gateway's
host; anyone who could create a pod in a watched namespace chose it. Kubernetes
local modes are now an opt-in per source (`allow_local_modes`).

A container labelled for `container` mode used to be run with the host mounts
named in `mcp.hangar.volumes`, read-write and without a read-only root. Container
mode itself stays on (it is documented, and the label's default), but label
volumes are honoured only under the source's `allowed_mount_paths`, and a
discovered container gets a read-only root unless its label says otherwise.
Remote discovery is unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from mcp_hangar.domain.exceptions import ValidationError
from mcp_hangar.infrastructure.discovery.registry import create_source
from mcp_hangar.infrastructure.launchers.container import ContainerLauncher
from mcp_hangar.server.bootstrap import discovery as bootstrap_discovery


@pytest.fixture(autouse=True)
def _client_libraries():
    """Both sources are built without their client libraries, as CI's core install is."""
    with (
        patch("mcp_hangar.infrastructure.discovery.docker_source.DOCKER_AVAILABLE", True),
        patch("mcp_hangar.infrastructure.discovery.docker_source.docker"),
        patch("mcp_hangar.infrastructure.discovery.kubernetes_source.KUBERNETES_AVAILABLE", True),
        patch("mcp_hangar.infrastructure.discovery.kubernetes_source.config"),
        patch("mcp_hangar.infrastructure.discovery.kubernetes_source.client"),
    ):
        yield


def _container(mode: str, **labels: str):
    container = MagicMock()
    container.id = "abc123def456789"
    container.name = "labelled"
    container.status = "running"
    container.labels = {"mcp.hangar.enabled": "true", "mcp.hangar.name": "labelled", "mcp.hangar.mode": mode, **labels}
    container.image = MagicMock(tags=["some/image:1"], id="sha256:abc123def456")
    container.attrs = {
        "NetworkSettings": {
            "Ports": {"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18080"}]},
            "Networks": {"bridge": {"IPAddress": "10.88.0.23"}},
        }
    }
    return container


def _pod(mode: str, command: str | None = None):
    annotations = {"mcp-hangar.io/enabled": "true", "mcp-hangar.io/name": "annotated", "mcp-hangar.io/mode": mode}
    if command:
        annotations["mcp-hangar.io/command"] = command
    pod = Mock()
    pod.metadata = Mock(uid="uid-1", annotations=annotations, labels={})
    pod.metadata.name = "annotated-pod"
    pod.status = Mock(pod_ip="10.0.0.9", phase="Running")
    pod.spec = Mock(node_name="node-1")
    return pod


class TestKubernetes:
    def test_a_subprocess_annotation_is_refused_by_default(self):
        source = create_source("kubernetes", {"in_cluster": False})

        assert source._parse_pod(_pod("subprocess", "npx -y some-package"), "team-a") is None

    def test_an_http_pod_is_discovered_as_before(self):
        source = create_source("kubernetes", {"in_cluster": False})

        found = source._parse_pod(_pod("http"), "team-a")

        assert found is not None
        assert found.mode == "http"
        assert found.connection_info["host"] == "10.0.0.9"

    def test_the_opt_in_lets_a_subprocess_annotation_through(self):
        source = create_source("kubernetes", {"in_cluster": False, "allow_local_modes": True})

        found = source._parse_pod(_pod("subprocess", "python -m srv"), "team-a")

        assert found is not None
        assert found.connection_info["command"] == ["python", "-m", "srv"]

    def test_a_truthy_string_is_not_the_opt_in(self):
        source = create_source("kubernetes", {"in_cluster": False, "allow_local_modes": "yes"})

        assert source._parse_pod(_pod("subprocess", "python -m srv"), "team-a") is None


class TestDocker:
    @pytest.mark.parametrize("mode", ["container", None])
    def test_container_mode_is_still_discovered_by_default(self, mode):
        # Documented, and the label's default: Hangar runs the container's image.
        container = _container(mode or "container")
        if mode is None:
            del container.labels["mcp.hangar.mode"]

        found = create_source("docker", {})._parse_container(container)

        assert found is not None
        assert found.mode == "container"

    def test_an_http_container_is_discovered_as_before(self):
        found = create_source("docker", {})._parse_container(_container("http"))

        assert found is not None
        assert found.connection_info["endpoint"] == "http://127.0.0.1:18080/mcp"

    def test_a_discovered_container_gets_a_read_only_root(self):
        found = create_source("docker", {})._parse_container(_container("container"))

        assert found.connection_info["read_only"] is True

    def test_the_read_only_label_still_opts_out(self):
        found = create_source("docker", {})._parse_container(
            _container("container", **{"mcp.hangar.read-only": "false"})
        )

        assert found.connection_info["read_only"] is False

    def test_label_volumes_need_an_allowed_mount_path(self):
        source = create_source("docker", {})

        assert source._parse_container(_container("container", **{"mcp.hangar.volumes": "/srv/data:/data"})) is None

    def test_the_refusal_is_logged_once(self):
        source = create_source("docker", {})
        container = _container("container", **{"mcp.hangar.volumes": "/:/host:rw"})
        with patch("mcp_hangar.infrastructure.discovery.docker_source.logger") as log:
            source._parse_container(container)
            source._parse_container(container)

        assert log.warning.call_count == 1
        assert log.warning.call_args.args[0] == "docker_discovery_label_volumes_refused"

    @pytest.mark.parametrize(
        "volume",
        [
            "/:/host:rw",
            "/var/run/docker.sock:/var/run/docker.sock",
            "/srv/mcp/../../etc:/x",
            "~/.ssh:/ssh",
            "/srv/mcp/run/docker.sock:/s",
            "/srv/mcpx:/x",
            "relative:/x",
            "/srv/mcp/ok:/ok,/etc/ssh/x:/x",
        ],
    )
    def test_label_volumes_outside_the_allowed_paths_are_refused(self, volume):
        source = create_source("docker", {"allowed_mount_paths": ["/srv/mcp"]})

        assert source._parse_container(_container("container", **{"mcp.hangar.volumes": volume})) is None

    def test_label_volumes_inside_the_allowed_paths_are_kept(self):
        source = create_source("docker", {"allowed_mount_paths": ["/srv/mcp"]})

        found = source._parse_container(_container("container", **{"mcp.hangar.volumes": "/srv/mcp/a:/data:rw"}))

        assert found.connection_info["volumes"] == ["/srv/mcp/a:/data:rw"]


class TestRegistration:
    @pytest.mark.asyncio
    async def test_a_discovered_docker_container_is_registered_read_only(self, monkeypatch):
        sent: list = []
        monkeypatch.setattr(
            bootstrap_discovery,
            "get_runtime",
            lambda: SimpleNamespace(command_bus=SimpleNamespace(send=sent.append)),
        )
        found = create_source("docker", {})._parse_container(_container("container"))

        assert await bootstrap_discovery._on_mcp_server_register(found) is True
        assert sent[0].mode == "docker"
        assert sent[0].read_only is True


def _launcher() -> ContainerLauncher:
    """The volume check alone; constructing one looks for a container runtime on PATH."""
    launcher = ContainerLauncher.__new__(ContainerLauncher)
    launcher._allowed_mount_paths = []
    return launcher


class TestTheSharedVolumeCheck:
    @pytest.mark.parametrize("volume", ["/:/host", "//:/host", "/etc/..:/host", "/srv/../:/host:rw"])
    def test_the_host_root_is_refused(self, volume):
        with pytest.raises(ValidationError):
            _launcher()._validate_volume(volume)

    def test_an_ordinary_mount_is_still_accepted(self):
        _launcher()._validate_volume("/srv/data:/data:rw")
