"""Schema validation for interceptors/list response.

Validates our response against a JSON Schema derived from the SEP-1763
Interceptor interface definition at:

    modelcontextprotocol/experimental-ext-interceptors @ 5bd7ab4

The upstream repo does not publish a machine-readable JSON Schema, so we
maintain a local schema that mirrors the spec. When bumping the pinned
SHA, review the upstream diff and update INTERCEPTOR_SCHEMA accordingly.
"""

from __future__ import annotations

import jsonschema
import pytest

from mcp_hangar.fastmcp_server.interceptors_list import interceptors_list_response

# Local schema derived from SEP-1763 Interceptor interface (pinned above).
# Our response uses a simplified shape: flat "supportedEvents" and "modes"
# arrays instead of the nested "hook" object, and "validator"/"mutator"
# type labels instead of "validation"/"mutation". This is intentional --
# the endpoint predates the SEP finalisation and will be aligned in a
# future breaking change.
INTERCEPTOR_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["interceptors"],
    "additionalProperties": False,
    "properties": {
        "interceptors": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "type"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "version": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["validator", "mutator"],
                    },
                    "supportedEvents": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "minItems": 1,
                    },
                    "modes": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["audit", "enforce"],
                        },
                        "minItems": 1,
                    },
                    "trustBoundary": {"type": "string"},
                },
            },
        },
    },
}


class TestInterceptorsListSchema:
    def test_response_validates_against_schema(self):
        response = interceptors_list_response()
        jsonschema.validate(response, INTERCEPTOR_SCHEMA)

    def test_names_are_unique(self):
        response = interceptors_list_response()
        names = [i["name"] for i in response["interceptors"]]
        assert len(names) == len(set(names)), f"Interceptor names must be unique per SEP-1763. Duplicates: {names}"

    def test_schema_rejects_missing_name(self):
        bad = {"interceptors": [{"type": "validator"}]}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, INTERCEPTOR_SCHEMA)

    def test_schema_rejects_unknown_type(self):
        bad = {"interceptors": [{"name": "x", "type": "unknown"}]}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, INTERCEPTOR_SCHEMA)
