**security:** on the front door, an upstream tool named like one of the gateway's
own tools (`hangar_status`, `hangar_whoami`, `hangar_call`, ...) is no longer
projected. Its name was listed twice and a call of it went to the upstream, so
the upstream answered for the gateway and the real tool could not be reached.
The gateway's tool keeps the name, and `flat_tool_name_reserved` is logged naming
the server, as a collision is. Egress mode, where a tool is always named with its
server, is unchanged
