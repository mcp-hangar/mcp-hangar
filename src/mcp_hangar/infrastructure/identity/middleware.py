"""ASGI middleware for identity extraction and context binding.

This is the core identity middleware path in ``src/``. It only normalizes
request metadata, extracts an ``IdentityContext`` via an ``IIdentityExtractor``,
and binds that context to ``identity_context_var`` for downstream consumers.

It does not authenticate, authorize, or reject requests. Enterprise HTTP auth
middleware composes with the same request-normalization helpers, then adds
authentication/error handling on top.
"""

from __future__ import annotations

from typing import cast

from starlette.types import ASGIApp, Receive, Scope, Send

from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.contracts.identity import IIdentityExtractor
from mcp_hangar.infrastructure.identity.trusted_proxy import headers_from_asgi_scope, resolve_source_ip
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)


class IdentityMiddleware:
    """Starlette/ASGI middleware for identity propagation.

    Extracts identity from each request and sets it in the contextvar.
    Clears the contextvar after the request completes to prevent leakage.

    Usage with Starlette::

        from starlette.applications import Starlette
        app = Starlette(...)
        app.add_middleware(IdentityMiddleware, extractor=my_extractor)
    """

    app: ASGIApp
    _extractor: IIdentityExtractor

    def __init__(self, app: ASGIApp, extractor: IIdentityExtractor) -> None:
        self.app = app
        self._extractor = extractor

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        client = cast(tuple[str, int] | None, scope.get("client"))
        headers = headers_from_asgi_scope(cast(list[tuple[bytes, bytes]] | None, scope.get("headers")))
        source_ip = (
            resolve_source_ip(
                headers=headers,
                client_host=client[0] if client else None,
                default=None,
            )
            or None
        )

        identity_ctx = self._extractor.extract(headers, source_ip=source_ip)

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
