### a group created again keeps only the members added to it

A group that `POST /api/groups` creates again, after a configuration file
declared it and then dropped it, no longer takes the members the earlier group
of that id had when this replica restarts. Only the members added after the
create come back; each skipped one logs
`recovery_skipped_member_row_of_an_earlier_group`. Add a member it should keep
with `POST /api/groups/{group_id}/members`. A replica whose file still declares
the group keeps every member, as before.

The group and member rows get a `generation` column, added by the schema
migration on both SQLite and PostgreSQL. Rows written before it read as the
first generation, so nothing changes for a group that was never created again.
During a rolling upgrade, a member that an older release adds to a group
created again is written without a generation, and is skipped by this release
at the next restart; add it again once every replica runs this release.
