"""Turning a live server into the record that can rebuild it.

One definition, because there were about to be two. `RecoveryService` already
built this snapshot -- reaching into a dozen of the aggregate's private
attributes to do it -- and the registration path now needs the same thing. Two
copies of "what a server's configuration is" drift the moment a field is added,
and the drift is invisible: the field is simply absent after a restart.

The reverse direction lives here too now. It stayed in recovery for as long as
recovery was the only thing that rebuilt a server from a record; a replica
learning about a registration from the log is the second, and two copies of
"how to turn a snapshot back into a server" would drift exactly as the forward
direction would.

Restoring also has to answer for the rows written *before* a field existed, and
`enforce_ssrf` is the case where reading the stored value literally is wrong:
see `_guard_the_upgrade_can_derive`.
"""

from ..contracts.persistence import McpServerConfigSnapshot
from ..model import McpServer
from ..security.ssrf import endpoint_is_a_literal_the_strict_policy_refuses
from ..value_objects import McpServerMode


def snapshot_of(mcp_server: McpServer, *, enabled: bool = True) -> McpServerConfigSnapshot:
    """Capture a server's configuration as it should be restored.

    Args:
        mcp_server: The live aggregate.
        enabled: Whether recovery should bring this server back. False is how a
            server stays on record without being restored.

    Returns:
        The snapshot to persist.
    """
    return McpServerConfigSnapshot(
        mcp_server_id=mcp_server.mcp_server_id,
        mode=mcp_server.mode_str,
        command=mcp_server._command,
        image=mcp_server._image,
        endpoint=mcp_server._endpoint,
        env=mcp_server._env,
        idle_ttl_s=mcp_server._idle_ttl.seconds,
        health_check_interval_s=mcp_server._health_check_interval.seconds,
        max_consecutive_failures=mcp_server._health.max_consecutive_failures,
        description=mcp_server.description,
        volumes=mcp_server._volumes,
        build=mcp_server._build,
        resources=mcp_server._resources,
        network=mcp_server._network,
        read_only=mcp_server._read_only,
        user=mcp_server._user,
        # Only servers whose tools were declared in configuration: for anything
        # else the tool list is what the server reported at startup, and
        # restoring a stale copy of that would answer for a running server
        # without asking it.
        tools=([tool.to_dict() for tool in mcp_server.tools] if mcp_server._tools_predefined else None),
        enabled=enabled,
        # The connect-time SSRF policy, all three parts or none of them. The
        # flag decides whether the guard runs at all; provenance and the
        # runtime-scoped addresses decide what it accepts. Carrying the flag
        # alone would restore enforcement for a DISCOVERY server without the
        # scoping that makes its container address legitimate, and the first
        # call after the restart would be refused instead of merely unguarded.
        provenance=mcp_server._provenance,
        runtime_addresses=mcp_server._runtime_addresses,
        enforce_ssrf=mcp_server._enforce_ssrf,
    )


def _guard_the_upgrade_can_derive(config: McpServerConfigSnapshot) -> bool:
    """Whether a row that predates `enforce_ssrf` should come back guarded.

    Reading the stored flag literally makes the round trip a fix for *new*
    registrations only. Every remote server registered while 2.5.0 was running
    has a row saying `enforce_ssrf: false`, because that was the field's default
    and nothing wrote it; such a server comes back unguarded forever, not merely
    until the next restart, because an update re-snapshots an aggregate that was
    itself rebuilt with the flag off. Only a delete plus a fresh registration
    would restore it. So the flag is derived, not trusted.

    **Why deriving it is sound.** Every row in the config store was written by
    the create or update handler through `RepositoryFleetWriter`:
    `RecoveryService.save_mcp_server_config` has no caller outside a unit test,
    the fleet projection only reads, and a server declared in `config.yaml` is
    built directly by `server.config._load_mcp_server_config` and added to the
    in-memory repository without ever reaching a writer. The create handler runs
    `validate_no_ssrf` on exactly `mode == remote and endpoint is not None`, so a
    row of that shape describes an endpoint that passed the SSRF check when it
    was registered -- which is the same population `enforce_ssrf` is set for.

    **Why it is scoped.** A pre-fix row is silent about provenance, so a server
    that discovery registered comes back HUMAN with no runtime addresses -- and
    turning the guard on over that would apply the strict policy to a container
    or pod address, refusing on every call an upstream that works today. That
    outage would be worse than the gap. The row's own endpoint is what separates
    the two: an endpoint that passed the *strict* check cannot be a literal in a
    refused range, so a row that does hold one can only have come from the
    scoped discovery path -- and both container sources build their endpoint out
    of the address the runtime reported (`http://{pod_ip}:{port}`,
    `http://{host}:{port}`), a literal every time. Skipping those leaves them
    exactly as this upgrade found them, unguarded, and refuses nothing new.

    The residue, stated rather than hidden: the filesystem and entrypoint
    sources splat a descriptor's own `metadata`, so one could hand-declare
    `runtime_addresses` for a *named* endpoint that resolves somewhere private.
    Such a row is indistinguishable from a human registration of a name and does
    get the derived guard. Re-registering it writes the real provenance and the
    scoping comes back with it.
    """
    if McpServerMode.normalize(config.mode) is not McpServerMode.REMOTE or not config.endpoint:
        return False
    return not endpoint_is_a_literal_the_strict_policy_refuses(config.endpoint)


def server_from_snapshot(config: McpServerConfigSnapshot) -> McpServer:
    """Rebuild a server from the record that describes it.

    Deliberately the plain constructor and nothing else: no lifecycle state, no
    tools discovered at runtime. What the record holds is the configuration, and
    a server rebuilt from it starts COLD -- which is true, because on this
    replica it has not started. Callers that want the state it *had* replay its
    stream over the top (recovery does; a follower learning of a registration
    has nothing to replay yet).

    Args:
        config: The stored configuration.

    Returns:
        The aggregate, as configured.
    """
    return McpServer(
        mcp_server_id=config.mcp_server_id,
        mode=config.mode,
        command=config.command,
        image=config.image,
        endpoint=config.endpoint,
        env=config.env,
        idle_ttl_s=config.idle_ttl_s,
        health_check_interval_s=config.health_check_interval_s,
        max_consecutive_failures=config.max_consecutive_failures,
        description=config.description,
        volumes=config.volumes,
        build=config.build,
        resources=config.resources,
        network=config.network,
        read_only=config.read_only,
        user=config.user,
        tools=config.tools,
        # The other half of the round trip. `validate_no_ssrf` ran once, at
        # registration, in a process that has since exited; the connect-time
        # re-check is the only thing left guarding this endpoint, and it is off
        # unless the flag comes back with the record.
        #
        # Provenance and the addresses are read as stored, and a row that
        # predates them restores HUMAN / no addresses -- the strict policy,
        # which is the safe direction to fail. The flag is not read as stored:
        # a row written before the field existed says False for a server that
        # was registration-checked, and believing it would leave every server
        # registered under 2.5.0 unguarded for good. See
        # `_guard_the_upgrade_can_derive` for what the record has to show
        # before the guard is derived, and why deriving it cannot refuse a
        # discovered container address.
        provenance=config.provenance,
        runtime_addresses=config.runtime_addresses,
        enforce_ssrf=config.enforce_ssrf or _guard_the_upgrade_can_derive(config),
    )
