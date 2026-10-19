**core:** the `PolicyPushRejected` domain event is removed. Its only producer
was the `POST /agent/policy` endpoint retired with the Hangar Cloud connector in
1.5.x, and since then nothing has constructed it and nothing has handled it. A
refused policy push is still recorded, as `AuthorizationDenied`. No event store
needs migrating: the endpoint published it through the bus's delivery-only
`publish()`, three weeks before `publish()` started persisting, and an event
naming no aggregate is still delivered only, so no stored stream holds one.
