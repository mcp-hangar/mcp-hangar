**core:** a configuration reload no longer drops an L7 egress policy set
through the REST API or the fleet projection. A reload that rebuilds a server,
because a setting the server is built from changed, built the new object from
the file alone -- and no config-file key carries an L7 policy -- so an
unrelated `env` edit dropped the policy while the reload reported success. The
policy is now carried onto the rebuilt server as the commit puts it in force,
and the reload logs `l7_policy_carried_to_rebuilt_mcp_server`. A policy the
file declares for that server would still win. A server the reload keeps is the
same object, and always kept its policy. The per-server log buffer, attached at
bootstrap and likewise absent from the file, is carried the same way, so a
rebuilt server's output still reaches `hangar_logs`.
