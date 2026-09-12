**security:** a suspended session is now refused (GHSA-fhwh-fmq2-7m5c).
`POST /api/sessions/{id}/suspend` answered 200 and replicated the suspension to
every replica, but no request path read it, so the session went on calling
tools. The earlier note that a suspended session "is now refused by every
replica" was not true until this release.

A request is now refused when the caller carries a suspended session id. It is
refused before any validation, authorization, cold start or upstream call. This
covers:

- `hangar_call`, every other `hangar_*` tool including the continuation tools,
  and a front door's flat tool calls;
- `tasks/get`, `tasks/cancel` and `tasks/update`;
- on a front door, the prompt, completion, resource and subscription methods
  that reach an upstream.

Every replica that has read the suspension refuses it.

A caller carries a session id from one of two sources. One is the session-id
claim of a verified OIDC token: `sid` by default, or the claim named by
`auth.oidc.session_id_claim`, per issuer if needed. A header cannot replace it.
The other is an `x-session-id` header, and only from a peer listed in
`MCP_TRUSTED_PROXIES`.

Not covered:

- a caller with no session id, which has nothing to match;
- stdio sessions;
- deployments with auth disabled;
- `tools/list`, the REST API and `/ws/events`.

Hangar now starts uvicorn with its forwarded-header handling off, so
`MCP_TRUSTED_PROXIES` alone decides which peer may forward a client address.
The client address is now the rightmost forwarded entry that is not a trusted
proxy, not the leftmost one, which the client can write. `FORWARDED_ALLOW_IPS`
is no longer read. `UPGRADE.md` covers both changes.
