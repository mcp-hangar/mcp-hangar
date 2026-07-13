"""Schema validation for interceptors/list response.

Validates our response against a JSON Schema derived from the SEP-1763
Interceptor interface definition at:

    modelcontextprotocol/experimental-ext-interceptors @ 99bc7c9

Re-pinned 5bd7ab4 -> 99bc7c9 for issue #401 (6 commits ahead). The notable
drift is upstream #25 ("Align capability key to SEP-2133 extensions format"),
which moved the interceptor capability to the reverse-DNS key
``io.modelcontextprotocol/interceptors``; the SEP prose ``Interceptor``
interface at 99bc7c9 also carries optional ``failOpen`` / ``priorityHint`` /
``compat`` / ``configSchema`` fields, reflected in INTERCEPTOR_SCHEMA_V2 below.

The upstream repo does not publish a machine-readable JSON Schema, so we
maintain a local schema that mirrors the spec. When bumping the pinned
SHA, review the upstream diff and update INTERCEPTOR_SCHEMA accordingly.
"""

from __future__ import annotations

import jsonschema
import pytest

from mcp_hangar.fastmcp_server.interceptors_list import (
    interceptors_list_response,
    interceptors_list_response_v2,
)

# Local schema derived from SEP-1763 Interceptor interface (pinned above).
# The DEFAULT (un-negotiated) response uses a simplified legacy shape: flat
# "supportedEvents"/"modes" arrays and "validator"/"mutator" type labels. This
# is preserved for backward compatibility. The PR #2624-aligned shape (hooks
# array with events + phase, and "validation"/"mutation" labels) is served only
# when the extension is negotiated -- see INTERCEPTOR_SCHEMA_V2 and
# tests/unit/test_interceptor_invoke.py.
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


# PR #2624-aligned shape, mirrored against the SEP prose Interceptor interface
# at experimental-ext-interceptors 99bc7c9. Each interceptor carries a "hooks"
# array of {events, phase} and "validation"/"mutation" type labels, plus the
# optional failOpen / priorityHint / compat / configSchema fields the interface
# defines. "trustBoundary" is a Hangar-local extension field (not in the SEP
# interface); it is retained on our response and allowed here.
INTERCEPTOR_SCHEMA_V2 = {
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
                "required": ["name", "type", "hooks"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "version": {"type": "string"},
                    "description": {"type": "string"},
                    "type": {"type": "string", "enum": ["validation", "mutation"]},
                    "mode": {"type": "string", "enum": ["active", "audit"]},
                    "failOpen": {"type": "boolean"},
                    "priorityHint": {
                        "oneOf": [
                            {"type": "integer"},
                            {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "request": {"type": "integer"},
                                    "response": {"type": "integer"},
                                },
                            },
                        ],
                    },
                    "compat": {
                        "type": "object",
                        "required": ["minProtocol"],
                        "additionalProperties": False,
                        "properties": {
                            "minProtocol": {"type": "string"},
                            "maxProtocol": {"type": "string"},
                        },
                    },
                    "configSchema": {"type": "object"},
                    # Hangar-local extension field (not in the SEP interface).
                    "trustBoundary": {"type": "string"},
                    "hooks": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["events", "phase"],
                            "additionalProperties": False,
                            "properties": {
                                "events": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {"type": "string", "minLength": 1},
                                },
                                "phase": {"type": "string", "enum": ["request", "response"]},
                            },
                        },
                    },
                },
            },
        },
        # ListInterceptorsResult carries an optional pagination cursor upstream.
        "nextCursor": {"type": "string"},
    },
}


class TestInterceptorsListSchemaV2:
    def test_v2_response_validates_against_schema(self):
        jsonschema.validate(interceptors_list_response_v2(), INTERCEPTOR_SCHEMA_V2)

    def test_v2_schema_rejects_missing_hooks(self):
        bad = {"interceptors": [{"name": "x", "type": "validation"}]}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, INTERCEPTOR_SCHEMA_V2)

    def test_v2_schema_rejects_bad_phase(self):
        bad = {
            "interceptors": [
                {"name": "x", "type": "mutation", "hooks": [{"events": ["tools/call"], "phase": "sideways"}]}
            ]
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, INTERCEPTOR_SCHEMA_V2)

    def test_v2_schema_accepts_99bc7c9_optional_fields(self):
        # Optional fields added to the SEP Interceptor interface at 99bc7c9.
        entry = {
            "interceptors": [
                {
                    "name": "content-filter",
                    "type": "mutation",
                    "hooks": [{"events": ["tools/call"], "phase": "request"}],
                    "failOpen": False,
                    "priorityHint": {"request": -1000, "response": 1000},
                    "compat": {"minProtocol": "2025-06-18"},
                    "configSchema": {"type": "object"},
                }
            ],
            "nextCursor": "opaque",
        }
        jsonschema.validate(entry, INTERCEPTOR_SCHEMA_V2)
