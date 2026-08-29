**core:** `remove_group_policy` and `remove_member_policy` removed only the
tool-kind policy, so a prompt or resource policy registered on a group or a
group member outlived its removal (and, for a member, so did its cached
resolution). Both now retire every kind, the way `remove_mcp_server_policy`
already did since #1034. No production path calls either yet
