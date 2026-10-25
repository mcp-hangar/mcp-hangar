### A changed discovered definition now replaces the running server

When a discovery source reports a new definition for a server it registered
(an edited definition file, changed Docker labels, changed pod annotations),
Hangar now replaces the server on the next cycle: it waits up to 30 seconds
for the calls in flight, stops the server, and registers the new definition.
Before, the change was refused with `McpServer already exists` on every cycle
until the server's TTL ran out; then the server was removed and created again
from the new definition.

What this changes for a setup that relied on the old behaviour:

- An edit takes effect within one refresh interval instead of after a
  TTL-long outage. If you edit definition files in place, the server restarts on
  every edit that changes its definition.
- A changed definition that the control plane refuses (an endpoint the SSRF
  policy refuses, a local mode on a gateway that shares its state) is no longer
  applied after the TTL. The running definition keeps running, and one
  `discovery_change_not_applied` WARNING names the reason. Fix or revert the
  definition.
- With `auto_register: false`, a change waits in `hangar_discovered` until it
  is approved, and the definition approved before keeps running. Approving it
  replaces the server; before, approval reported success and changed nothing.
- A changed definition that fails validation is quarantined while the running
  definition keeps running, and stays registered past its TTL.
- A server that is not registered by discovery is never replaced, even when a
  source reports the same name: a server created over the API, re-pointed over
  the API, or declared in the configuration file.

Unchanged: a definition a source refuses at parse time (a Kubernetes local
mode without `allow_local_modes`, a Docker volume outside
`allowed_mount_paths`, a definition file the trust checks refuse) is not
reported at all. On an additive source the running server stays; on an
authoritative source it is removed after its TTL, as before.
