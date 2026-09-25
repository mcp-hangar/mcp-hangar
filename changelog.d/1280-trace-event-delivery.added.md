**core:** event delivery and persistence now say which handler and which append.
Each handler's run is a `hangar.event.handled` event on its
`event.publish.<Type>` span, naming the handler (`hangar.event.handler.name`,
its bounded `__qualname__`), its kind (`hangar.event.handler.kind`) and its
outcome (`hangar.event.handler.outcome`, with `error.type` on a failure), so one
failing handler among successful peers is identifiable. The publish span also
carries `hangar.event.id`, `hangar.event.producer` and
`hangar.event.delivery_mode` (`live`, `tailed`, `recovered`), and
`event_store.append` carries `hangar.event_store.append.outcome` (`appended`,
`conflict`, `failed`). When an append fails, its span now ends before the
unpersisted delivery instead of containing it, so the append's duration is the
store's alone and the delivery is its sibling, as on success. Still one span
per event, not one per handler; no payload is recorded, and existing attributes
are unchanged.
