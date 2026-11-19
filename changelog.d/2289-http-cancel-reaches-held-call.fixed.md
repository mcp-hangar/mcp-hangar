**core:** a streamable HTTP client that gave up on a call held for approval
left the approval open. On the handshake era the client's
`notifications/cancelled` is a POST of its own, and the gateway, which serves
HTTP without a session, had no request for it to reach: the record stayed
`pending`, and a later `mcp-hangar approvals approve` ran a call the client had
been told failed. The gateway now finds the held calls of the named request
through their approval records, on whichever replica the cancel lands, and
cancels them as a stdio cancel does (mcp-hangar#2279). It acts only for an
authenticated principal, and only when the pending calls of that principal and
tenant with that request id are all of one request. On 2026-07-28 the client
closes the request's stream, which already cancelled the hold
