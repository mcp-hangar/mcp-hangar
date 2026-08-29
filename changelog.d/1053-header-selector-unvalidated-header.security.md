**core:** an `MCPEgressPolicy` header selector could match an `Mcp-Param-*`
header nothing had validated against the request body. The SDK's pre-dispatch
check is fail-open by design -- a `tools/list` that raises means no schema, no
check, and the call is dispatched anyway -- and `evaluate_headers` admitted a
request on its protocol version alone, which says only whether the check was
*owed*. Since 2.14.0 Hangar routes on those headers itself, so SEP-2243's own
failure mode (route on one value, execute another) sat inside the policy engine.
A request whose validation was skipped now satisfies no `allow`, `deny` or
`requireApproval` selector: it falls through to the tool rules and the policy
default, and the audit reason says the rules were not consulted rather than that
none matched. Nothing changes for a policy that writes no header selectors
(ADR-025)
