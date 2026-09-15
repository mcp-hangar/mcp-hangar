**core:** a tool call made while a configuration reload swaps the governance
overlays now gets one file's answer. The reload swaps the tool-access policies,
then the withdrawals and pins, and `hangar_call`'s gates, like the front door's
listing and routing, read them one after another. A call in between could
combine the new policy with the previous withdrawals: a reload that moved a
control from `tools.deny_list: [t]` to `tool_projection.withdrawn: [t]` let `t`
run for a moment, which neither file allows. Each call's access, withdrawal,
pins, digest-enforcement modes and approval list are now decided together, as
are the front door's flat map and the re-check after an approval hold. A call
that arrives during the swap waits for it to finish.
