**core:** a request body that is not JSON, not a JSON object, or lacks a field the
route reads now answers 400 (`invalid_body` or `missing_fields`, as the routes that already checked answer) on the REST routes that parsed it
unguarded -- server create, update and L7 policy, groups, discovery sources, the
auth key and role routes, tool access policies and approval resolution. Each
answered a generic 500 and was logged as an unhandled fault.
