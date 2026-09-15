**core:** on the front door, a server that is a member of several groups is now
governed by each of them, as `hangar_call` naming that member already was. The
front door kept one group per member, so the tool policy, withdrawals, pins and
`header_exposure` block of the member's other groups did not apply there, and
which group counted depended on the order of the groups in the file. A tool that
any of the member's groups denies or withdraws is no longer listed for it, and a
call to it is refused. A member of several groups is now routed to itself rather
than through one of its groups. A member of one group is routed and governed as
before. See `UPGRADE.md`.
