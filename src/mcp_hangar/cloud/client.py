"""HTTP client for Hangar Cloud uplink REST API.

Handles registration, heartbeats, event batches, and state sync.
Auth is license-key based (sent as Bearer token on every request).
"""

from __future__ import annotations

import platform
import socket
import sys
from typing import Any

import httpx

from .config import CloudConfig
from ..logging_config import get_logger

logger = get_logger(__name__)

# Avoid importing version at module level (may not be resolved yet).
_VERSION: str | None = None


def _get_version() -> str:
    global _VERSION
    if _VERSION is None:
        try:
            from importlib.metadata import PackageNotFoundError, version
            _VERSION = version("mcp-hangar")
        except (ImportError, PackageNotFoundError):
            _VERSION = "unknown"
    return _VERSION


class CloudClient:
    """Async HTTP client speaking the /api/uplink/* REST contract."""

    def __init__(self, config: CloudConfig) -> None:
        self._cfg = config
        self._base = config.endpoint.rstrip("/")
        self._agent_id: str | None = None
        self._http = httpx.AsyncClient(
            base_url=self._base,
            timeout=httpx.Timeout(
                connect=config.connect_timeout_s,
                read=config.request_timeout_s,
                write=config.request_timeout_s,
                pool=config.connect_timeout_s,
            ),
            headers={"Authorization": f"Bearer {config.license_key}"},
        )

    # -- lifecycle ----------------------------------------------------------

    async def close(self) -> None:
        await self._http.aclose()

    # -- registration -------------------------------------------------------

    async def register(self) -> dict[str, Any]:
        """POST /api/uplink/register -- returns agent_id, tenant_id, config."""
        hostname = socket.gethostname()
        body = {
            "agent_id": f"uplink-{hostname}",
            "hostname": hostname,
            "version": _get_version(),
            "python_version": sys.version.split()[0],
            "os": platform.system(),
        }
        resp = await self._http.post("/api/uplink/register", json=body)
        resp.raise_for_status()
        data = resp.json()
        self._agent_id = data.get("agent_id", "")
        logger.info(
            "cloud_registered",
            agent_id=self._agent_id,
            tenant_id=data.get("tenant_id", ""),
        )
        return data

    # -- heartbeat ----------------------------------------------------------

    async def heartbeat(self, provider_count: int, healthy_count: int, uptime_s: float) -> None:
        """POST /api/uplink/{id}/heartbeat"""
        if not self._agent_id:
            return
        resp = await self._http.post(
            f"/api/uplink/{self._agent_id}/heartbeat",
            json={
                "provider_count": provider_count,
                "healthy_providers": healthy_count,
                "uptime_seconds": int(uptime_s),
                "status": "online",
            },
        )
        resp.raise_for_status()

    # -- events -------------------------------------------------------------

    async def send_events(self, events: list[dict[str, Any]]) -> None:
        """POST /api/uplink/{id}/events"""
        if not self._agent_id or not events:
            return
        resp = await self._http.post(
            f"/api/uplink/{self._agent_id}/events",
            json={"events": events},
        )
        resp.raise_for_status()

    # -- state sync ---------------------------------------------------------

    async def sync_state(self, providers: list[dict[str, Any]]) -> None:
        """PUT /api/uplink/{id}/state"""
        if not self._agent_id:
            return
        resp = await self._http.put(
            f"/api/uplink/{self._agent_id}/state",
            json={"providers": providers},
        )
        resp.raise_for_status()

    # -- shutdown -----------------------------------------------------------

    async def deregister(self) -> None:
        """Send a final heartbeat with status=shutting_down."""
        if not self._agent_id:
            return
        try:
            await self._http.post(
                f"/api/uplink/{self._agent_id}/heartbeat",
                json={
                    "provider_count": 0,
                    "healthy_providers": 0,
                    "uptime_seconds": 0,
                    "status": "shutting_down",
                },
            )
        except (httpx.HTTPError, OSError):
            pass  # best-effort

    @property
    def agent_id(self) -> str | None:
        return self._agent_id
