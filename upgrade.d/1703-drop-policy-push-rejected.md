### `PolicyPushRejected` is removed

`from mcp_hangar.domain.events import PolicyPushRejected` now fails with an
`ImportError`. Delete the import and any handler subscribed to it: nothing has
published the event since the `POST /agent/policy` endpoint was retired in
1.5.x, so such a handler has never been called and removing it changes no
behaviour. To observe a refused policy push, subscribe to `AuthorizationDenied`,
which is published when the authorizer refuses the `policy:write` request.

No event store needs migrating. The event was only ever delivered, never
persisted, so no stored stream contains one.
