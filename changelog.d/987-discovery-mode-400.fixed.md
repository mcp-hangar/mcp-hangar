**core:** a misspelt discovery `mode` over REST answered 500 instead of 400.
`POST /api/discovery/sources` and `PUT /api/discovery/sources/{source_id}`
checked only that `mode` was present; a value like `"Authoritative"` reached
the command handler's `DiscoveryMode(...)` conversion, whose bare `ValueError`
was reported as "an internal server error occurred" and logged as an unhandled
exception. Both endpoints now answer 400 naming the rejected value and the two
valid spellings (`additive`, `authoritative`). If you scripted around the 500,
read the 400's `detail` instead.
