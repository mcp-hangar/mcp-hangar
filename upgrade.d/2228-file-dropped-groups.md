### a group or member the file dropped no longer comes back after a restart

- A group the configuration file dropped, or a member it no longer names in a
  group, no longer comes back after a restart. Its row is kept, claimed by the
  file, and counted in `GET /api/system` as
  `instance.dormant_file_claimed_rows`. Migration `008_group_row_claims` adds
  the column that records the claim; an older release ignores it.
- A group or member dropped from the file before the upgrade was never
  claimed, and nothing claims it now, so it still comes back at a restart, and
  at every one after it. Remove it after that restart with
  `DELETE /api/groups/{group_id}` or
  `DELETE /api/groups/{group_id}/members/{member_id}`.
- Rolling back a file that took over a group created over the API no longer
  restores that group. Create it again with `POST /api/groups`, which replaces
  the claimed row, and add the members the file named with
  `POST /api/groups/{group_id}/members`. Members added over the API stay in
  their rows and join the group again at the next restart.
