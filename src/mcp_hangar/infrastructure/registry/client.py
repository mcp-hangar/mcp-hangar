"""HTTP client for the official MCP server registry.

This module provides a concrete implementation of IRegistryClient that
communicates with the official MCP server registry at
registry.modelcontextprotocol.io.
"""

import asyncio
from typing import Any

import httpx

from ...domain.contracts.registry import IRegistryClient, PackageInfo, ServerDetails, ServerSummary, TransportInfo
from ...domain.exceptions import RegistryConnectionError, RegistryServerNotFoundError
from ...logging_config import get_logger
from .cache import RegistryCache

logger = get_logger(__name__)


class RegistryClient(IRegistryClient):
    """HTTP client for the official MCP server registry.

    Implements IRegistryClient protocol with caching, retry logic,
    and proper error handling.

    Attributes:
        base_url: Base URL of the registry API.
        timeout: Request timeout in seconds.
        max_retries: Maximum number of retry attempts.
        cache: Optional cache for registry responses.
    """

    DEFAULT_BASE_URL = "https://registry.modelcontextprotocol.io/v0"
    DEFAULT_TIMEOUT = 10.0
    DEFAULT_MAX_RETRIES = 3
    RETRY_BACKOFF_FACTOR = 0.5

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        cache: RegistryCache | None = None,
        user_agent: str | None = None,
    ):
        """Initialize the registry client.

        Args:
            base_url: Base URL of the registry API.
            timeout: Request timeout in seconds.
            max_retries: Maximum number of retry attempts.
            cache: Optional cache for registry responses.
            user_agent: Optional User-Agent header value.
        """
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._cache = cache
        self._user_agent = user_agent or self._get_default_user_agent()
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client with connection pooling."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": self._user_agent},
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client and release connections."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _get_default_user_agent(self) -> str:
        """Get the default User-Agent string."""
        try:
            from ... import __version__

            return f"mcp-hangar/{__version__}"
        except ImportError:
            return "mcp-hangar/unknown"

    async def search(self, query: str, limit: int = 10) -> list[ServerSummary]:
        """Search for MCP servers matching the query.

        Args:
            query: Search query string.
            limit: Maximum number of results to return.

        Returns:
            List of matching server summaries.

        Raises:
            RegistryConnectionError: If the registry request fails.
        """
        cache_key = f"search:{query}:{limit}"

        if self._cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug("registry_cache_hit", key=cache_key)
                return cached

        url = f"{self._base_url}/servers"
        params = {"search": query, "limit": str(limit)}

        try:
            data = await self._request("GET", url, params=params)
        except RegistryConnectionError:
            raise

        servers = data.get("servers", [])
        results = [self._parse_server_summary(s) for s in servers]

        if self._cache:
            self._cache.set(cache_key, results)

        return results

    async def get_server(self, server_id: str) -> ServerDetails | None:
        """Get detailed information about a specific MCP server.

        Since the registry API doesn't have a direct GET endpoint for individual
        servers, this method searches for the server by name and returns the
        first exact match.

        Args:
            server_id: Server name/identifier in the registry.

        Returns:
            Server details if found, None if not found.

        Raises:
            RegistryConnectionError: If the registry request fails.
        """
        cache_key = f"server:{server_id}"

        if self._cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug("registry_cache_hit", key=cache_key)
                return cached

        # Search for the server by name
        url = f"{self._base_url}/servers"
        params = {"search": server_id, "limit": "10"}

        try:
            data = await self._request("GET", url, params=params)
        except RegistryConnectionError:
            raise

        # Find exact match in results
        servers = data.get("servers", [])
        for server_data in servers:
            details = self._parse_server_details(server_data)
            if details.id == server_id or details.name == server_id:
                if self._cache:
                    self._cache.set(cache_key, details)
                return details

        return None

    async def _request(
        self,
        method: str,
        url: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Make an HTTP request with retry logic.

        Args:
            method: HTTP method (GET, POST, etc.).
            url: Request URL.
            params: Optional query parameters.

        Returns:
            Parsed JSON response.

        Raises:
            RegistryConnectionError: If all retries fail.
            RegistryServerNotFoundError: If server returns 404.
        """
        last_error: Exception | None = None
        client = await self._get_client()

        for attempt in range(self._max_retries):
            try:
                response = await client.request(
                    method,
                    url,
                    params=params,
                )

                if response.status_code == 404:
                    raise RegistryServerNotFoundError(url.split("/")[-1])

                response.raise_for_status()
                return response.json()

            except RegistryServerNotFoundError:
                raise

            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(
                    "registry_request_timeout",
                    url=url,
                    attempt=attempt + 1,
                    max_retries=self._max_retries,
                )

            except httpx.HTTPStatusError as e:
                last_error = e
                logger.warning(
                    "registry_request_http_error",
                    url=url,
                    status_code=e.response.status_code,
                    attempt=attempt + 1,
                    max_retries=self._max_retries,
                )

            except httpx.RequestError as e:
                last_error = e
                logger.warning(
                    "registry_request_error",
                    url=url,
                    error=str(e),
                    attempt=attempt + 1,
                    max_retries=self._max_retries,
                )

            if attempt < self._max_retries - 1:
                backoff = self.RETRY_BACKOFF_FACTOR * (2**attempt)
                await asyncio.sleep(backoff)

        raise RegistryConnectionError(url, str(last_error))

    def _parse_server_summary(self, data: dict[str, Any]) -> ServerSummary:
        """Parse a server summary from API response.

        The registry API returns servers in this format:
        {
            "server": {"name": "...", "description": "..."},
            "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active"}}
        }

        Args:
            data: Server data from API response.

        Returns:
            Parsed ServerSummary.
        """
        server_data = data.get("server", data)
        meta = data.get("_meta", {})
        official_meta = meta.get("io.modelcontextprotocol.registry/official", {})

        server_name = server_data.get("name", "")

        return ServerSummary(
            id=server_name,
            name=server_name,
            description=server_data.get("description", ""),
            is_official=official_meta.get("status") == "active",
        )

    def _parse_server_details(self, data: dict[str, Any]) -> ServerDetails:
        """Parse server details from API response.

        The registry API returns servers in this format:
        {
            "server": {
                "name": "...",
                "description": "...",
                "packages": [{"registryType": "npm", "identifier": "...", ...}],
                "repository": {"url": "..."}
            },
            "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active"}}
        }

        Args:
            data: Server data from API response.

        Returns:
            Parsed ServerDetails.
        """
        server_data = data.get("server", data)
        meta = data.get("_meta", {})
        official_meta = meta.get("io.modelcontextprotocol.registry/official", {})

        server_name = server_data.get("name", "")
        repository = server_data.get("repository", {})

        # Parse packages
        packages = []
        for pkg in server_data.get("packages", []):
            transport_data = pkg.get("transport", {})
            transport = TransportInfo(
                type=transport_data.get("type", "stdio"),
                args=transport_data.get("args"),
            )
            packages.append(
                PackageInfo(
                    registry_type=pkg.get("registryType", pkg.get("registry_type", "")),
                    identifier=pkg.get("identifier", ""),
                    version=pkg.get("version"),
                    transport=transport,
                    file_sha256=pkg.get("fileSha256", pkg.get("file_sha256")),
                )
            )

        # Extract required environment variables from packages
        required_env_vars = []
        for pkg in server_data.get("packages", []):
            for env_var in pkg.get("environmentVariables", []):
                if env_var.get("isRequired", False) or env_var.get("isSecret", False):
                    var_name = env_var.get("name", "")
                    if var_name and var_name not in required_env_vars:
                        required_env_vars.append(var_name)

        return ServerDetails(
            id=server_name,
            name=server_name,
            description=server_data.get("description", ""),
            vendor=None,
            source_url=repository.get("url") if repository else None,
            is_official=official_meta.get("status") == "active",
            packages=packages,
            required_env_vars=required_env_vars,
        )
