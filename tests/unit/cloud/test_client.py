"""Tests for cloud.client -- HTTP client for uplink REST API."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_hangar.cloud.client import CloudClient
from mcp_hangar.cloud.config import CloudConfig


@pytest.fixture
def config():
    return CloudConfig(license_key="hk_v1_test", endpoint="http://localhost:9999")


def _mock_response(json_data=None, status_code=200):
    resp = MagicMock()
    resp.json.return_value = json_data or {}
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    return resp


class TestCloudClient:
    @pytest.mark.asyncio
    async def test_register_sets_agent_id(self, config):
        client = CloudClient(config)
        client._http.post = AsyncMock(return_value=_mock_response(
            {"agent_id": "ag_123", "tenant_id": "t_456", "config": {}}
        ))
        data = await client.register()
        assert data["agent_id"] == "ag_123"
        assert client.agent_id == "ag_123"
        await client.close()

    @pytest.mark.asyncio
    async def test_heartbeat(self, config):
        client = CloudClient(config)
        client._agent_id = "ag_123"
        client._http.post = AsyncMock(return_value=_mock_response({"status": "ok"}))
        await client.heartbeat(4, 3, 120.0)
        client._http.post.assert_called_once()
        args = client._http.post.call_args
        assert "ag_123/heartbeat" in args[0][0]
        await client.close()

    @pytest.mark.asyncio
    async def test_heartbeat_skipped_without_agent_id(self, config):
        client = CloudClient(config)
        client._http.post = AsyncMock()
        await client.heartbeat(1, 1, 10.0)
        client._http.post.assert_not_called()
        await client.close()

    @pytest.mark.asyncio
    async def test_send_events(self, config):
        client = CloudClient(config)
        client._agent_id = "ag_123"
        client._http.post = AsyncMock(return_value=_mock_response({"acked": 1}))
        await client.send_events([{"event_type": "ToolInvocationCompleted"}])
        client._http.post.assert_called_once()
        await client.close()

    @pytest.mark.asyncio
    async def test_send_events_skipped_empty(self, config):
        client = CloudClient(config)
        client._agent_id = "ag_123"
        client._http.post = AsyncMock()
        await client.send_events([])
        client._http.post.assert_not_called()
        await client.close()

    @pytest.mark.asyncio
    async def test_sync_state(self, config):
        client = CloudClient(config)
        client._agent_id = "ag_123"
        client._http.put = AsyncMock(return_value=_mock_response({"status": "ok"}))
        await client.sync_state([{"id": "github", "status": "READY"}])
        client._http.put.assert_called_once()
        await client.close()

    @pytest.mark.asyncio
    async def test_deregister_best_effort(self, config):
        client = CloudClient(config)
        client._agent_id = "ag_123"
        client._http.post = AsyncMock(return_value=_mock_response())
        await client.deregister()
        # Should have sent a heartbeat with status=shutting_down
        call_kwargs = client._http.post.call_args[1]
        assert call_kwargs["json"]["status"] == "shutting_down"
        await client.close()

    @pytest.mark.asyncio
    async def test_deregister_ignores_errors(self, config):
        client = CloudClient(config)
        client._agent_id = "ag_123"
        client._http.post = AsyncMock(side_effect=Exception("network error"))
        # Should not raise
        await client.deregister()
        await client.close()

    def test_auth_header_configured(self, config):
        client = CloudClient(config)
        assert client._http.headers["authorization"] == "Bearer hk_v1_test"
