"""A front-door replica is ready once its required catalogue has been projected (#1446).

In ``front_door`` the projection is ``tools/list``, and it is built from each
server's ``McpServerStarted``. A replica whose boot warm-up could not start a
server serves a catalogue without it, and until #1446 still answered
``/health/ready`` with 200, so the Service sent it traffic it answered with a
short list (#878, #885, #886).

``tool_access.required_catalogue`` names the servers a replica must have
projected before it is ready::

    tool_access:
      mode: front_door
      required_catalogue:
        servers: [payments, search-pool]
        retry_for_s: 600

## What readiness waits for, and for how long

Until every required server has been projected **once** on this replica,
``/health/ready`` answers 503 and names what is missing. After that, readiness
never looks at the catalogue again: a projection is not removed when a server
stops, so a later outage or an idle stop cannot take the replica out of the
Service. That is what keeps #599 fixed. An idle gateway with every backend cold
is still ready, because being ready here means "was projected", not "is warm".

A group id is required as a group: it is satisfied once **any one** of its
members has been projected, since that member's tools are the group's.

## The retry

The boot warm-up starts every server once. A required server it could not start
is retried by ``CatalogueRetry``, on the warm-up's thread once the warm-up is
done, for at most ``retry_for_s`` seconds (600 by default; 0 turns it off). It
does not fight the server lifecycle, which is how #1429 failed:

- It starts a server the way a call does, not the way ``hangar_start`` does
  (``StartMcpServerCommand(deliberate=False)``). A dead server then waits out
  its own backoff, which is also checked before the command is sent, and a
  capability-blocked one is refused.
- It never starts a server that is ``dead`` for ``given_up`` or
  ``capability_blocked``. Readiness stays 503 and names the reason: the replica
  cannot serve the catalogue it was told to, and only an operator can decide
  that server should run again, by starting it deliberately or by taking it off
  the list.
- It leaves a ``degraded`` server to the recovery saga, which either restarts it
  or gives up on it.
- It only ever starts servers this replica has never projected. An idle stop
  needs a start first, and every start publishes ``McpServerStarted``, which is
  what projects. A server stopped for being idle has been projected, so the
  retry never restarts one. A server is first attempted no sooner than
  ``MIN_SPACING_S`` after the retry first sees it unprojected. That covers the
  one path that publishes a start late: a group starting its members directly,
  whose ``McpServerStarted`` is published by the GC worker in the same sweep as
  an idle stop.
- It stops for a server once that server is projected, stops altogether once
  the list is satisfied, and stops at shutdown.

Every attempt writes one log line, naming the error type and never the error
text, and one sample of ``mcp_hangar_catalogue_retries_total``.

## Egress

Nothing here applies. The block is checked like any other key, so a typo is
still refused, and then ignored: in ``egress`` lazy start on first use is the
design, and readiness does not depend on backends (#599).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
import threading
import time
from typing import Any

from ..application.commands import StartMcpServerCommand
from ..application.read_models.tool_projection import get_tool_projection_registry
from ..domain.exceptions import CannotStartMcpServerError, ConfigurationError
from ..domain.model.mcp_server import DEAD_CAPABILITY_BLOCKED, DEAD_GIVEN_UP
from ..domain.services.tool_access_resolver import is_front_door
from ..logging_config import get_logger
from ..metrics import record_catalogue_retry

logger = get_logger(__name__)

#: How long the retry runs after the warm-up, unless `retry_for_s` says otherwise.
DEFAULT_RETRY_FOR_S = 600.0
#: How often the retry looks again at what is still missing.
RETRY_POLL_S = 1.0
#: The least time between two attempts on one server, and before its first.
MIN_SPACING_S = 2.0
#: How long shutdown waits for an attempt in flight to finish.
STOP_JOIN_S = 5.0

#: Why-dead reasons the retry never starts a server for. Only an operator's
#: deliberate start brings one of these back.
NOT_RETRIED = frozenset({DEAD_GIVEN_UP, DEAD_CAPABILITY_BLOCKED})

_WHERE = "tool_access.required_catalogue"
_KEYS = frozenset({"servers", "retry_for_s"})


@dataclass(frozen=True)
class Requirement:
    """One name on the list: a server, or a group any one of whose members satisfies it."""

    name: str
    servers: tuple[str, ...]


@dataclass(frozen=True)
class RequiredCatalogue:
    """``tool_access.required_catalogue``, checked."""

    requirements: tuple[Requirement, ...]
    retry_for_s: float


def required_catalogue(full_config: Mapping[str, Any]) -> RequiredCatalogue | None:
    """``tool_access.required_catalogue``, checked against ``mcp_servers``. Absent means None.

    Raises:
        ConfigurationError: If the block is malformed, carries a key nothing
            reads, or names something that is not in ``mcp_servers``. A name that
            can never be projected would hold readiness at 503 forever, so it is
            refused here instead.
    """
    tool_access = full_config.get("tool_access")
    block = tool_access.get("required_catalogue") if isinstance(tool_access, dict) else None
    if block is None:
        return None
    if not isinstance(block, dict):
        raise ConfigurationError(f"Invalid {_WHERE}: it must be a mapping with a `servers` list.")
    unknown = sorted(set(block) - _KEYS)
    if unknown:
        raise ConfigurationError(f"Invalid {_WHERE}: unknown key(s) {unknown}; allowed keys: {sorted(_KEYS)}.")

    names = block.get("servers")
    if not isinstance(names, list) or not names or not all(isinstance(name, str) and name for name in names):
        raise ConfigurationError(
            f"Invalid {_WHERE}.servers {names!r}. It must be a non-empty list of server or group ids; "
            f"omit {_WHERE} entirely to keep readiness independent of the catalogue."
        )
    servers = full_config.get("mcp_servers")
    specs: Mapping[str, Any] = servers if isinstance(servers, dict) else {}
    requirements = tuple(_requirement(name, specs) for name in dict.fromkeys(names))
    return RequiredCatalogue(requirements=requirements, retry_for_s=_retry_for_s(block))


def _requirement(name: str, specs: Mapping[str, Any]) -> Requirement:
    if name not in specs:
        raise ConfigurationError(
            f"{_WHERE}.servers names {name!r}, which is not in mcp_servers. A replica could never "
            f"project it, so readiness would stay 503. Known ids: {sorted(specs)}."
        )
    spec = specs[name]
    if not (isinstance(spec, dict) and spec.get("mode") == "group"):
        return Requirement(name=name, servers=(name,))
    raw_members = spec.get("members")
    members = tuple(
        str(member["id"])
        for member in (raw_members if isinstance(raw_members, list) else [])
        if isinstance(member, dict) and member.get("id")
    )
    if not members:
        raise ConfigurationError(
            f"{_WHERE}.servers names group {name!r}, which has no members, so nothing could ever satisfy it."
        )
    return Requirement(name=name, servers=members)


def _retry_for_s(block: Mapping[str, Any]) -> float:
    raw = block.get("retry_for_s", DEFAULT_RETRY_FOR_S)
    # `True` is an int to isinstance; a flag here is a mistake, not a duration.
    if isinstance(raw, bool) or not isinstance(raw, int | float) or raw < 0:
        raise ConfigurationError(
            f"Invalid {_WHERE}.retry_for_s {raw!r}. It must be a number of seconds, 0 or more "
            "(0 turns the retry off); omit it for the default."
        )
    return float(raw)


class _Gate:
    """The process's required catalogue, and whether it has been projected yet.

    Complete is a latch: once every requirement has been met, nothing makes this
    replica not ready again, a reload included. A process that booted with no
    list is complete from the start, so a reload that adds one does not take a
    serving replica out of the Service. A reload before completion replaces the
    list, and one that removes it releases the wait.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._required: RequiredCatalogue | None = None
        self._complete = False
        self._retry = "not_started"

    def configure(self, required: RequiredCatalogue | None) -> None:
        with self._lock:
            if self._complete:
                return
            self._required = required
            self._complete = required is None

    def required(self) -> RequiredCatalogue | None:
        with self._lock:
            return self._required

    def set_retry(self, state: str) -> None:
        with self._lock:
            self._retry = state

    def unsatisfied(self) -> list[Requirement]:
        """The requirements no projection has met yet; empty once complete."""
        with self._lock:
            required, complete = self._required, self._complete
        if required is None or complete:
            return []
        registry = get_tool_projection_registry()
        missing = [r for r in required.requirements if not any(registry.was_projected(s) for s in r.servers)]
        if not missing:
            with self._lock:
                newly = not self._complete
                self._complete = True
            if newly:
                logger.info("required_catalogue_projected", required=[r.name for r in required.requirements])
        return missing

    def readiness(self, repository: Any) -> dict[str, Any] | None:
        """The ``catalogue`` field of ``/health/ready``, or None when no list is in force."""
        if self.required() is None:
            return None
        missing = self.unsatisfied()
        if not missing:
            return {"status": "complete"}
        with self._lock:
            retry = self._retry
        return {
            "status": "waiting",
            "missing": [requirement.name for requirement in missing],
            "not_retried": _not_retried(repository, missing),
            "retry": retry,
        }


def _not_retried(repository: Any, missing: list[Requirement]) -> dict[str, str]:
    """Each unprojected server the retry will not start, and why."""
    reasons: dict[str, str] = {}
    for server_id in _candidates(missing):
        server = repository.get(server_id)
        reason = getattr(server, "dead_reason_snapshot", None) if server is not None else None
        if reason in NOT_RETRIED:
            reasons[server_id] = str(reason)
    return reasons


def _candidates(missing: list[Requirement]) -> list[str]:
    """The servers that could still meet a missing requirement, each once."""
    return list(dict.fromkeys(server_id for requirement in missing for server_id in requirement.servers))


_gate = _Gate()


def configure_required_catalogue(required: RequiredCatalogue | None) -> None:
    """Put a checked list in force; None when absent, or in ``egress``."""
    _gate.configure(required)


def catalogue_readiness(repository: Any) -> dict[str, Any] | None:
    """What ``/health/ready`` says about the catalogue, or None when it has nothing to say."""
    return _gate.readiness(repository)


def reset() -> None:
    """Forget the list and the latch. For tests, and for a re-bootstrapped process."""
    global _gate
    _gate = _Gate()


class CatalogueRetry:
    """Retry, within bounds, the required servers the boot warm-up could not project.

    ``run`` is called on the warm-up's thread after the warm-up, and returns at
    once unless this is a front door with a list in force. See the module
    docstring for what it will and will not start.
    """

    def __init__(self, runtime: Any, *, poll_s: float = RETRY_POLL_S, spacing_s: float = MIN_SPACING_S) -> None:
        self._runtime = runtime
        self._poll_s = poll_s
        self._spacing_s = spacing_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_at: dict[str, float] = {}
        self._attempts: Counter[str] = Counter()

    def stop(self, timeout: float = STOP_JOIN_S) -> None:
        """Stop, and wait up to ``timeout`` for an attempt in flight. Safe to call more than once."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def run(self) -> None:
        """Retry until the list is projected, ``retry_for_s`` has passed, or ``stop`` is called."""
        required = _gate.required()
        if required is None or self._stop.is_set() or not is_front_door():
            return
        if required.retry_for_s <= 0:
            _gate.set_retry("off")
            return
        self._thread = threading.current_thread()
        _gate.set_retry("running")
        try:
            _gate.set_retry(self._loop(time.monotonic() + required.retry_for_s))
        finally:
            self._thread = None

    def _loop(self, deadline: float) -> str:
        """The retry itself; returns how it ended."""
        while not self._stop.is_set():
            missing = _gate.unsatisfied()
            if not missing:
                return "finished"
            if time.monotonic() >= deadline:
                logger.warning(
                    "required_catalogue_retry_exhausted",
                    missing=[requirement.name for requirement in missing],
                    attempts=dict(self._attempts),
                )
                return "exhausted"
            for server_id in _candidates(missing):
                if self._stop.is_set():
                    break
                if self._due(server_id):
                    self._attempt(server_id)
            self._stop.wait(self._poll_s)
        return "stopped"

    def _due(self, server_id: str) -> bool:
        """Whether to start ``server_id`` now. Never for a projected server; see the module docstring."""
        now = time.monotonic()
        if now < self._next_at.setdefault(server_id, now + self._spacing_s):
            return False
        if get_tool_projection_registry().was_projected(server_id):
            return False
        server = self._runtime.repository.get(server_id)
        return server is not None and _retryable(server)

    def _attempt(self, server_id: str) -> None:
        """One start, as a call would make it; one log line and one metric sample."""
        self._attempts[server_id] += 1
        attempt = self._attempts[server_id]
        error_type: str | None = None
        try:
            self._runtime.command_bus.send(StartMcpServerCommand(mcp_server_id=server_id, deliberate=False))
        except CannotStartMcpServerError:
            outcome, error_type = "refused", CannotStartMcpServerError.__name__
        except Exception as e:  # noqa: BLE001 -- fault-barrier: one server's failed start must not end the retry for the others
            outcome, error_type = "failed", type(e).__name__
        else:
            outcome = "projected" if get_tool_projection_registry().was_projected(server_id) else "started"
        # After the attempt, so the spacing runs from when it ended.
        self._next_at[server_id] = time.monotonic() + self._spacing_s
        record_catalogue_retry(server_id, outcome)
        log = logger.info if outcome in ("projected", "started") else logger.warning
        log(
            "required_catalogue_retry", mcp_server_id=server_id, attempt=attempt, outcome=outcome, error_type=error_type
        )


def _retryable(server: Any) -> bool:
    """Whether a start by the retry is the server's to take, by its state.

    ``cold`` and ``dead`` for a start failure or a crash are: nothing else will
    start them before a call does. ``dead`` waits out the server's own backoff.
    ``ready`` and ``initializing`` have a start under way, or its projection;
    ``degraded`` belongs to the recovery saga; and ``NOT_RETRIED`` to an operator.
    """
    state = server.state.value
    if state == "cold":
        return True
    if state != "dead":
        return False
    if getattr(server, "dead_reason_snapshot", None) in NOT_RETRIED:
        return False
    return bool(server.health.can_retry())
