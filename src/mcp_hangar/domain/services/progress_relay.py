"""Relay upstream ``notifications/progress`` back to the caller that asked (#883).

A caller attaches ``_meta.progressToken`` to a ``tools/call``; the upstream
only emits progress when asked, and until this existed the gateway never
asked -- the recorded wire showed ``traceparent`` and nothing else, so every
long call looked frozen to its caller.

The relay is a process-global token map. The serving surface mints a fresh
upstream token per relayed call (caller tokens are opaque and can collide
across sessions on a shared upstream client), registers a forwarder for it,
and the upstream's standing GET stream (#882) hands arriving progress to
:func:`forward`. The forwarder owns delivery -- typically scheduling the
SDK session's ``send_progress_notification`` onto its event loop.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from typing import Any

#: forwarder(progress, total, message) -> None
ProgressForwarder = Callable[[float, float | None, str | None], None]

_forwarders: dict[str, ProgressForwarder] = {}
_lock = threading.Lock()


def mint_token() -> str:
    """A fresh, non-colliding upstream progress token."""
    return f"hangar-progress-{uuid.uuid4().hex[:16]}"


def register(token: str, forwarder: ProgressForwarder) -> None:
    with _lock:
        _forwarders[token] = forwarder


def unregister(token: str | None) -> None:
    """No-op for ``None`` so a call that never registered can clean up blindly."""
    if token is None:
        return
    with _lock:
        _forwarders.pop(token, None)


def forward(params: dict[str, Any]) -> bool:
    """Deliver an upstream progress notification to its registered caller.

    Returns whether the token was known. Runs on the GET stream's reader
    thread; a forwarder that raises is the caller surface's bug and is left
    to the stream's handler fault barrier.
    """
    token = params.get("progressToken") or params.get("progress_token")
    if not isinstance(token, str):
        return False
    with _lock:
        forwarder = _forwarders.get(token)
    if forwarder is None:
        return False
    progress = params.get("progress")
    forwarder(
        float(progress) if isinstance(progress, (int, float)) else 0.0,
        params.get("total"),
        params.get("message"),
    )
    return True
