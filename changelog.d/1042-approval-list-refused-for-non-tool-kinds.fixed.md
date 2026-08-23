**core:** `approval_list` under `access.prompt` / `access.resource` is refused at
load instead of accepted and ignored. It was documented as inherited from the
tools policy and announced that way in 2.13.0, but `requires_approval()` has one
consumer -- the tool call path -- so an approval-listed prompt or resource was
listed and served immediately: no hold, no human, no metric. A configuration that
asks for enforcement no path performs is now refused where it is written, the way
per-tenant pins without an identity are. Whether the hold belongs on
`resources/read` / `prompts/get` at all is a separate decision, so the refusal
says "not supported", not "invalid"
