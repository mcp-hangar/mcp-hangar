**core:** `POST /api/admin/tools/{server}/{name}/withdraw` and `/restore` accept
`kind` (`tool`, `prompt` or `resource`) in the JSON body, so a prompt or a
resource can be withdrawn at runtime instead of only from `withdrawn_prompts:` /
`withdrawn_resources:` and a reload. Before, the endpoint reported success for a
prompt name, left the prompt served, and withdrew a same-named tool for that
tenant. An unrecognised `kind` is a 400 and writes nothing; no `kind` still
means a tool. A resource is named by its upstream uri (`demo://doc/1`), and the
name segment now accepts slashes so such a uri reaches the endpoint. The
response JSON gains a `kind` field and the emitted event carries it
