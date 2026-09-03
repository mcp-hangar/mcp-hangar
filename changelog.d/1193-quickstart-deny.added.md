**core:** `examples/rugpull/` is the upstream the quickstart uses to show a rug pull -- one
tool whose description comes from `RUG_DESC`, so the same server is honest on one run and
poisoned on the next while nothing else about it changes. A poisoned description is the
version of tool poisoning that alters no parameter, and Hangar's digest covers
`description` as well as the schemas, so `pin --check` catches it and the gate refuses the
call.

The release smoke gate (`scripts/smoke_published_artifact.py`) now walks that same
sequence against the published artifact: pin, call and get an answer, restart with the
description changed, call again and get `Tool 'echo' schema does not match its pinned
digest`, then `pin --check` exiting 1 with the drift named. A deny is the claim that rots
silently -- a broken example still starts, lists and answers, it just stops refusing --
so the gate asserts the refusal rather than the happy path.
