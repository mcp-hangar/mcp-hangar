**core:** a group whose members had all left rotation and then gone `cold`
(reaped by the GC while the group could not call them) or DEAD (the recovery
saga gave up) refused every call on that replica until someone started it by
hand, even once the upstream was healthy again. A new per-replica worker,
`group_recovery`, runs every 30 s and starts those members of a group with
nothing to select, backing off per member from 30 s, doubling to a 600 s cap,
while the upstream stays down. A start goes through the usual
`McpServerStarted` path, so rotation and the group circuit follow the same rules
as any other success. It logs `group_recovery_probe_started`,
`group_recovery_probe_succeeded` and `group_recovery_probe_failed`. A member
that was never started, was stopped on purpose or was blocked for a capability
violation is left alone. `hangar_group_rebalance` no longer takes `cold`
members out of rotation, so on a stuck group it leaves a member a call can
select and start
