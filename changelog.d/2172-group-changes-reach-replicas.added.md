**core:** group changes reach running replicas. A group or member created,
updated, deleted or removed over REST on one replica is applied by its peers
from the stored rows within the event tail's interval, with no restart, by the
rules a restart uses: the file wins for what it declares, and a member whose
server arrives later joins when it does. A tool access policy set through
`/api/auth/policies` reaches peers the same way, so a replica applies a group's
policy to the members it learns of. `ToolAccessPolicySet` and
`ToolAccessPolicyCleared` now go on the shared log, in a `tool_access_policy`
stream. `PUT /api/groups/{id}` with a new strategy now also changes how the group
selects members, on every replica.
