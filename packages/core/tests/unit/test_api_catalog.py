"""Unit tests for catalog REST API endpoints (CAT-02).

Tests cover all 5 catalog endpoints:
GET /catalog, GET /catalog/{id}, POST /catalog/entries,
DELETE /catalog/entries/{id}, POST /catalog/{id}/deploy.
"""

from unittest.mock import Mock, patch

import pytest
from starlette.testclient import TestClient

from mcp_hangar.application.commands.crud_commands import CreateProviderCommand
from mcp_hangar.domain.model.catalog import McpProviderEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(entry_id: str = "e1", name: str = "filesystem", builtin: bool = False) -> McpProviderEntry:
    """Return a minimal McpProviderEntry for mock catalog."""
    return McpProviderEntry(
        entry_id=entry_id,
        name=name,
        description=f"{name} provider",
        mode="subprocess",
        command=["uvx", f"mcp-server-{name}"],
        image=None,
        tags=["test"],
        verified=True,
        source="builtin" if builtin else "custom",
        required_env=[],
        builtin=builtin,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_catalog():
    """Mock McpCatalogRepository."""
    catalog = Mock()
    catalog.list_entries.return_value = [_make_entry()]
    catalog.get_entry.side_effect = lambda entry_id: _make_entry() if entry_id == "e1" else None
    catalog.add_entry.return_value = None
    catalog.remove_entry.return_value = None
    catalog.count.return_value = 1
    return catalog


@pytest.fixture
def mock_context(mock_catalog):
    """Mock ApplicationContext with catalog_repository wired."""
    ctx = Mock()
    ctx.command_bus = Mock()
    ctx.command_bus.send.return_value = {"provider_id": "filesystem", "created": True}
    ctx.query_bus = Mock()
    ctx.groups = {}
    ctx.discovery_orchestrator = None
    ctx.catalog_repository = mock_catalog
    return ctx


@pytest.fixture
def api_client(mock_context):
    """TestClient with mocked context and catalog."""
    from mcp_hangar.server.api import create_api_router

    with patch("mcp_hangar.server.api.middleware.get_context", return_value=mock_context):
        with patch("mcp_hangar.server.api.catalog.get_context", return_value=mock_context):
            app = create_api_router()
            client = TestClient(app, raise_server_exceptions=False)
            yield client


@pytest.fixture
def api_client_no_catalog(mock_context):
    """TestClient with catalog_repository=None (not configured)."""
    mock_context.catalog_repository = None
    from mcp_hangar.server.api import create_api_router

    with patch("mcp_hangar.server.api.middleware.get_context", return_value=mock_context):
        with patch("mcp_hangar.server.api.catalog.get_context", return_value=mock_context):
            app = create_api_router()
            client = TestClient(app, raise_server_exceptions=False)
            yield client


# ---------------------------------------------------------------------------
# GET /catalog
# ---------------------------------------------------------------------------


class TestListCatalogEntries:
    """Tests for GET /catalog."""

    def test_returns_200_with_entries_list(self, api_client):
        """GET /catalog returns HTTP 200 with entries array."""
        response = api_client.get("/catalog/")
        assert response.status_code == 200
        data = response.json()
        assert "entries" in data
        assert "total" in data

    def test_returns_entries_as_dicts(self, api_client):
        """GET /catalog entries are serialized as dicts with entry_id."""
        response = api_client.get("/catalog/")
        entries = response.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["entry_id"] == "e1"

    def test_returns_404_when_catalog_not_configured(self, api_client_no_catalog):
        """GET /catalog returns 404 when catalog_repository is None."""
        response = api_client_no_catalog.get("/catalog/")
        assert response.status_code == 404

    def test_passes_search_param_to_repository(self, api_client, mock_catalog):
        """GET /catalog?search=file calls list_entries with search='file'."""
        api_client.get("/catalog/?search=file")
        mock_catalog.list_entries.assert_called_with(search="file", tags=None)

    def test_passes_tags_param_to_repository(self, api_client, mock_catalog):
        """GET /catalog?tags=files,local calls list_entries with tags=['files','local']."""
        api_client.get("/catalog/?tags=files,local")
        call_kwargs = mock_catalog.list_entries.call_args[1]
        assert "files" in call_kwargs["tags"]
        assert "local" in call_kwargs["tags"]


# ---------------------------------------------------------------------------
# GET /catalog/{entry_id}
# ---------------------------------------------------------------------------


class TestGetCatalogEntry:
    """Tests for GET /catalog/{entry_id}."""

    def test_returns_200_for_existing_entry(self, api_client):
        """GET /catalog/e1 returns HTTP 200."""
        assert api_client.get("/catalog/e1").status_code == 200

    def test_returns_entry_dict(self, api_client):
        """GET /catalog/e1 returns entry as dict with correct entry_id."""
        data = api_client.get("/catalog/e1").json()
        assert data["entry_id"] == "e1"
        assert data["name"] == "filesystem"

    def test_returns_404_for_missing_entry(self, api_client):
        """GET /catalog/missing returns HTTP 404."""
        assert api_client.get("/catalog/missing").status_code == 404


# ---------------------------------------------------------------------------
# POST /catalog/entries
# ---------------------------------------------------------------------------


class TestAddCatalogEntry:
    """Tests for POST /catalog/entries."""

    def test_returns_201_on_success(self, api_client):
        """POST /catalog/entries returns HTTP 201."""
        response = api_client.post(
            "/catalog/entries",
            json={"name": "my-tool", "description": "Test tool", "mode": "subprocess"},
        )
        assert response.status_code == 201

    def test_returns_entry_id_and_added_true(self, api_client):
        """POST /catalog/entries returns entry_id and added=True."""
        response = api_client.post(
            "/catalog/entries",
            json={"name": "my-tool", "description": "Test tool", "mode": "subprocess"},
        )
        data = response.json()
        assert "entry_id" in data
        assert data["added"] is True

    def test_calls_repo_add_entry(self, api_client, mock_catalog):
        """POST /catalog/entries calls catalog.add_entry once."""
        api_client.post(
            "/catalog/entries",
            json={"name": "my-tool", "description": "Test", "mode": "subprocess"},
        )
        mock_catalog.add_entry.assert_called_once()


# ---------------------------------------------------------------------------
# DELETE /catalog/entries/{entry_id}
# ---------------------------------------------------------------------------


class TestRemoveCatalogEntry:
    """Tests for DELETE /catalog/entries/{entry_id}."""

    def test_returns_200_for_custom_entry(self, api_client):
        """DELETE /catalog/entries/e1 returns HTTP 200 for custom entry."""
        response = api_client.delete("/catalog/entries/e1")
        assert response.status_code == 200

    def test_returns_deleted_true(self, api_client):
        """DELETE /catalog/entries/e1 returns deleted=True."""
        assert api_client.delete("/catalog/entries/e1").json()["deleted"] is True

    def test_returns_422_for_builtin_entry(self, api_client, mock_catalog):
        """DELETE /catalog/entries/e1 returns 422 when entry is builtin (ValueError)."""
        mock_catalog.remove_entry.side_effect = ValueError("Cannot delete builtin catalog entry")
        response = api_client.delete("/catalog/entries/e1")
        assert response.status_code == 422

    def test_returns_404_for_missing_entry(self, api_client, mock_catalog):
        """DELETE /catalog/entries/e1 returns 404 when entry not found (KeyError)."""
        mock_catalog.remove_entry.side_effect = KeyError("not found")
        response = api_client.delete("/catalog/entries/e1")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /catalog/{entry_id}/deploy
# ---------------------------------------------------------------------------


class TestDeployCatalogEntry:
    """Tests for POST /catalog/{entry_id}/deploy."""

    def test_returns_201_on_success(self, api_client):
        """POST /catalog/e1/deploy returns HTTP 201."""
        assert api_client.post("/catalog/e1/deploy").status_code == 201

    def test_returns_provider_id_and_deployed_true(self, api_client):
        """POST /catalog/e1/deploy returns provider_id and deployed=True."""
        data = api_client.post("/catalog/e1/deploy").json()
        assert data["deployed"] is True
        assert data["provider_id"] == "filesystem"  # entry.name used as provider_id

    def test_dispatches_create_provider_command(self, api_client, mock_context):
        """POST /catalog/e1/deploy dispatches CreateProviderCommand."""
        api_client.post("/catalog/e1/deploy")
        calls = mock_context.command_bus.send.call_args_list
        assert any(isinstance(c[0][0], CreateProviderCommand) for c in calls)

    def test_returns_404_for_missing_entry(self, api_client, mock_catalog):
        """POST /catalog/missing/deploy returns 404 when entry not found."""
        mock_catalog.get_entry.return_value = None
        assert api_client.post("/catalog/missing/deploy").status_code == 404
