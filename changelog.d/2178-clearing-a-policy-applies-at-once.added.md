**core:** clearing a tool access policy with `DELETE /api/auth/policies/{scope}/{target_id}`
takes effect at once, on the replica that cleared it and on every running peer
within the event tail's interval. Before, the row was deleted but the policy
held until each replica restarted. What a clear leaves is what a restart
leaves: the configuration file's policy for that scope when the file declares
one, and no policy when it does not. A clear only takes back a policy the
endpoint stored; a policy `hangar_load` set for a server, with no stored row
behind it, is not lifted by a clear.
