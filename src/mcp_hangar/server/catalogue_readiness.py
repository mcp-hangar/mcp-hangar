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

``retry_for_s`` is a window, opened when this process first applies its
configuration and never moved by a reload. Inside it, ``/health/ready`` answers
503 until every required server has been projected **once** on this replica.
When all of them have been, or when the window ends, readiness stops looking at
the catalogue for good and goes back to today's rule. So a replica is held out
of the Service for at most ``retry_for_s``: a required server that never comes
back, or that the lifecycle gave up on, cannot hold it forever.

The window counts from that first apply, before the rest of boot and the boot
warm-up, so ``retry_for_s`` has to cover those too. If they outlast it, the
retry still gives each missing server one attempt, within its backoff, before
it ends; readiness does not wait for that attempt.

A projection is not removed when a server stops, so a later outage or an idle
stop never takes a ready replica out of the Service. That is what keeps #599
fixed: an idle gateway with every backend cold is still ready, because being
ready here means "was projected", not "is warm".

A group id is required as a group: it is satisfied once **any one** of its
members has been projected, since that member's tools are the group's. A group
member defined only inline, in the group's ``members``, can be named too.

``/health/ready`` answers without authentication, so its ``catalogue`` field
carries counts and state, never a server id or a dead reason. The ids and
reasons go where operators already look: a ``required_catalogue_waiting`` log
line each time they change, the retry's own log lines, and ``hangar_health``.
Both keep reporting what is missing after the window has ended.

## The retry

The boot warm-up starts every server once. A required server it could not start
is retried by ``CatalogueRetry``, on the warm-up's thread once the warm-up is
done, until the list is met or the window ends. It does not fight the server
lifecycle, which is how #1429 failed:

- It starts a server the way a call does, not the way ``hangar_start`` does
  (``StartMcpServerCommand(deliberate=False)``). A dead server then waits out
  its own backoff, which is also checked before the command is sent, and a
  capability-blocked one is refused.
- It never starts a server that is ``dead`` for ``given_up`` or
  ``capability_blocked``: only an operator can decide that server should run
  again, by starting it deliberately or by taking it off the list. When nothing
  it may start is left, it ends early, as ``blocked``; readiness still waits for
  the rest of the window, for such a deliberate start.
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
- It stops for a server once that server is projected, stops starting a
  group's other members once one of them is, and stops altogether once the
  list is met, when the window ends, when a reload removes the list, and at
  shutdown. Each way it ends is a final state; none of them reads ``running``.

Every attempt writes one log line, naming the error type and never the error
text, and one sample of ``mcp_hangar_catalogue_retries_total``.

## What the configuration refuses

An id that is not a server or group in ``mcp_servers``, a group with no members,
an unknown key, and a ``retry_for_s`` that is not a number above 0. And, where
several replicas take the management lease -- a ``coordination:`` block, or a
shared storage backend -- a required server that runs in a local mode: only the
lease holder may start one (``LocalModeNotOwnedError``), so a replica that does
not hold the lease could never project it. The backend is read as the loader
reads it, case-blind and with an empty value meaning none. A backend a plugin
registers is treated as shared, as a precaution, and a single replica on a
shared backend is refused as well: a configuration cannot know how many
replicas will run it.

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
from ..application.read_models.tool_projection import get_tool_projection_registry, ToolProjectionRegistry
from ..domain.exceptions import CannotStartMcpServerError, ConfigurationError
from ..domain.model.mcp_server import DEAD_CAPABILITY_BLOCKED, DEAD_GIVEN_UP
from ..domain.services.tool_access_resolver import is_front_door
from ..infrastructure.launchers import LOCAL_MODES
from ..logging_config import get_logger
from ..metrics import record_catalogue_retry

logger = get_logger(__name__)

#: How long readiness may wait for the list, and the retry may run, unless `retry_for_s` says otherwise.
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

#: The one built-in storage backend that is local to one process (`registry.is_shared`).
_LOCAL_BACKENDS = frozenset({"sqlite"})

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
        ConfigurationError: If the block is malformed or carries a key nothing
            reads; if it names something that is not a server or group in
            ``mcp_servers``, or a group with no members, which could never be
            projected; or if it requires a local-mode server where only the
            lease holder may start one.
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
    known = _known_servers(specs)
    requirements = tuple(_requirement(name, specs, known) for name in dict.fromkeys(names))
    _refuse_what_only_the_lease_holder_starts(full_config, requirements, known)
    return RequiredCatalogue(requirements=requirements, retry_for_s=_retry_for_s(block))


def _is_group(spec: Any) -> bool:
    return isinstance(spec, dict) and spec.get("mode") == "group"


def _members(spec: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = spec.get("members")
    return [
        member for member in (raw if isinstance(raw, list) else []) if isinstance(member, dict) and member.get("id")
    ]


def _known_servers(specs: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Every server id the configuration builds, with its spec.

    The top-level servers, and a group member defined only inline in its group,
    which the loader builds from the member's own spec. A top-level spec wins,
    as it does in the loader.
    """
    known: dict[str, Mapping[str, Any]] = {
        str(server_id): (spec if isinstance(spec, dict) else {})
        for server_id, spec in specs.items()
        if not _is_group(spec)
    }
    for spec in specs.values():
        if _is_group(spec):
            for member in _members(spec):
                known.setdefault(str(member["id"]), member)
    return known


def _requirement(name: str, specs: Mapping[str, Any], known: Mapping[str, Any]) -> Requirement:
    spec = specs.get(name)
    if isinstance(spec, dict) and _is_group(spec):
        members = tuple(str(member["id"]) for member in _members(spec))
        if not members:
            raise ConfigurationError(
                f"{_WHERE}.servers names group {name!r}, which has no members, so nothing could ever satisfy it."
            )
        return Requirement(name=name, servers=members)
    if name not in known:
        raise ConfigurationError(
            f"{_WHERE}.servers names {name!r}, which is not a server or group in mcp_servers. A replica "
            f"could never project it. Known ids: {sorted(set(known) | set(specs))}."
        )
    return Requirement(name=name, servers=(name,))


def _lease_is_taken(full_config: Mapping[str, Any]) -> str | None:
    """Why only one replica may start a local-mode server here, or None when every replica may.

    A ``coordination:`` block declares a cluster. A storage backend other than
    ``sqlite`` is shared, and a shared backend is what gives this gateway a
    management lease (`bootstrap.coordination.init_lease_keeper`). A backend a
    plugin registers is treated as shared too: that refuses a list a follower
    might not be able to meet, rather than accepting one it cannot.
    """
    if "coordination" in full_config:
        return "declares a cluster (`coordination:`)"
    persistence = full_config.get("persistence")
    backend = persistence.get("backend") if isinstance(persistence, dict) else None
    # Read as the loader reads it (`bootstrap.persistence.select_backend`): an
    # empty value is no backend, and the name is not case-sensitive.
    name = str(backend or "").strip().lower()
    if name and name not in _LOCAL_BACKENDS:
        return f"persists through the shared `{name}` backend"
    return None


def _is_local(spec: Mapping[str, Any]) -> bool:
    return str(spec.get("mode", "subprocess")).strip().lower() in LOCAL_MODES


def _refuse_what_only_the_lease_holder_starts(
    full_config: Mapping[str, Any], requirements: tuple[Requirement, ...], known: Mapping[str, Mapping[str, Any]]
) -> None:
    """Refuse a requirement no replica but the lease holder could ever meet (#1446).

    A group is refused only when every member is local: a remote member is one
    any replica can start.
    """
    why = _lease_is_taken(full_config)
    if why is None:
        return
    offenders = [
        requirement.name
        for requirement in requirements
        if all(_is_local(known.get(server_id, {})) for server_id in requirement.servers)
    ]
    if offenders:
        raise ConfigurationError(
            f"{_WHERE}.servers requires {offenders}, which run in a local mode ({sorted(LOCAL_MODES)}), and this "
            f"gateway {why}. Only the instance holding the management lease may start a local-mode server, so a "
            "replica that does not hold it could never project one. Use `remote` mode for a server every replica "
            "must serve, or take it off the list."
        )


def _retry_for_s(block: Mapping[str, Any]) -> float:
    raw = block.get("retry_for_s", DEFAULT_RETRY_FOR_S)
    # `True` is an int to isinstance; a flag here is a mistake, not a duration.
    if isinstance(raw, bool) or not isinstance(raw, int | float) or raw <= 0:
        raise ConfigurationError(
            f"Invalid {_WHERE}.retry_for_s {raw!r}. It must be a number of seconds above 0: how long readiness "
            f"may wait for the list, and the retry may run. Omit it for the default; to not wait at all, omit {_WHERE}."
        )
    return float(raw)


def _met(requirement: Requirement, registry: ToolProjectionRegistry) -> bool:
    return any(registry.was_projected(server_id) for server_id in requirement.servers)


@dataclass(frozen=True)
class _Snapshot:
    """The list in force, what it is missing, and what that means for readiness."""

    required: RequiredCatalogue
    missing: list[Requirement]
    not_retried: dict[str, str]
    holds_readiness: bool
    retry: str


class _Gate:
    """The process's required catalogue, its window, and whether it has been projected.

    The window opens when this process first applies its configuration, with
    that configuration's ``retry_for_s`` (none at all when it has no list), and
    nothing moves it. Readiness waits only inside it, and only until the list
    has been met once: meeting it is a latch. A reload replaces the list,
    removes it, or adds one, and each is reported; none of them holds readiness
    outside the window, or after the latch.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._required: RequiredCatalogue | None = None
        self._window_ends_at: float | None = None
        self._complete = False
        self._retry = "not_started"
        # The retry is giving a server the first attempt the window closed on.
        self._grace = False
        # Whether the last readiness check raised, so a failing probe logs once.
        self._readiness_failed = False
        # What `observe` last logged, so a probe every few seconds logs a change once.
        self._logged: tuple[Any, ...] | None = None

    def configure(self, required: RequiredCatalogue | None) -> None:
        with self._lock:
            if self._window_ends_at is None:
                self._window_ends_at = time.monotonic() + (required.retry_for_s if required is not None else 0.0)
            self._required = required

    def required(self) -> RequiredCatalogue | None:
        with self._lock:
            return self._required

    def set_retry(self, state: str, *, grace: bool = False) -> None:
        with self._lock:
            self._retry = state
            self._grace = grace

    def window_closed(self) -> bool:
        with self._lock:
            return self._window_ends_at is None or time.monotonic() >= self._window_ends_at

    def observe(self, repository: Any) -> _Snapshot | None:
        """The list in force and what it is missing; None when there is none.

        Logged, with the ids and dead reasons and never error text, each time
        any of it changes. That line is where an operator reads the ids: the
        readiness endpoint is unauthenticated, so it reports counts.
        """
        required = self.required()
        if required is None:
            return None
        registry = get_tool_projection_registry()
        missing = [requirement for requirement in required.requirements if not _met(requirement, registry)]
        not_retried = _not_retried(repository, missing) if missing else {}
        now = time.monotonic()
        with self._lock:
            newly_complete = not missing and not self._complete
            self._complete = self._complete or not missing
            window_open = self._window_ends_at is not None and now < self._window_ends_at
            holds = bool(missing) and not self._complete and window_open
            retry = _reported(self._retry, missing, not_retried, window_open or self._grace)
            snapshot = _Snapshot(required, missing, not_retried, holds, retry)
            seen = (tuple(r.name for r in missing), tuple(sorted(not_retried.items())), holds, retry)
            changed = bool(missing) and seen != self._logged
            self._logged = seen
        if newly_complete:
            logger.info("required_catalogue_projected", required=[r.name for r in required.requirements])
        if changed:
            logger.warning(
                "required_catalogue_waiting",
                missing=list(seen[0]),
                not_retried=not_retried,
                holds_readiness=holds,
                retry=snapshot.retry,
            )
        return snapshot

    def readiness(self, repository: Any) -> dict[str, Any] | None:
        """The ``catalogue`` field of ``/health/ready``: counts and state, no ids. None when no list is in force.

        Behind a fault barrier: a check that raises answers None, which is
        today's rule, and is logged once, by type, until a check succeeds again.
        """
        try:
            snapshot = self.observe(repository)
        except Exception as e:  # noqa: BLE001 -- fault-barrier: a probe that cannot read the catalogue falls back to today's rule
            with self._lock:
                first = not self._readiness_failed
                self._readiness_failed = True
            if first:
                logger.error("required_catalogue_readiness_failed", error_type=type(e).__name__)
            return None
        with self._lock:
            self._readiness_failed = False
        if snapshot is None:
            return None
        total = len(snapshot.required.requirements)
        return {
            "complete": not snapshot.missing,
            "holds_readiness": snapshot.holds_readiness,
            "required": total,
            "projected": total - len(snapshot.missing),
            "missing_count": len(snapshot.missing),
            "not_retried_count": len(snapshot.not_retried),
            "retry": snapshot.retry,
        }

    def detail(self, repository: Any) -> dict[str, Any] | None:
        """The same, with the ids and dead reasons, for a surface that authenticates its caller."""
        snapshot = self.observe(repository)
        if snapshot is None:
            return None
        return {
            "complete": not snapshot.missing,
            "holds_readiness": snapshot.holds_readiness,
            "required": [requirement.name for requirement in snapshot.required.requirements],
            "missing": [requirement.name for requirement in snapshot.missing],
            "not_retried": snapshot.not_retried,
            "retry": snapshot.retry,
        }


def _reported(retry: str, missing: list[Requirement], not_retried: dict[str, str], window_open: bool) -> str:
    """The retry's state as reported: final from the moment it is decided, not from the retry's next pass.

    The retry notices the window's end, or that nothing it may start is left,
    on its next pass, up to ``RETRY_POLL_S`` later. It starts nothing in
    between -- each pass checks both before any attempt -- so the report does
    not wait for it, and never reads ``running`` when there is nothing to run.
    """
    if retry != "running" or not missing:
        return retry
    if not window_open:
        return "exhausted"
    if set(_candidates(missing)) <= set(not_retried):
        return "blocked"
    return retry


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
    """What ``/health/ready`` says about the catalogue: counts, no ids. None when it has nothing to say."""
    return _gate.readiness(repository)


def catalogue_detail(repository: Any) -> dict[str, Any] | None:
    """The catalogue with its ids and dead reasons, for ``hangar_health``. None when no list is in force."""
    return _gate.detail(repository)


def reset() -> None:
    """Forget the list, the window and the latch. For tests, and for a re-bootstrapped process."""
    global _gate
    _gate = _Gate()


class CatalogueRetry:
    """Retry, within the window, the required servers the boot warm-up could not project.

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
        """Retry until the list is met, nothing is left it may start, the window ends, or ``stop``."""
        if _gate.required() is None or self._stop.is_set() or not is_front_door():
            return
        self._thread = threading.current_thread()
        _gate.set_retry("running")
        final = "failed"
        try:
            final = self._loop()
        except Exception as e:  # noqa: BLE001 -- fault-barrier: the retry ends in a final state, never `running` with no thread
            logger.error("required_catalogue_retry_failed", error_type=type(e).__name__)
        finally:
            _gate.set_retry(final)
            self._thread = None

    def _loop(self) -> str:
        """The retry itself; returns how it ended."""
        while not self._stop.is_set():
            try:
                ended = self._pass()
            except Exception as e:  # noqa: BLE001 -- fault-barrier: one failed pass must not end the retry for good
                logger.warning("required_catalogue_retry_error", error_type=type(e).__name__)
                ended = None
            if ended is not None:
                return ended
            self._stop.wait(self._poll_s)
        return "stopped"

    def _pass(self) -> str | None:
        """One look at the list and one round of due attempts: how the retry ended, or None to go on."""
        snapshot = _gate.observe(self._runtime.repository)
        if snapshot is None:
            return "stopped"  # a reload removed the list
        if not snapshot.missing:
            return "finished"
        missing = [requirement.name for requirement in snapshot.missing]
        only: set[str] | None = None
        if _gate.window_closed():
            # Boot and the warm-up can outlast the window: each missing server
            # still gets its first attempt, within its backoff. Readiness does
            # not wait for it.
            only = self._owed(snapshot.missing)
            if not only:
                logger.warning("required_catalogue_retry_exhausted", missing=missing, attempts=dict(self._attempts))
                return "exhausted"
            _gate.set_retry("running", grace=True)
        elif set(_candidates(snapshot.missing)) <= set(snapshot.not_retried):
            logger.warning("required_catalogue_retry_blocked", missing=missing, not_retried=snapshot.not_retried)
            return "blocked"
        self._attempt_due(snapshot.missing, only)
        return None

    def _owed(self, missing: list[Requirement]) -> set[str]:
        """The servers the window closed on before their first attempt, and that the retry may start."""
        repository = self._runtime.repository
        return {s for s in _candidates(missing) if not self._attempts[s] and _eligible(repository.get(s))}

    def _attempt_due(self, missing: list[Requirement], only: set[str] | None) -> None:
        """Start each due server once, of ``only`` if given; not a group's other members once one is projected."""
        registry = get_tool_projection_registry()
        tried: set[str] = set()
        for requirement in missing:
            for server_id in requirement.servers:
                if self._stop.is_set():
                    return
                # Met by an attempt earlier in this pass.
                if _met(requirement, registry):
                    break
                if server_id not in tried and (only is None or server_id in only) and self._due(server_id):
                    self._attempt(server_id)
                tried.add(server_id)

    def _due(self, server_id: str) -> bool:
        """Whether to start ``server_id`` now: its spacing, then its state and backoff (`_retryable`)."""
        now = time.monotonic()
        if now < self._next_at.setdefault(server_id, now + self._spacing_s):
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
    return _eligible(server) and (server.state.value != "dead" or bool(server.health.can_retry()))


def _eligible(server: Any) -> bool:
    """Whether the retry may start the server at all, its backoff aside: see `_retryable`."""
    if server is None:
        return False
    state = server.state.value
    return state == "cold" or (state == "dead" and getattr(server, "dead_reason_snapshot", None) not in NOT_RETRIED)
