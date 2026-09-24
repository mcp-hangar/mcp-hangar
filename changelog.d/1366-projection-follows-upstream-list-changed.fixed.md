**core:** an upstream's own `notifications/tools/list_changed` now reaches the
front door's tool projection. The aggregate re-listed the upstream's catalogue,
but the front door lists from the projection registry, which only a start
filled, so a client told to re-list was served the catalogue from the last
start: a tool the upstream added was missing and one it removed was still
listed. The refreshed catalogue is now projected through the same handler a
start uses, before the relay tells clients to re-list, and other servers'
projections and every withdrawal and pin overlay are left alone
