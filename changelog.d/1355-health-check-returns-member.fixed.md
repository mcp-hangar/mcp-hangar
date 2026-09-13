**core:** a passing health check now returns a group member to rotation. A
member driven out by consecutive failures used to stay out through passing
health checks, traffic and time, until someone ran `hangar_group_rebalance` or
restarted the process. `GroupRebalanceSaga`, which turns `HealthCheckPassed`
into `report_success()`, never found the member's group on the path `serve`
runs. The servers are loaded before the saga exists, so no member was
registered with it, and it was handed the context's groups while those were
still an empty dict. It now reads a member's groups from the live groups
mapping.

Health events now reach the group, so a failing health check or a failed
restart counts against a member. A stop does not: idle reaping,
`hangar_stop`, reload and unload all leave a member in rotation and the
group's circuit untouched. One failing health check counts once, even when it
also degrades the server. Separately, a group's circuit breaker, which only
`hangar_group_rebalance` ever closed, now closes once `min_healthy` members are
back in rotation
