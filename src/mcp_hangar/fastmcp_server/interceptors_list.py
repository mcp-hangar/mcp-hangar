"""SEP-1763 interceptor HTTP endpoints (``interceptors/list`` + ``interceptor/invoke``).

Exposes mcp-hangar as a discoverable, invocable interceptor per the SEP-1763
specification. Registered as custom HTTP routes on the FastMCP server since the
MCP SDK does not yet support custom JSON-RPC method registration for
non-standard methods.

Pinned spec revision
--------------------
The SEP-1763 wire format is reconciled against MCP PR #2624
("SEP-2624: Interceptors for the Model Context Protocol"), which is **OPEN**
and can still move. We pin to an explicit upstream revision so the shape is
reproducible; re-pin (and review the diff) when bumping:

    repo:   modelcontextprotocol/modelcontextprotocol
    pr:     #2624 (branch ``SEP-1763``, state OPEN)
    head:   8029c78ae88a3aadeb83c2f63cbbf2f04ec43e3a
    as of:  2026-06-11

The legacy ``interceptors/list`` (v1.2) response shape is preserved as the
default so clients that do not negotiate the extension are unaffected. The
PR #2624-aligned shape (``hooks`` array with ``events`` + ``phase``, and the
``interceptor/invoke`` method) is served ONLY when the extension is negotiated
via the capability header/query below.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_hangar import __version__
from mcp_hangar.domain.events import InterceptorInvoked
from mcp_hangar.domain.value_objects.hook import HookPhase
from mcp_hangar.infrastructure.event_bus import get_event_bus
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)

# --- Pinned upstream revision (MCP PR #2624 / SEP-1763). -----------------------
# Keep in sync with the module docstring and tests/unit/test_interceptors_list_schema.py.
SEP_2624_PR = 2624
SEP_2624_PIN = "8029c78ae88a3aadeb83c2f63cbbf2f04ec43e3a"

# --- Capability negotiation (WS-3 T3 posture: off by default, opt-in). ---------
# A client signals it has negotiated the PR #2624 extension by sending the header
# ``MCP-Interceptor-Ext: sep-2624`` (or the ``?ext=sep-2624`` query param on GET).
# When absent the extension stays hidden: ``interceptors/list`` returns the legacy
# v1.2 shape and ``interceptor/invoke`` is not exposed (404).
INTERCEPTOR_EXT_HEADER = "MCP-Interceptor-Ext"
INTERCEPTOR_EXT_VALUE = "sep-2624"

# Interceptor types per PR #2624 (``validation`` / ``mutation``).
_VALIDATOR = "mcp-hangar-validator"
_MUTATOR = "mcp-hangar-mutator"

_VALIDATOR_EVENTS = ("tools/call", "tools/list")
_MUTATOR_EVENTS = ("tools/call",)


def interceptor_ext_negotiated(request: Request) -> bool:
    """Return whether the caller negotiated the PR #2624 interceptor extension.

    Accepts the negotiation signal via the ``MCP-Interceptor-Ext`` request
    header or, for GET convenience, the ``ext`` query parameter.
    """
    header = request.headers.get(INTERCEPTOR_EXT_HEADER, "")
    if header.strip().lower() == INTERCEPTOR_EXT_VALUE:
        return True
    return request.query_params.get("ext", "").strip().lower() == INTERCEPTOR_EXT_VALUE


# --- interceptors/list ---------------------------------------------------------


def interceptors_list_response() -> dict[str, Any]:
    """Build the legacy (v1.2) ``interceptors/list`` payload.

    Unchanged default shape for clients that have NOT negotiated the PR #2624
    extension: flat ``supportedEvents``/``modes`` arrays and ``validator``/
    ``mutator`` type labels.
    """
    return {
        "interceptors": [
            {
                "name": _VALIDATOR,
                "version": __version__,
                "type": "validator",
                "supportedEvents": list(_VALIDATOR_EVENTS),
                "modes": ["audit", "enforce"],
                "trustBoundary": "host",
            },
            {
                "name": _MUTATOR,
                "version": __version__,
                "type": "mutator",
                "supportedEvents": list(_MUTATOR_EVENTS),
                "modes": ["enforce"],
                "trustBoundary": "host",
            },
        ],
    }


def interceptors_list_response_v2() -> dict[str, Any]:
    """Build the PR #2624-aligned ``interceptors/list`` payload.

    Reconciled shape (pinned to :data:`SEP_2624_PIN`): ``validation``/
    ``mutation`` type labels and a ``hooks`` array where each entry carries
    ``events`` + ``phase`` (``request``/``response``). Hangar's interceptors
    hook the request phase only.
    """
    return {
        "interceptors": [
            {
                "name": _VALIDATOR,
                "version": __version__,
                "description": "Validates tool calls/listings at the host trust boundary.",
                "type": "validation",
                "hooks": [
                    {"events": list(_VALIDATOR_EVENTS), "phase": "request"},
                ],
                "mode": "active",
                "trustBoundary": "host",
            },
            {
                "name": _MUTATOR,
                "version": __version__,
                "description": "Mutates tool-call payloads at the host trust boundary.",
                "type": "mutation",
                "hooks": [
                    {"events": list(_MUTATOR_EVENTS), "phase": "request"},
                ],
                "mode": "active",
                "trustBoundary": "host",
            },
        ],
    }


async def interceptors_list_handler(request: Request) -> JSONResponse:
    """Handle ``GET /interceptors/list``.

    Returns the PR #2624-aligned shape when the extension is negotiated,
    otherwise the unchanged legacy v1.2 shape.
    """
    if interceptor_ext_negotiated(request):
        return JSONResponse(interceptors_list_response_v2())
    return JSONResponse(interceptors_list_response())


# --- interceptor/invoke --------------------------------------------------------

# Registry of Hangar interceptors keyed by name -> (type, supported events).
_INTERCEPTORS: dict[str, tuple[str, tuple[str, ...]]] = {
    _VALIDATOR: ("validation", _VALIDATOR_EVENTS),
    _MUTATOR: ("mutation", _MUTATOR_EVENTS),
}


def _jsonrpc_error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _event_supported(event: str, supported: tuple[str, ...]) -> bool:
    return event == "*" or "*" in supported or event in supported


def interceptor_invoke_result(params: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a single ``interceptor/invoke`` call and return its result.

    Validates params against the pinned PR #2624 shape, publishes a phase-aware
    hook onto the event bus (exercising request/response delivery), and returns
    a ``ValidationResult`` or ``MutationResult``.

    Hangar's built-in interceptors are conformance-shaped no-ops here: the
    validator passes (``valid: true``) and the mutator is a pass-through
    (``modified: false``). This reconciles the wire format and phase-aware
    delivery; real enforcement wiring is out of scope for this reconciliation.

    Raises:
        ValueError: If params are missing/invalid (mapped to JSON-RPC -32602).
    """
    name = params.get("name")
    event = params.get("event")
    phase = params.get("phase")

    if not isinstance(name, str) or name not in _INTERCEPTORS:
        raise ValueError(f"unknown interceptor: {name!r}")
    if not isinstance(event, str) or not event:
        raise ValueError("event must be a non-empty string")
    if phase not in ("request", "response"):
        raise ValueError("phase must be 'request' or 'response'")

    itype, supported = _INTERCEPTORS[name]
    if not _event_supported(event, supported):
        raise ValueError(f"interceptor {name!r} does not hook event {event!r}")

    started = time.monotonic()
    correlation_id = str(uuid.uuid4())

    # Phase-aware delivery: wrap the invocation as a Hook on the event bus.
    hook_phase = HookPhase.REQUEST if phase == "request" else HookPhase.RESPONSE
    try:
        get_event_bus().publish_hook(
            InterceptorInvoked(
                interceptor=name,
                lifecycle_event=event,
                phase=phase,
                correlation_id=correlation_id,
            ),
            hook_phase,
        )
    except Exception as exc:  # noqa: BLE001 -- fault-barrier: delivery must not fail the invoke
        logger.warning("interceptor_hook_delivery_failed", interceptor=name, error=str(exc))

    duration_ms = (time.monotonic() - started) * 1000.0

    if itype == "validation":
        return {
            "interceptor": name,
            "type": "validation",
            "phase": phase,
            "durationMs": duration_ms,
            "valid": True,
        }
    return {
        "interceptor": name,
        "type": "mutation",
        "phase": phase,
        "durationMs": duration_ms,
        "modified": False,
        "payload": params.get("payload"),
    }


async def interceptor_invoke_handler(request: Request) -> JSONResponse:
    """Handle ``POST /interceptor/invoke`` (PR #2624).

    Gated by capability negotiation: without the negotiated extension the
    endpoint is not exposed (404). Accepts a JSON-RPC envelope
    ``{jsonrpc, id, method: "interceptor/invoke", params}`` and returns a
    JSON-RPC result/error envelope.
    """
    if not interceptor_ext_negotiated(request):
        return JSONResponse({"error": "not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 -- malformed body -> JSON-RPC parse error
        return JSONResponse(_jsonrpc_error(None, -32700, "Parse error"), status_code=400)

    if not isinstance(body, dict):
        return JSONResponse(_jsonrpc_error(None, -32600, "Invalid Request"), status_code=400)

    req_id = body.get("id")
    if body.get("method") != "interceptor/invoke":
        return JSONResponse(_jsonrpc_error(req_id, -32601, "Method not found"), status_code=404)

    params = body.get("params")
    if not isinstance(params, dict):
        return JSONResponse(_jsonrpc_error(req_id, -32602, "Invalid params"), status_code=400)

    try:
        result = interceptor_invoke_result(params)
    except ValueError as exc:
        return JSONResponse(_jsonrpc_error(req_id, -32602, str(exc)), status_code=400)

    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})


def register_interceptors_list(mcp: FastMCP) -> None:
    """Register the interceptor HTTP routes on *mcp*.

    Registers ``GET /interceptors/list`` (always) and ``POST /interceptor/invoke``
    (gated at request time by capability negotiation).
    """
    mcp.custom_route("/interceptors/list", methods=["GET"], name="interceptors_list")(interceptors_list_handler)
    mcp.custom_route("/interceptor/invoke", methods=["POST"], name="interceptor_invoke")(interceptor_invoke_handler)
