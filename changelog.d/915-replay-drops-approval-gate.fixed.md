A restart no longer removes a human-consent gate the configuration still
declares. The tool-access-policy store held only `allow_list` and `deny_list`,
and the startup replay rebuilt a policy from exactly those two and assigned it
over whatever the resolver held. YAML registers policies earlier in the same
boot, so a target with `tools.approval_list` and any prior REST policy update
came back **ungated** — and the startup reachability check, running after the
replay, saw nothing left to demand the gate and started clean.

The store now persists `approval_list`, `approval_timeout_seconds` and
`approval_channel`, and the replay hands back whole policies rather than two
lists a caller has to remember to widen. An existing database is migrated in
place on first open. A row written by an older build carries NULL approval
columns; rather than let that erase a gate the resolver already holds, the
replay carries the in-force gate forward and logs
`tap_replay_carried_approval_gate`.

The REST update path now persists the same policy it enforces. It already
preserved the gate in memory but handed the store the command's two lists, so
the store held less than the resolver — which is what the next restart replayed.

No action needed on upgrade. If a gate was lost to this on an earlier restart,
it comes back on the next one, because the YAML declaration was never the thing
that went missing.
