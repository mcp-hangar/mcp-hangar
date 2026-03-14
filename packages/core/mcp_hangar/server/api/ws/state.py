"""WebSocket endpoint for periodic provider/group state snapshots."""

import asyncio
import json
import time

from starlette.websockets import WebSocket, WebSocketDisconnect

from ....logging_config import get_logger
from ..serializers import HangarJSONEncoder
from ...context import get_context

logger = get_logger(__name__)

_DEFAULT_INTERVAL_S = 2.0
_MIN_INTERVAL_S = 0.5
_MAX_INTERVAL_S = 60.0
_INTERVAL_CONFIG_TIMEOUT_S = 2.0


async def ws_state_endpoint(websocket: WebSocket) -> None:
    """Stream periodic provider and group state snapshots to a connected client.

    Protocol:
    1. Client connects; server accepts.
    2. Client MAY send interval config within 2 seconds:
       {"interval": N}  -- N in seconds, clamped to [0.5, 60.0]. Default: 2.0.
    3. Server sends state_snapshot messages at the configured interval.
    4. On disconnect, the loop exits cleanly.

    Snapshot payload:
    {
        "type": "state_snapshot",
        "timestamp": <unix float>,
        "providers": [<provider.to_dict()>, ...],
        "groups": [<group.to_status_dict()>, ...]
    }

    Args:
        websocket: Starlette WebSocket instance.
    """
    await websocket.accept()

    # Read optional interval config from client.
    interval = _DEFAULT_INTERVAL_S
    try:
        msg = await asyncio.wait_for(
            websocket.receive_json(),
            timeout=_INTERVAL_CONFIG_TIMEOUT_S,
        )
        raw_interval = float(msg.get("interval", _DEFAULT_INTERVAL_S))
        interval = max(_MIN_INTERVAL_S, min(_MAX_INTERVAL_S, raw_interval))
    except (TimeoutError, Exception):  # noqa: BLE001
        interval = _DEFAULT_INTERVAL_S

    logger.info("ws_state_connected", interval=interval)
    context = get_context()

    try:
        while True:
            # Snapshot state outside any lock -- GIL provides sufficient safety for list().
            providers_snapshot = list(context.providers.values())
            groups_snapshot = list(context.groups.values())

            payload = {
                "type": "state_snapshot",
                "timestamp": time.time(),
                "providers": [p.to_dict() for p in providers_snapshot],
                "groups": [g.to_status_dict() for g in groups_snapshot],
            }
            await websocket.send_text(json.dumps(payload, cls=HangarJSONEncoder))
            await asyncio.sleep(interval)
    except WebSocketDisconnect:
        logger.debug("ws_state_disconnected")
    finally:
        logger.info("ws_state_cleanup_done")
