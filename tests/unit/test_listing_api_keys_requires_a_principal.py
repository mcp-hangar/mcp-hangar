"""`GET /api/auth/keys` without `principal_id` is a 400, not an empty list (#1697).

The docstring says the parameter is required; the route answered 200 and `[]`,
which reads as "this principal has no keys".
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from mcp_hangar.auth.api import routes


def _request(query: str = "") -> Request:
    return Request({"type": "http", "method": "GET", "path": "/keys", "query_string": query.encode(), "headers": []})


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "principal_id=", "principal_id=%20%20", "include_revoked=false"])
async def test_a_missing_or_blank_principal_is_a_400(query):
    with patch.object(routes, "dispatch_query", AsyncMock()) as dispatch:
        response = await routes.list_api_keys(_request(query))

    assert response.status_code == 400
    assert json.loads(response.body)["error"]["message"] == "principal_id is required"
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_a_principal_still_lists_its_keys():
    answer = {"principal_id": "user:ann", "keys": [], "total": 0, "active": 0}
    with patch.object(routes, "dispatch_query", AsyncMock(return_value=answer)) as dispatch:
        response = await routes.list_api_keys(_request("principal_id=user:ann&include_revoked=false"))

    assert response.status_code == 200
    assert json.loads(response.body) == answer
    query = dispatch.call_args.args[0]
    assert (query.principal_id, query.include_revoked) == ("user:ann", False)
