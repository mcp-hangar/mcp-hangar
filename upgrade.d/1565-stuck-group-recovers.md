### a group with no member left in rotation starts its members again

A group whose members had all failed out of rotation and then gone `cold` or
DEAD (given up on, start failed or crashed) used to stay that way until an
operator ran `hangar_start` on it. Each replica now starts those members itself,
every 30 s at first and backing off to once every 10 minutes while the upstream
stays down. Expect `group_recovery_probe_*` log lines and start attempts against
an upstream that is still down. The same applies to members whose start failed
when an `auto_start` group loaded. A member stopped on purpose (`hangar_stop` on
it or on its group, or a detection block), one never started in an
`auto_start: false` group, and one blocked for a capability violation are not
started.

The first failed start of a member reaped for idling can degrade it, and the
recovery saga then retries it up to `max_retries` times before it gives up, as
it does for any degraded server. After that each failed probe start gives up at
once, and only the probe's backoff spaces the attempts.

`hangar_group_rebalance` changed with it. It used to take every member that was
not `ready` out of rotation. It now keeps, or puts back, a `cold` member, and
one DEAD because its process crashed or its start failed, when that member is in
rotation or left it on a failure: the call that selects such a member starts it.
Members Hangar gave up on, degraded ones and ones blocked for a capability
violation still leave rotation; the recovery worker, or `hangar_start`, brings
those back.
