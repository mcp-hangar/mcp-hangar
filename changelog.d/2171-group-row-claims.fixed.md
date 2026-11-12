**core:** a group the configuration file took over from the group API, and a
member the file named after the API added it, no longer come back after a
restart on a file that dropped them. A reload removed them, but their rows
stayed registrations, so a restart restored them. The claim pass now also
claims the group rows the manager's file declares and the member rows it names
in them, under the same rules as a server's row: standalone at boot and after
each reload, on PostgreSQL only the lease holder, fenced by the lease
generation. A restart skips a claimed row the file in force does not declare,
with a `recovery_skipped_group_row_a_file_claimed` or
`recovery_skipped_member_row_a_file_claimed` warning, and leaves it in place: a
replica whose file still declares the group keeps the members added over REST,
and the group's deny list keeps applying to them. `POST /api/groups` over a
claimed row this replica's file does not declare replaces it instead of
answering "already exists", and adding a member over REST takes the claim off
its row. Migration `008_group_row_claims` adds a nullable `claimed_by_file`
column to both group tables; `instance.dormant_file_claimed_rows` now counts
group and member rows too
