**core:** the `hangar_sources` tool description now lists the `id` field the tool
has returned since 2.5.0. That docstring is what an MCP client reads verbatim to
learn the tool's shape, and it still described the seven pre-2.5.0 fields, so a
client had no way to know the addressable id was there. The description also says
what the id is for: it is the id `/api/discovery/sources/{id}` takes, and a source
declared in `config.yaml` derives it from its `source_type`, so it survives a
restart. No behaviour change -- the returned payload is the same as in 2.5.0
