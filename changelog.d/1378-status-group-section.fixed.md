**core:** the `hangar_status` dashboard drew a group in the server table, so a
group's state (`healthy`, `partial`, `inactive`) fell through to `[?]`, and a
server that was starting (state `initializing`) did too. Server states are a
lifecycle and group states are availability computed from the members; they
are two vocabularies and stay two. The dashboard now has a server section
(indicator, id, state, note) and a separate group section (id, state, healthy
members, circuit open or closed). Every server state, `dead` included, has an
indicator, and `initializing` shows as `[STARTING]`. The frame is sized to its
content, so every line has the same width and the right border closes. A name
too long for its column ends in `…` instead of being cut into something that
looks like a different id. In the structured result, `groups[].indicator` now
comes from the group vocabulary (`[HEALTHY]`, `[PARTIAL]`, `[INACTIVE]`,
`[DEGRADED]`) and each group gains `circuit_open`. `hangar_list` and
`hangar_details` are unchanged: they return each vocabulary as it is.
