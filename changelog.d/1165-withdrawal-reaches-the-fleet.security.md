**core:** a runtime withdrawal made through `POST
/admin/tools/{server}/{name}/withdraw` lived in the RAM of the replica that
served the request. On a fleet of N the other N-1 kept listing and serving the
withdrawn tool, prompt or resource -- reachable by retrying until the load
balancer picked another pod -- and a rolling restart lifted the withdrawal
altogether, while the response said `{"withdrawn": true}` and the REST
reference said it persists. `ToolWithdrawn` and `ToolRestored` are now recorded
in a per-server withdrawal stream, applied on every replica by a new
`WithdrawalProjection` off the event tail, and folded back into the overlay at
startup, so a replica that joins later comes up with the fleet's withdrawals
rather than without them
