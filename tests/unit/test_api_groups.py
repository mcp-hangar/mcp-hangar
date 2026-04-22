"""Tests for provider group REST API endpoints.

Tests cover:
- GET /groups returns list of all group summaries
- GET /groups returns empty list when no groups
- GET /groups/{id} returns group detail with members and circuit breaker
- GET /groups/{id} returns 404 for unknown group
- POST /groups/{id}/rebalance triggers rebalance and returns result
- POST /groups/{id}/rebalance returns 404 for unknown group
"""

from unittest.mock import Mock, patch

import pytest
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_member(mcp_server_id: str, weight: int = 1, in_rotation: bool = True) -> Mock:
    """Create a mock GroupMember."""
    member = Mock()
    member.id = mcp_server_id
    member.weight = weight
    member.priority = 1
    member.in_rotation = in_rotation
    member.provider = Mock()
    member.provider.state = Mock()
    member.provider.state.value = "ready"
    return member


def _make_mock_group(
    group_id: str,
    strategy: str = "round_robin",
    state: str = "healthy",
    members: list | None = None,
    circuit_open: bool = False,
) -> Mock:
    """Create a mock ProviderGroup."""
    group = Mock()
    group.id = group_id
    group.strategy = Mock()
    group.strategy.value = strategy
    group.state = Mock()
    group.state.value = state
    group.circuit_open = circuit_open
    group._circuit_breaker = Mock()
    group._circuit_breaker.failure_count = 0

    if members is None:
        members = [_make_mock_member("provider-1")]

    group.members = members
    group.total_count = len(members)
    group.healthy_count = sum(1 for m in members if m.in_rotation)

    # to_status_dict provides a pre-serialized view
    group.to_status_dict.return_value = {
        "group_id": group_id,
        "description": None,
        "state": state,
        "strategy": strategy,
        "min_healthy": 1,
        "healthy_count": group.healthy_count,
        "total_members": len(members),
        "is_available": not circuit_open and group.healthy_count >= 1,
        "circuit_open": circuit_open,
        "members": [
            {
                "id": m.id,
                "state": m.provider.state.value,
                "in_rotation": m.in_rotation,
                "weight": m.weight,
                "priority": m.priority,
                "consecutive_failures": 0,
            }
            for m in members
        ],
    }
    return group


@pytest.fixture
def mock_group():
    """Mock ProviderGroup for 'my-group'."""
    return _make_mock_group(
        group_id="my-group",
        strategy="round_robin",
        state="healthy",
        members=[
            _make_mock_member("provider-1", weight=1, in_rotation=True),
            _make_mock_member("provider-2", weight=2, in_rotation=False),
        ],
    )


@pytest.fixture
def mock_context(mock_group):
    """Mock ApplicationContext with groups dict."""
    ctx = Mock()
    ctx.groups = {"my-group": mock_group}
    ctx.command_bus = Mock()
    ctx.query_bus = Mock()
    return ctx


@pytest.fixture
def api_client(mock_context):
    """Starlette TestClient for the API app with mocked context."""
    from mcp_hangar.server.api import create_api_router

    with patch("mcp_hangar.server.api.middleware.get_context", return_value=mock_context):
        with patch("mcp_hangar.server.api.groups.get_context", return_value=mock_context):
            app = create_api_router()
            client = TestClient(app, raise_server_exceptions=False)
            yield client


@pytest.fixture
def empty_groups_client():
    """API client with no groups configured."""
    ctx = Mock()
    ctx.groups = {}
    ctx.command_bus = Mock()
    ctx.query_bus = Mock()

    from mcp_hangar.server.api import create_api_router

    with patch("mcp_hangar.server.api.middleware.get_context", return_value=ctx):
        with patch("mcp_hangar.server.api.groups.get_context", return_value=ctx):
            app = create_api_router()
            client = TestClient(app, raise_server_exceptions=False)
            yield client


# ---------------------------------------------------------------------------
# GET /groups
# ---------------------------------------------------------------------------


class TestListGroups:
    """Tests for GET /groups."""

    def test_returns_200(self, api_client):
        """GET /groups returns HTTP 200."""
        response = api_client.get("/groups/")
        assert response.status_code == 200

    def test_returns_groups_key(self, api_client):
        """GET /groups returns JSON with 'groups' key."""
        response = api_client.get("/groups/")
        data = response.json()
        assert "groups" in data
        assert isinstance(data["groups"], list)

    def test_returns_group_summary_fields(self, api_client):
        """GET /groups returns groups with expected summary fields."""
        response = api_client.get("/groups/")
        data = response.json()
        assert len(data["groups"]) == 1
        group = data["groups"][0]
        assert group["group_id"] == "my-group"
        assert group["strategy"] == "round_robin"
        assert group["state"] == "healthy"
        assert "total_members" in group
        assert "healthy_count" in group

    def test_returns_empty_list_when_no_groups(self, empty_groups_client):
        """GET /groups returns empty list when no groups configured."""
        response = empty_groups_client.get("/groups/")
        data = response.json()
        assert data["groups"] == []

    def test_returns_correct_member_counts(self, api_client):
        """GET /groups returns correct member and healthy counts."""
        response = api_client.get("/groups/")
        data = response.json()
        group = data["groups"][0]
        assert group["total_members"] == 2
        assert group["healthy_count"] == 1  # Only provider-1 is in_rotation


# ---------------------------------------------------------------------------
# GET /groups/{id}
# ---------------------------------------------------------------------------


class TestGetGroup:
    """Tests for GET /groups/{id}."""

    def test_returns_200_for_known_group(self, api_client):
        """GET /groups/my-group returns HTTP 200."""
        response = api_client.get("/groups/my-group")
        assert response.status_code == 200

    def test_returns_group_detail_with_members(self, api_client):
        """GET /groups/my-group returns detail including members list."""
        response = api_client.get("/groups/my-group")
        data = response.json()
        assert data["group_id"] == "my-group"
        assert "members" in data
        assert isinstance(data["members"], list)
        assert len(data["members"]) == 2

    def test_members_contain_expected_fields(self, api_client):
        """GET /groups/my-group members have id, weight, state, in_rotation."""
        response = api_client.get("/groups/my-group")
        data = response.json()
        member = data["members"][0]
        assert "id" in member
        assert "weight" in member
        assert "in_rotation" in member
        assert "state" in member

    def test_returns_circuit_breaker_state(self, api_client):
        """GET /groups/my-group returns circuit_open field."""
        response = api_client.get("/groups/my-group")
        data = response.json()
        assert "circuit_open" in data
        assert data["circuit_open"] is False

    def test_returns_404_for_unknown_group(self, api_client):
        """GET /groups/unknown returns HTTP 404."""
        response = api_client.get("/groups/unknown")
        assert response.status_code == 404

    def test_returns_error_envelope_for_404(self, api_client):
        """GET /groups/unknown returns error envelope format."""
        response = api_client.get("/groups/unknown")
        data = response.json()
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]


# ---------------------------------------------------------------------------
# POST /groups/{id}/rebalance
# ---------------------------------------------------------------------------


class TestRebalanceGroup:
    """Tests for POST /groups/{id}/rebalance."""

    def test_returns_200_for_known_group(self, api_client):
        """POST /groups/my-group/rebalance returns HTTP 200."""
        response = api_client.post("/groups/my-group/rebalance")
        assert response.status_code == 200

    def test_returns_rebalance_result(self, api_client):
        """POST /groups/my-group/rebalance returns status and group_id."""
        response = api_client.post("/groups/my-group/rebalance")
        data = response.json()
        assert data["status"] == "rebalanced"
        assert data["group_id"] == "my-group"

    def test_calls_rebalance_on_group(self, api_client, mock_context):
        """POST /groups/my-group/rebalance calls group.rebalance()."""
        response = api_client.post("/groups/my-group/rebalance")
        assert response.status_code == 200
        mock_context.groups["my-group"].rebalance.assert_called_once()

    def test_returns_404_for_unknown_group(self, api_client):
        """POST /groups/unknown/rebalance returns HTTP 404."""
        response = api_client.post("/groups/unknown/rebalance")
        assert response.status_code == 404
