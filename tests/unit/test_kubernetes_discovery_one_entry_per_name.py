"""Pods that share one `mcp-hangar.io/name` yield one discovered entry (#1692).

Every replica of a Deployment carries the same annotations, so discovery used to
return an entry per pod under one name. The orchestrator tracks a server by name
and compares fingerprints, so the entries replaced each other every cycle and
each swap was recorded as a config change. One entry per name, chosen by a fixed
order, keeps a steady state steady.
"""

from __future__ import annotations

from unittest.mock import MagicMock, Mock

import pytest

from mcp_hangar.infrastructure.discovery.kubernetes_source import KubernetesDiscoverySource


def _pod(name: str, ip: str, *, app_name: str = "search", phase: str = "Running"):
    pod = Mock()
    pod.metadata = Mock(
        uid=f"uid-{name}", annotations={"mcp-hangar.io/enabled": "true", "mcp-hangar.io/name": app_name}, labels={}
    )
    pod.metadata.name = name
    pod.status = Mock(pod_ip=ip, phase=phase)
    pod.spec = Mock(node_name="node-1")
    return pod


def _source(pods_by_namespace: dict[str, list]) -> KubernetesDiscoverySource:
    source = KubernetesDiscoverySource(namespaces=list(pods_by_namespace), in_cluster=False)
    v1 = MagicMock()
    v1.list_namespaced_pod.side_effect = lambda namespace, label_selector=None: MagicMock(
        items=pods_by_namespace[namespace]
    )
    source._v1 = v1
    source._initialized = True
    return source


class TestSeveralPodsOneName:
    @pytest.mark.asyncio
    async def test_the_replicas_of_a_deployment_are_one_entry(self):
        source = _source(
            {"apps": [_pod("search-c", "10.0.0.3"), _pod("search-a", "10.0.0.1"), _pod("search-b", "10.0.0.2")]}
        )

        found = await source.discover()

        assert [e.name for e in found] == ["search"]

    @pytest.mark.asyncio
    async def test_the_choice_is_fixed_whatever_order_the_api_lists_them_in(self):
        one = await _source({"apps": [_pod("search-a", "10.0.0.1"), _pod("search-b", "10.0.0.2")]}).discover()
        two = await _source({"apps": [_pod("search-b", "10.0.0.2"), _pod("search-a", "10.0.0.1")]}).discover()

        assert one[0].fingerprint == two[0].fingerprint
        assert one[0].metadata["pod_name"] == "search-a"

    @pytest.mark.asyncio
    async def test_a_pod_that_is_not_running_does_not_take_the_place(self):
        source = _source({"apps": [_pod("search-a", "10.0.0.1", phase="Pending"), _pod("search-b", "10.0.0.2")]})

        found = await source.discover()

        assert [e.metadata["pod_name"] for e in found] == ["search-b"]

    @pytest.mark.asyncio
    async def test_the_same_name_in_two_namespaces_is_one_entry_too(self):
        source = _source({"b-team": [_pod("search-1", "10.0.1.1")], "a-team": [_pod("search-9", "10.0.2.1")]})

        found = await source.discover()

        assert len(found) == 1
        assert found[0].metadata["namespace"] == "a-team"

    @pytest.mark.asyncio
    async def test_distinct_names_are_all_kept(self):
        source = _source({"apps": [_pod("search-a", "10.0.0.1"), _pod("mail-a", "10.0.0.9", app_name="mail")]})

        found = await source.discover()

        assert sorted(e.name for e in found) == ["mail", "search"]

    @pytest.mark.asyncio
    async def test_the_callback_hears_of_the_one_entry_only(self):
        source = _source({"apps": [_pod("search-a", "10.0.0.1"), _pod("search-b", "10.0.0.2")]})
        heard = []

        async def on_discovered(server):
            heard.append(server.metadata["pod_name"])

        source.on_mcp_server_discovered = on_discovered  # type: ignore[method-assign]

        await source.discover()

        assert heard == ["search-a"]
