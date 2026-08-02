"""OPA's verdict is a boolean or it is a denial -- never merely truthy.

``evaluate()`` used to do ``allowed = result.get("result", False)`` and then
``if allowed:``. Rego rules are routinely authored to return something other
than a bare boolean, and every one of those shapes is truthy:

* ``{"result": {"allow": true, "reason": "..."}}`` -- a partial object rule
* ``{"result": "deny"}`` -- a string verdict, which granted access while saying
  the opposite
* ``{"result": ["some", "trace"]}`` -- a set/array rule

So a policy misconfiguration did not fail closed; it granted everything. These
tests pin each shape to a denial, and pin that a real boolean still works in
both directions -- a guard that denied unconditionally would pass the denial
assertions alone.
"""

from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar.auth.infrastructure.opa_authorizer import OPAAuthorizer


def _authorizer_returning(payload: dict) -> OPAAuthorizer:
    """An OPAAuthorizer whose HTTP client returns ``payload`` as the OPA body."""
    authorizer = OPAAuthorizer(opa_url="http://opa.invalid", policy_path="hangar/allow")
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    client = MagicMock()
    client.post.return_value = response
    authorizer._client = client
    return authorizer


class TestNonBooleanVerdictsDeny:
    @pytest.mark.parametrize(
        "payload,label",
        [
            ({"result": {"allow": True, "reason": "ok"}}, "object rule"),
            ({"result": "deny"}, "string verdict"),
            ({"result": "allow"}, "string verdict, permissive wording"),
            ({"result": ["trace"]}, "array rule"),
            ({"result": 1}, "integer"),
            ({"result": {"nested": {"allow": True}}}, "nested object"),
        ],
    )
    def test_truthy_non_boolean_is_denied(self, payload, label):
        result = _authorizer_returning(payload).evaluate({"principal": {"id": "user:alice"}})
        assert not result.allowed, f"{label} was treated as an allow"
        assert result.reason == "opa_error:non_boolean_result"

    def test_falsy_non_boolean_is_also_denied_as_a_type_error(self):
        """Denying for the right reason matters for operability.

        An empty object denied before this change too, but as "opa_denied" --
        indistinguishable from a policy that genuinely said no.
        """
        result = _authorizer_returning({"result": {}}).evaluate({})
        assert not result.allowed
        assert result.reason == "opa_error:non_boolean_result"


class TestUndefinedResultDenies:
    def test_missing_result_key_is_a_configuration_error(self):
        """OPA omits `result` when the queried rule is undefined."""
        result = _authorizer_returning({}).evaluate({})
        assert not result.allowed
        assert result.reason == "opa_error:undefined_result"


class TestBooleanVerdictsStillWork:
    """The guard must not have turned OPA into a permanent denial."""

    def test_true_allows(self):
        result = _authorizer_returning({"result": True}).evaluate({})
        assert result.allowed
        assert result.reason == "opa_policy"

    def test_false_denies_as_policy_not_as_error(self):
        result = _authorizer_returning({"result": False}).evaluate({})
        assert not result.allowed
        assert result.reason == "opa_denied"


class TestTransportFailuresStillFailClosed:
    def test_connection_error_denies(self):
        import httpx

        authorizer = OPAAuthorizer(opa_url="http://opa.invalid", policy_path="hangar/allow")
        client = MagicMock()
        client.post.side_effect = httpx.ConnectError("no route")
        authorizer._client = client

        with patch.dict("sys.modules"):
            result = authorizer.evaluate({})

        assert not result.allowed
        assert result.reason == "opa_error:connection_failed"
