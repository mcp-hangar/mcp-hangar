**core:** hot-unloading an mcp_server now retires its prompt and resource
policies too, not only its tool policy. Since the policy surface became
kind-keyed, `remove_mcp_server_policy` dropped one kind and left the other two
registered for an id that is free to be loaded again -- so a server taking that
id later would have been governed by its predecessor's prompt/resource rules,
in either direction: a stale `deny_list` restricting a server that never
declared one, or a stale `allow_list` filtering its catalogue. Unloading a
server retires the whole server.
