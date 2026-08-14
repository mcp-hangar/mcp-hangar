"""`IdentityMiddleware`: the caller identity derived from an inbound request."""

from unittest.mock import AsyncMock, Mock

import pytest


class TestIdentityMiddleware:
    """Tests for the ASGI identity middleware."""

    def _make_extractor(self, identity=None):
        extractor = Mock()
        extractor.extract.return_value = identity
        return extractor

    @pytest.mark.asyncio
    async def test_non_http_scope_passes_through(self):
        from mcp_hangar.infrastructure.identity.middleware import IdentityMiddleware

        app = AsyncMock()
        extractor = self._make_extractor()
        mw = IdentityMiddleware(app=app, extractor=extractor)

        scope = {"type": "lifespan"}
        receive = AsyncMock()
        send = AsyncMock()

        await mw(scope, receive, send)

        app.assert_called_once_with(scope, receive, send)
        extractor.extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_http_scope_extracts_identity_and_sets_context(self):
        from mcp_hangar.context import identity_context_var
        from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
        from mcp_hangar.infrastructure.identity.middleware import IdentityMiddleware

        identity = IdentityContext(
            caller=CallerIdentity(user_id="alice", agent_id="agent-1", session_id="s1", principal_type="user"),
            correlation_id="corr-1",
        )
        extractor = self._make_extractor(identity=identity)

        captured_identity = []

        async def inner_app(scope, receive, send):
            captured_identity.append(identity_context_var.get())

        mw = IdentityMiddleware(app=inner_app, extractor=extractor)

        scope = {
            "type": "http",
            "headers": [
                (b"x-user-id", b"alice"),
                (b"x-agent-id", b"agent-1"),
            ],
        }
        receive = AsyncMock()
        send = AsyncMock()

        await mw(scope, receive, send)

        assert len(captured_identity) == 1
        assert captured_identity[0] is identity

    @pytest.mark.asyncio
    async def test_context_is_reset_after_request(self):
        from mcp_hangar.context import identity_context_var
        from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
        from mcp_hangar.infrastructure.identity.middleware import IdentityMiddleware

        identity = IdentityContext(
            caller=CallerIdentity(user_id="alice", agent_id=None, session_id=None, principal_type="user"),
        )
        extractor = self._make_extractor(identity=identity)

        async def inner_app(scope, receive, send):
            pass

        mw = IdentityMiddleware(app=inner_app, extractor=extractor)
        scope = {"type": "http", "headers": []}
        await mw(scope, AsyncMock(), AsyncMock())

        # After middleware completes, contextvar should be reset
        assert identity_context_var.get() is None

    @pytest.mark.asyncio
    async def test_context_is_reset_even_on_error(self):
        from mcp_hangar.context import identity_context_var
        from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
        from mcp_hangar.infrastructure.identity.middleware import IdentityMiddleware

        identity = IdentityContext(
            caller=CallerIdentity(user_id="bob", agent_id=None, session_id=None, principal_type="user"),
        )
        extractor = self._make_extractor(identity=identity)

        async def inner_app(scope, receive, send):
            raise RuntimeError("app error")

        mw = IdentityMiddleware(app=inner_app, extractor=extractor)
        scope = {"type": "http", "headers": []}

        with pytest.raises(RuntimeError, match="app error"):
            await mw(scope, AsyncMock(), AsyncMock())

        assert identity_context_var.get() is None

    @pytest.mark.asyncio
    async def test_websocket_scope_also_extracts_identity(self):
        from mcp_hangar.context import identity_context_var
        from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
        from mcp_hangar.infrastructure.identity.middleware import IdentityMiddleware

        identity = IdentityContext(
            caller=CallerIdentity(user_id="ws-user", agent_id=None, session_id=None, principal_type="user"),
        )
        extractor = self._make_extractor(identity=identity)

        captured = []

        async def inner_app(scope, receive, send):
            captured.append(identity_context_var.get())

        mw = IdentityMiddleware(app=inner_app, extractor=extractor)
        scope = {"type": "websocket", "headers": []}

        await mw(scope, AsyncMock(), AsyncMock())

        assert len(captured) == 1
        assert captured[0] is identity

    @pytest.mark.asyncio
    async def test_none_identity_still_sets_contextvar(self):
        from mcp_hangar.context import identity_context_var
        from mcp_hangar.infrastructure.identity.middleware import IdentityMiddleware

        extractor = self._make_extractor(identity=None)

        captured = []

        async def inner_app(scope, receive, send):
            captured.append(identity_context_var.get())

        mw = IdentityMiddleware(app=inner_app, extractor=extractor)
        scope = {"type": "http", "headers": []}

        await mw(scope, AsyncMock(), AsyncMock())

        assert captured[0] is None

    @pytest.mark.asyncio
    async def test_headers_decoded_from_asgi_scope(self):
        from mcp_hangar.infrastructure.identity.middleware import IdentityMiddleware

        captured_headers = []

        def capture_extract(headers, source_ip=None):
            captured_headers.append((headers, source_ip))
            return None

        extractor = Mock()
        extractor.extract = capture_extract

        async def inner_app(scope, receive, send):
            pass

        mw = IdentityMiddleware(app=inner_app, extractor=extractor)
        scope = {
            "type": "http",
            "headers": [
                (b"content-type", b"application/json"),
                (b"authorization", b"Bearer xyz"),
            ],
        }

        await mw(scope, AsyncMock(), AsyncMock())

        assert len(captured_headers) == 1
        assert captured_headers[0][0]["content-type"] == "application/json"
        assert captured_headers[0][0]["authorization"] == "Bearer xyz"
        assert captured_headers[0][1] is None
