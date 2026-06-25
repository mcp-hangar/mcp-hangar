"""Tests for per-tenant digest pinning (issue #233).

Covers the per-tenant digest-pin feature end-to-end at the unit level:

- ``ToolProjectionRegistry`` config-pin overlay: ``set_config_pin`` /
  ``resolve_pin`` semantics (per-tenant, ``None`` tenant, isolation between
  tenants) and ``clear_config_pins`` (clears pins AND resets enforcement).
- The digest-enforcement mode accessor / mutator and its strict default.
- The executor's core enforcement logic, exercised directly via
  ``DigestValidator`` + a ``DigestPolicy`` built from the registry's
  enforcement mode and a single-pin allowlist (block / warn / audit).
- The full chain ``build_from_tools -> resolve -> validate``, proving the
  ``projection.schema`` shape the executor hands to ``DigestValidator`` hashes
  back to ``projection.digest``.
- The ``server/config.py`` per-server ``tool_projection`` parser
  (``_load_mcp_server_config``): pin + enforcement registration plus
  warn-skipping of invalid enforcement strings and malformed digests.

All example values use NEUTRAL placeholders (no real brand names). No network
or backend I/O is involved -- registries are constructed directly for
isolation and the global singleton is reset around the config-parser test.
"""

from __future__ import annotations

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    ToolProjectionRegistry,
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.digest_computation import compute_tool_digest
from mcp_hangar.domain.services.digest_validator import DigestValidator
from mcp_hangar.domain.value_objects import (
    DigestEnforcement,
    DigestPolicy,
    DigestUnknownPolicy,
    ToolDigest,
)


# ---------------------------------------------------------------------------
# Neutral placeholders
# ---------------------------------------------------------------------------

_SERVER = "srv-alpha"
_TOOL = "search"
_TENANT_A = "tenant-a"
_TENANT_B = "tenant-b"
_CALL_ID = "call-1"

# A syntactically valid sha256 (64 lowercase hex chars) that does NOT match any
# real schema -- used as a deliberately stale / mismatching pin.
_STALE_SHA = "a" * 64


def _tool_schema(
    name: str = _TOOL,
    description: str = "Full-text search over the corpus.",
) -> ToolSchema:
    """Build a representative ToolSchema value object."""
    return ToolSchema(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )


def _stale_pin(tool_name: str = _TOOL) -> ToolDigest:
    """A valid-but-mismatching pin for *tool_name*."""
    return ToolDigest(tool_name=tool_name, sha256=_STALE_SHA)


# ---------------------------------------------------------------------------
# 1. Registry config-pin overlay
# ---------------------------------------------------------------------------


class TestRegistryPinOverlay:
    """set_config_pin / resolve_pin / clear_config_pins semantics."""

    def test_resolve_pin_returns_pin_for_pinned_tenant(self) -> None:
        registry = ToolProjectionRegistry()
        pin = _stale_pin()
        registry.set_config_pin(_SERVER, _TOOL, _TENANT_A, pin)

        assert registry.resolve_pin(_SERVER, _TOOL, _TENANT_A) == pin

    def test_resolve_pin_returns_none_for_different_tenant(self) -> None:
        registry = ToolProjectionRegistry()
        registry.set_config_pin(_SERVER, _TOOL, _TENANT_A, _stale_pin())

        assert registry.resolve_pin(_SERVER, _TOOL, _TENANT_B) is None

    def test_resolve_pin_returns_none_for_none_tenant(self) -> None:
        registry = ToolProjectionRegistry()
        registry.set_config_pin(_SERVER, _TOOL, _TENANT_A, _stale_pin())

        assert registry.resolve_pin(_SERVER, _TOOL, None) is None

    def test_resolve_pin_returns_none_when_unpinned(self) -> None:
        registry = ToolProjectionRegistry()

        assert registry.resolve_pin(_SERVER, _TOOL, _TENANT_A) is None

    def test_clear_config_pins_removes_pins_and_resets_enforcement(self) -> None:
        registry = ToolProjectionRegistry()
        registry.set_config_pin(_SERVER, _TOOL, _TENANT_A, _stale_pin())
        registry.set_digest_enforcement(DigestEnforcement.WARN)

        registry.clear_config_pins()

        assert registry.resolve_pin(_SERVER, _TOOL, _TENANT_A) is None
        assert registry.digest_enforcement() is DigestEnforcement.BLOCK


# ---------------------------------------------------------------------------
# 2. Enforcement default + override
# ---------------------------------------------------------------------------


class TestEnforcementMode:
    """digest_enforcement default, override, and reset on clear."""

    def test_default_enforcement_is_block(self) -> None:
        registry = ToolProjectionRegistry()

        assert registry.digest_enforcement() is DigestEnforcement.BLOCK

    def test_set_digest_enforcement_takes_effect(self) -> None:
        registry = ToolProjectionRegistry()

        registry.set_digest_enforcement(DigestEnforcement.WARN)

        assert registry.digest_enforcement() is DigestEnforcement.WARN

    def test_clear_resets_enforcement_to_block(self) -> None:
        registry = ToolProjectionRegistry()
        registry.set_digest_enforcement(DigestEnforcement.AUDIT)

        registry.clear_config_pins()

        assert registry.digest_enforcement() is DigestEnforcement.BLOCK


# ---------------------------------------------------------------------------
# 3. DigestValidator enforcement via registry policy (executor core logic)
# ---------------------------------------------------------------------------


class TestDigestEnforcementViaPolicy:
    """Mirror the executor: build a DigestPolicy from the registry's mode and a
    single-pin allowlist, then validate the projection schema."""

    def _validate(
        self,
        registry: ToolProjectionRegistry,
        schema: dict,
        pin: ToolDigest,
    ):
        return DigestValidator(
            DigestPolicy(
                enforcement=registry.digest_enforcement(),
                unknown=DigestUnknownPolicy.BLOCK,
                allowlist=frozenset({pin}),
            )
        ).validate_tool(schema, _SERVER, _CALL_ID)

    def test_block_mismatch_blocks_and_emits_event(self) -> None:
        registry = ToolProjectionRegistry()  # default BLOCK
        schema = _tool_schema().to_dict()

        result = self._validate(registry, schema, _stale_pin())

        assert result.blocked is True
        assert result.valid is False
        assert result.event is not None

    def test_matching_pin_passes(self) -> None:
        registry = ToolProjectionRegistry()  # default BLOCK
        schema = _tool_schema().to_dict()
        real_digest = compute_tool_digest(schema)

        result = self._validate(registry, schema, real_digest)

        assert result.blocked is False
        assert result.valid is True
        assert result.event is None

    def test_warn_mismatch_does_not_block_but_emits_event(self) -> None:
        registry = ToolProjectionRegistry()
        registry.set_digest_enforcement(DigestEnforcement.WARN)
        schema = _tool_schema().to_dict()

        result = self._validate(registry, schema, _stale_pin())

        assert result.blocked is False
        assert result.valid is False
        assert result.event is not None

    def test_audit_mismatch_does_not_block_but_emits_event(self) -> None:
        registry = ToolProjectionRegistry()
        registry.set_digest_enforcement(DigestEnforcement.AUDIT)
        schema = _tool_schema().to_dict()

        result = self._validate(registry, schema, _stale_pin())

        assert result.blocked is False
        assert result.valid is False
        assert result.event is not None


# ---------------------------------------------------------------------------
# 4. Full chain: build_from_tools -> resolve -> validate
# ---------------------------------------------------------------------------


class TestFullChain:
    """Prove the projection.schema the executor passes is the canonical shape:
    compute_tool_digest(proj.schema) == proj.digest, and that a stale pin makes
    validate_tool(proj.schema, ...) block."""

    def test_projection_schema_hashes_to_projection_digest(self) -> None:
        registry = ToolProjectionRegistry()
        schema = _tool_schema()
        registry.build_from_tools(_SERVER, [schema])

        proj = registry.resolve(_SERVER, schema.name, _TENANT_A)

        assert proj is not None
        assert compute_tool_digest(proj.schema) == proj.digest

    def test_stale_pin_blocks_projection_schema(self) -> None:
        registry = ToolProjectionRegistry()
        schema = _tool_schema()
        registry.build_from_tools(_SERVER, [schema])
        pin = _stale_pin(schema.name)
        registry.set_config_pin(_SERVER, schema.name, _TENANT_A, pin)

        proj = registry.resolve(_SERVER, schema.name, _TENANT_A)
        assert proj is not None

        resolved_pin = registry.resolve_pin(_SERVER, schema.name, _TENANT_A)
        assert resolved_pin is not None

        result = DigestValidator(
            DigestPolicy(
                enforcement=registry.digest_enforcement(),
                unknown=DigestUnknownPolicy.BLOCK,
                allowlist=frozenset({resolved_pin}),
            )
        ).validate_tool(proj.schema, _SERVER, _CALL_ID)

        assert result.blocked is True
        assert result.event is not None

    def test_matching_pin_passes_projection_schema(self) -> None:
        registry = ToolProjectionRegistry()
        schema = _tool_schema()
        registry.build_from_tools(_SERVER, [schema])

        proj = registry.resolve(_SERVER, schema.name, _TENANT_A)
        assert proj is not None

        # Pin to the projection's own (correct) digest.
        registry.set_config_pin(_SERVER, schema.name, _TENANT_A, proj.digest)
        resolved_pin = registry.resolve_pin(_SERVER, schema.name, _TENANT_A)
        assert resolved_pin is not None

        result = DigestValidator(
            DigestPolicy(
                enforcement=registry.digest_enforcement(),
                unknown=DigestUnknownPolicy.BLOCK,
                allowlist=frozenset({resolved_pin}),
            )
        ).validate_tool(proj.schema, _SERVER, _CALL_ID)

        assert result.blocked is False
        assert result.valid is True


# ---------------------------------------------------------------------------
# 5. Config parsing (server/config.py)
# ---------------------------------------------------------------------------


class TestConfigParsing:
    """The per-server tool_projection block registers pins + enforcement on the
    GLOBAL registry via _load_mcp_server_config, and warn-skips bad input."""

    @pytest.fixture(autouse=True)
    def _reset_registry(self):
        reset_tool_projection_registry()
        yield
        reset_tool_projection_registry()

    @staticmethod
    def _load(spec: dict) -> None:
        # Import lazily so the autouse reset has already run.
        from mcp_hangar.server.config import _load_mcp_server_config

        _load_mcp_server_config(_SERVER, spec)

    def _spec(self, tool_projection: dict) -> dict:
        # Minimal subprocess spec; capabilities present to avoid noise but the
        # parser does not require it for the tool_projection branch.
        return {
            "mode": "subprocess",
            "command": ["/bin/true"],
            "tool_projection": tool_projection,
        }

    def test_pin_and_enforcement_registered(self) -> None:
        self._load(
            self._spec(
                {
                    "digest_enforcement": "warn",
                    "tenant_overrides": {
                        "t1": {"pins": {_TOOL: _STALE_SHA}},
                    },
                }
            )
        )

        registry = get_tool_projection_registry()
        pin = registry.resolve_pin(_SERVER, _TOOL, "t1")
        assert pin is not None
        assert pin.sha256 == _STALE_SHA
        assert pin.tool_name == _TOOL
        assert registry.digest_enforcement() is DigestEnforcement.WARN

    def test_invalid_enforcement_is_skipped(self) -> None:
        self._load(
            self._spec(
                {
                    "digest_enforcement": "not-a-mode",
                    "tenant_overrides": {
                        "t1": {"pins": {_TOOL: _STALE_SHA}},
                    },
                }
            )
        )

        registry = get_tool_projection_registry()
        # Enforcement falls back to the strict default; the (valid) pin still lands.
        assert registry.digest_enforcement() is DigestEnforcement.BLOCK
        assert registry.resolve_pin(_SERVER, _TOOL, "t1") is not None

    def test_malformed_digest_is_skipped(self) -> None:
        self._load(
            self._spec(
                {
                    "digest_enforcement": "block",
                    "tenant_overrides": {
                        "t1": {"pins": {_TOOL: "not-64-hex"}},
                    },
                }
            )
        )

        registry = get_tool_projection_registry()
        # The malformed pin is warn-skipped (no raise), so no pin is registered.
        assert registry.resolve_pin(_SERVER, _TOOL, "t1") is None
        assert registry.digest_enforcement() is DigestEnforcement.BLOCK
