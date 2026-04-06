"""ASGI middleware for identity extraction and context binding.

Intercepts incoming HTTP requests, extracts CallerIdentity from headers
(or JWT), and binds it to the contextvar so that all downstream code
(including Provider.invoke_tool) can read it.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Callable

import structlog

from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.contracts.identity import IIdentityExtractor

logger = structlog.get_logger(__name__)


class IdentityMiddleware:
    """Starlette/ASGI middleware for identity propagation.

    Extracts identity from each request and sets it in the contextvar.
    Clears the contextvar after the request completes to prevent leakage.

    Usage with Starlette::

        from starlette.applications import Starlette
        app = Starlette(...)
        app.add_middleware(IdentityMiddleware, extractor=my_extractor)
    """

    def __init__(self, app: Any, extractor: IIdentityExtractor) -> None:
        self.app = app
        self._extractor = extractor

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # Extract headers from ASGI scope
        headers: dict[str, str] = {}
        for name_bytes, value_bytes in scope.get("headers", []):
            headers[name_bytes.decode("latin-1").lower()] = value_bytes.decode("latin-1")

        identity_ctx = self._extractor.extract(headers)

        # Bind to contextvar for the duration of the request
        token = identity_context_var.set(identity_ctx)
        try:
            if identity_ctx and identity_ctx.caller:
                logger.debug(
                    "identity_bound",
                    user_id=identity_ctx.caller.user_id,
                    agent_id=identity_ctx.caller.agent_id,
                    principal_type=identity_ctx.caller.principal_type,
                )
            await self.app(scope, receive, send)
        finally:
            identity_context_var.reset(token)

