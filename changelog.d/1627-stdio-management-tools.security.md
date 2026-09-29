**security:** over stdio with auth on, the `hangar_*` management tools are now
authorized for the principal `auth.stdio.principal` declares, on its declared
roles (ADR-026). `authorize_tool` read the caller only from the HTTP request,
which a pipe never carries, so every management tool a declared principal was
listed was refused as `Authentication required`. It now resolves the caller
the way `hangar_call` does, and decides the declared principal with the same
declared-role authorizer: a declared `admin` is served what it is listed and a
declared `viewer` is refused, for example,
`Not authorized to call 'hangar_warm': mcp_servers:lifecycle permission required`.
What the front door lists and what may be called agree for every built-in role.
The front door's listing likewise answers from the declared principal only for
a caller with no request, so an HTTP request never takes it. HTTP and auth off
are unchanged (#1627).
